"""Local door OCR and explicitly supplied clock schedule, independent of matching."""
from datetime import datetime, timezone, timedelta
from functools import lru_cache
import math
import re
import unicodedata
from threading import Lock

SHANGHAI = timezone(timedelta(hours=8))
# User-supplied timetable screenshot, 2026-10-01. Intervals are half-open.
PERIODS = (('08:00','08:45'),('08:55','09:40'),('10:00','10:45'),('10:55','11:40'),
           ('12:10','12:55'),('13:05','13:50'),('14:00','14:45'),('14:55','15:40'),
           ('15:50','16:35'),('16:55','17:40'),('17:50','18:35'),('19:20','20:05'),
           ('20:15','21:00'),('21:10','21:55'))
_ocr_lock = Lock()


def ensure_capture_schema():
    from sqlalchemy import inspect, text
    from app.models import db, LectureSiteCapture
    LectureSiteCapture.__table__.create(db.engine,checkfirst=True)
    columns={column['name'] for column in inspect(db.engine).get_columns('lecture_site_captures')}
    if 'draft_json' not in columns:
        with db.engine.begin() as connection:
            connection.execute(text("ALTER TABLE lecture_site_captures ADD COLUMN draft_json TEXT NOT NULL DEFAULT '{}'"))
    if 'is_archived' not in columns:
        with db.engine.begin() as connection:
            connection.execute(text('ALTER TABLE lecture_site_captures ADD COLUMN is_archived BOOLEAN NOT NULL DEFAULT FALSE'))


def now_at_site():
    return datetime.now(SHANGHAI)


def infer_period(clock):
    return next((i for i,(start,end) in enumerate(PERIODS,1) if start <= clock < end),None)


def _room_text(text):
    """Only accept a complete room line, never digits buried in other content."""
    if not isinstance(text,str):return None
    normalized=unicodedata.normalize('NFKC',text).strip()
    if normalized.startswith('(') and normalized.endswith(')'):
        normalized=normalized[1:-1].strip()
    label=r'(?:教室|講義室|classroom|room)'
    match=re.fullmatch(rf'(?:{label}\s*[:：]?\s*)?(\d{{3,4}})(?:\s*{label})?',normalized,re.IGNORECASE)
    return match.group(1) if match else None


def room_numbers(lines):
    found = {}
    for text, score in lines:
        if type(score) not in (float,int) or not math.isfinite(score) or not 0<=score<=1:
            continue
        # Avoid pulling a door number from phone numbers, dates or other long runs.
        value=_room_text(text)
        if value and score >= .65:
            found[value]=max(score,found.get(value,0))
    ranked=sorted(found,key=lambda v:(-found[v],v))
    certain=len(ranked)==1 and found[ranked[0]] >= .9
    return {'room_number':ranked[0] if certain else None,
            'status':'recognized' if certain else ('needs_check' if ranked else 'unrecognized'),
            'alternatives':ranked[:8]}


@lru_cache(maxsize=1)
def _engine():
    from rapidocr_onnxruntime import RapidOCR
    return RapidOCR(intra_op_num_threads=2,inter_op_num_threads=2)


