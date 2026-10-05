"""Dedicated loopback trial on a copied debug DB with a separate browser cookie."""
import argparse
from datetime import datetime,timedelta
import json
import os
from pathlib import Path
import shutil
import sqlite3
import sys
import uuid

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
OUTPUT=ROOT/'output/2026-10-02-site-context-fusion'
RUNTIME=OUTPUT/'runtime'


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port',type=int,default=5100)
    parser.add_argument('--prepare',action='store_true')
    parser.add_argument('--scenario',choices=('biased','no_location','ocr_unavailable'),default='biased')
    args=parser.parse_args();RUNTIME.mkdir(parents=True,exist_ok=True)
    target=RUNTIME/'lecture_forms-debug.db'
    if not target.exists():
        original=ROOT/'output/2026-10-01-single-schedule/launcher-runtime/lecture_forms-debug.db'
        with sqlite3.connect(original.as_uri()+'?mode=ro',uri=True) as src,sqlite3.connect(target) as dst:src.backup(dst)
    os.environ.update(LOCAL_DEBUG_MODE='1',INSTANCE_DIR=str(RUNTIME),SQLITE_DB_PATH=str(target),
                      UPLOAD_FOLDER=str(RUNTIME/'uploads'),LECTURE_CAPTURE_ENABLED='true',
                      SECRET_KEY='site-context-isolated-demo-'+str(args.port))
    from app.app import app,init_database
    app.config['SESSION_COOKIE_NAME']='site_context_fusion_demo'
    init_database()
    if args.prepare:
        prepare_fixture(app,args.scenario)
        return
    print(f'Fusion trial: http://127.0.0.1:{args.port}/user/submit_form',flush=True)
    app.run(host='127.0.0.1',port=args.port,debug=False,use_reloader=False)


def prepare_fixture(app,scenario='biased'):
    from app.models import db,LectureSiteCapture,LectureFormDraft,User
    from app.services.lecture_site_capture import ensure_capture_schema,PERIODS
    from app.services.listening_assistant import ListeningAssistantService
    from app.services.academic_term import get_current_teaching_semester
    from app.services.campus_buildings import load_buildings,representative_point
    from app.blueprints.user.forms import LECTURE_FORM_DRAFT_KEY
    from werkzeug.security import generate_password_hash
    with app.app_context():
        ensure_capture_schema();service=ListeningAssistantService(semester=get_current_teaching_semester())
        chosen=None
        for day in range(6,13):
            rows=service.search_partial({'date':f'2026-04-{day:02}'},candidate_limit=None).candidates
            chosen=next((c for c in rows if c.room=='8-609' and c.period[1]<=14 and not c.conflicts),None)
            if chosen:break
        if not chosen:raise RuntimeError('No valid primary 8-609 course in replay week')
        user=User.query.filter_by(student_id='context_demo').first()
        if not user:
            user=User(number='FUSION-DEMO',student_id='context_demo',name='联合模型模拟验证',
                      department='技术部',gender='-',grade='-',college='-',major='-',dormitory='-',
                      phone='-',qq='-',password_hash=generate_password_hash('fusion-demo-2026'),
                      role='信息员',group='test',is_active=True)
            db.session.add(user);db.session.flush()
        footprint=next(f for f in load_buildings()['features'] if f['properties']['code']=='25')
        lon,lat=representative_point(footprint)
        clock=PERIODS[min(chosen.period[0]+1,chosen.period[1])-1][0]
        instant=datetime.fromisoformat(chosen.lecture_date.isoformat()+'T'+clock+':00+08:00')+timedelta(minutes=5)
        photos=Path(app.config['UPLOAD_FOLDER'])/'lecture_site';photos.mkdir(parents=True,exist_ok=True)
        key='fusion-'+uuid.uuid4().hex+'.jpg';shutil.copy2(OUTPUT/'ocr-input-1.jpg',photos/key)
        ocr=json.loads((OUTPUT/'real-photo-ocr.json').read_text(encoding='utf-8'))[0]['result']
        if scenario=='ocr_unavailable':ocr={'status':'unavailable','hypotheses':[]}
        location={'latitude':lat,'longitude':lon,'accuracy':35,'fixture_kind':'simulated_position'} if scenario=='biased' else {}
        record=LectureSiteCapture(user_id=user.id,photo_key=key,received_at=(instant+timedelta(seconds=15)).isoformat(),
            client_captured_at=instant.isoformat(),location_json=json.dumps(location),
            room_number=None,ocr_status=ocr['status'],ocr_alternatives_json=json.dumps({'hypotheses':ocr['hypotheses']}))
        db.session.add(record);db.session.flush()
        payload={'site_capture_id':record.id,'lecture_date':chosen.lecture_date.isoformat(),'listener_number':user.number}
        record.draft_json=json.dumps(payload,ensure_ascii=False)
        draft=LectureFormDraft.query.filter_by(user_id=user.id,draft_key=LECTURE_FORM_DRAFT_KEY).first()
        if not draft:draft=LectureFormDraft(user_id=user.id,draft_key=LECTURE_FORM_DRAFT_KEY);db.session.add(draft)
        draft.payload_json=record.draft_json;db.session.commit()
        manifest={'scope':'Isolated copy only; real door photo, simulated location/time, actual configured primary timetable',
                  'scenario':scenario,
                  'capture_id':record.id,'target':chosen.to_public_dict(),'received_at':record.received_at,
                  'login_account':'context_demo','login_password':'fusion-demo-2026'}
        (OUTPUT/'browser-fixture.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
        print('Prepared isolated capture',record.id,'date',chosen.lecture_date,'period',chosen.period,'room',chosen.room)


if __name__=='__main__':main()