def recognize_door(path, *, region=None):
    import numpy as np
    from PIL import Image, ImageOps
    from app.services.site_context_model import normalize_hypotheses
    from app.services.door_regions import rectify_region,visual_sign_regions,normalized_region
    image = ImageOps.exif_transpose(Image.open(path)).convert('RGB')
    if region is not None:
        if not isinstance(region,dict) or set(region)!={'x','y','width','height'}:
            raise ValueError('请框选门牌所在区域。')
        x,y,w,h=(region[k] for k in ('x','y','width','height'))
        if (any(type(v) not in (int,float) or not math.isfinite(v) for v in (x,y,w,h))
                or min(x,y)<0 or min(w,h)<=0 or x+w>1.000001 or y+h>1.000001
                or w*image.width<12 or h*image.height<12):
            raise ValueError('框选区域过小或超出照片，请重新框选。')
        image=image.crop((round(x*image.width),round(y*image.height),
                          round((x+w)*image.width),round((y+h)*image.height)))
    array = np.asarray(image)[:, :, ::-1].copy()
    hypotheses=[];auto_regions=[]
    with _ocr_lock:
        engine=_engine()
        def read_region(crop,method):
            # Read both directions from the actual pixels. Numeric-only signs
            # can fool the text classifier (6/9 and reversed digit order).
            if crop.shape[0]>crop.shape[1]*1.5:
                crop=np.rot90(crop).copy()
            for reverse in (False,True):
                oriented=np.rot90(crop,2).copy() if reverse else crop
                try:
                    lines,_=engine(oriented,use_det=False,use_cls=False)
                    for text,score in lines or []:
                        value=_room_text(str(text))
                        if value:
                            hypotheses.append({'value':value,'score':float(score),
                                'method':method+'_rot180' if reverse else method})
                except Exception:
                    if region is not None and not reverse:raise
                    continue  # A failed recheck must not discard a good read.
        if region is not None:
            read_region(array,'manual_region')
        else:
            # Respect EXIF-corrected image orientation on the first reading;
            # orientation ambiguity is examined explicitly below, not hidden
            # behind a classifier's confidently inverted numeric result.
            result,_=engine(array,use_cls=False)
            regions=0
            for item in result or []:
                box,text,score=item
                normalized=unicodedata.normalize('NFKC',str(text)).strip()
                value=_room_text(normalized)
                if value:
                    hypotheses.append({'value':value,'score':float(score),'method':'whole'})
                # Re-read a bounded set of short lines; OCR must read letters
                # such as O/0 again, rather than replacing them with digits.
                if not 2<=len(normalized)<=8:continue
                if regions>=4:continue
                points=np.asarray(box,dtype=float)
                if points.shape!=(4,2) or not np.isfinite(points).all():continue
                crop=rectify_region(array,points)
                if crop is None:continue
                regions+=1
                auto_regions.append(normalized_region(points,image.width,image.height,'text_region'))
                read_region(crop,'region_single_line')
            # A bounded visual fallback remains available for missed text.
            if not any(h['score']>=.85 for h in hypotheses):
                for points in visual_sign_regions(array,limit=4):
                    crop=rectify_region(array,points)
                    if crop is None:continue
                    auto_regions.append(normalized_region(points,image.width,image.height,'visual_region'))
                    read_region(crop,'visual_region')
    hypotheses=normalize_hypotheses(hypotheses)
    # The extra inverted pass is a robustness check, not an equal new source.
    # Retain its weak readings for review, but only an existing trustworthy
    # threshold (.85) can make that pass veto a strong direct reading.
    parsed=room_numbers([(h['value'],h['score']) for h in hypotheses
                        if not h['method'].endswith('_rot180') or h['score']>=.85])
    parsed['hypotheses']=hypotheses
    # The persisted public response and guide reload expose these same bounded
    # hypotheses; do not quietly drop weak alternatives only before saving.
    parsed['alternatives']=[h['value'] for h in hypotheses]
    parsed['regions']=auto_regions
    return parsed


def normalize_location(value):
    if not isinstance(value,dict):return {}
    allowed={'latitude':(-90,90),'longitude':(-180,180),'accuracy':(0,100000)}
    result={}
    for key,(lo,hi) in allowed.items():
        number=value.get(key)
        if type(number) not in (float,int) or not math.isfinite(number) or not lo <= number <= hi:return {}
        if key=='accuracy' and number<=0:return {}
        result[key]=number
    return result


def decode_ocr_evidence(raw):
    import json
    from app.services.site_context_model import normalize_hypotheses
    try:value=json.loads(raw) if isinstance(raw,str) else raw
    except (ValueError,TypeError):value=[]
    hypotheses=normalize_hypotheses(value.get('hypotheses',[]) if isinstance(value,dict) else value if isinstance(value,list) else [])
    return {'hypotheses':hypotheses,'alternatives':[h['value'] for h in hypotheses]}


def normalize_location_evidence(value, *, now_ms=None):
    result=normalize_location(value)
    if not result:return {}
    if now_ms is None:now_ms=now_at_site().timestamp()*1000
    samples=[]
    raw=value.get('samples',[]) if isinstance(value.get('samples',[]),list) else []
    for item in raw[-64:]:
        point=normalize_location(item)
        stamp=item.get('timestamp') if isinstance(item,dict) else None
        if not point or type(stamp) not in (float,int) or not math.isfinite(stamp) or not -2000<=now_ms-stamp<=30000:continue
        samples.append({**point,'timestamp':stamp})
    if samples:result['samples']=sorted(samples,key=lambda p:p['timestamp'])[-12:]
    return result


def merge_location_evidence(existing,incoming):
    if not normalize_location(incoming):return existing
    best=incoming if not normalize_location(existing) or incoming['accuracy']<existing['accuracy'] else existing
    result=normalize_location(best)
    unique={}
    for point in existing.get('samples',[])+incoming.get('samples',[]):
        if not isinstance(point,dict):continue
        key=tuple(point.get(k) for k in ('timestamp','latitude','longitude','accuracy'))
        unique[key]=point
    if unique:result['samples']=sorted(unique.values(),key=lambda p:p.get('timestamp',0))[-12:]
    return result


def clock_evidence(received_at, captured_at):
    try:
        received=datetime.fromisoformat(received_at.replace('Z','+00:00'))
        if received.tzinfo is None:received=received.replace(tzinfo=SHANGHAI)
        received=received.astimezone(SHANGHAI)
        if not captured_at:return {'clock':received.strftime('%H:%M'),'reliability':.6}
        captured=datetime.fromisoformat(captured_at.replace('Z','+00:00'))
        if captured.tzinfo is None:return {'clock':None,'reliability':0}
        captured=captured.astimezone(SHANGHAI)
        if captured.date()!=received.date() or not -5<=(received-captured).total_seconds()<=600:
            return {'clock':None,'reliability':0}
        return {'clock':captured.strftime('%H:%M'),'reliability':1}
    except (ValueError,TypeError,AttributeError):return {'clock':None,'reliability':0}
