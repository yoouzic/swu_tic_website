"""Photo-first site records; OCR failure never discards a successfully stored photo."""
import io
import json
import re
import uuid
from pathlib import Path
from flask import current_app, jsonify, request, send_file, url_for, abort
from sqlalchemy import case, or_
from app.models import db, LectureFormDraft, LectureSiteCapture, ListeningAssistantScheduleEntry
from app.security import submission_required
from app.services import lecture_site_capture as capture_service
from app.services.academic_term import get_current_teaching_semester
from app.services.listening_assistant import ListeningAssistantService
from app.services.listening_assistant_contracts import AssistantQuery, normalize_room
from app.services.listening_assistant_evidence import revalidate_selection, AssistantSelectionError
from app.services.listening_assistant_schedule import ScheduleSourceUnavailable
from app.services.schedule_snapshots import resolve_current_schedule_snapshot
from app.services.schedule_availability import current_schedule_availability
from app.services.lecture_form_draft_concurrency import LectureFormDraftConflict, save_lecture_form_draft_atomic
from . import user_bp
from .forms import _load_lecture_form_draft, _parse_draft_payload, LECTURE_FORM_DRAFT_KEY
from .listening_assistant import _assistant_login_required


def _enabled():
    if not current_app.config.get('LECTURE_CAPTURE_ENABLED'):abort(404)


def _folder():
    return Path(current_app.config.get('LECTURE_CAPTURE_FOLDER') or Path(current_app.config['UPLOAD_FOLDER'])/'lecture_site').resolve()


def _owned(capture_id,user):
    return LectureSiteCapture.query.filter_by(id=capture_id,user_id=user.id).first_or_404()


def _draft(user):
    from app.models import LectureFormDraft
    draft=_load_lecture_form_draft(user.id)
    if not draft:
        # Keep a new draft transient until its atomic save handles creation races.
        draft=LectureFormDraft(user_id=user.id,draft_key=LECTURE_FORM_DRAFT_KEY)
    return draft,_parse_draft_payload(draft)


def _public(record):
    availability=current_schedule_availability()
    try:
        stored=json.loads(record.draft_json or '{}')
        assistant=stored.get('assistant',{}) if isinstance(stored,dict) else {}
    except (TypeError,ValueError):
        assistant={}
    confirmed=(isinstance(assistant,dict) and availability['candidates_ready']
               and bool(record.confirmed_candidate_id)
               and record.confirmed_candidate_id.startswith(f"primary:{availability['batch_id']}:")
               and assistant.get('candidate_id')==record.confirmed_candidate_id
               and assistant.get('stage') in {'confirmed','done','complete'}
               and assistant.get('source_kind')=='primary'
               and assistant.get('semester')==availability['semester']
               and str(assistant.get('source_batch_id'))==str(availability['batch_id']))
    return {'id':record.id,'photo_url':url_for('user.site_capture_photo',capture_id=record.id),
            'received_at':record.received_at,'record_date':record.received_at[:10],
            'room_number':record.room_number,'building':record.building,
            'period_number':record.period_number,'ocr_status':record.ocr_status,
            'alternatives':capture_service.decode_ocr_evidence(record.ocr_alternatives_json)['alternatives'],
            'has_location':bool(json.loads(record.location_json)),
            'location_accuracy':json.loads(record.location_json).get('accuracy'),
            'course_confirmed':bool(confirmed),
            'submitted':bool(record.form_id)}


def _query(record):
    from datetime import date
    if not record.building or not record.room_number:raise ValueError('请先确认楼栋和门牌号。')
    return AssistantQuery(lecture_date=date.fromisoformat(record.received_at[:10]),room=normalize_room(f'{record.building}-{record.room_number}'))


def _schedule_building_codes():
    buildings=set()
    try:
        snapshot=resolve_current_schedule_snapshot(get_current_teaching_semester(),include_rows=False)
        if snapshot.batch:
            rows=db.session.query(ListeningAssistantScheduleEntry.location_normalized).filter_by(batch_id=snapshot.batch.id).distinct().all()
            for (room,) in rows:
                match=re.fullmatch(r'([\w\u4e00-\u9fff]+)-(\d{3,4})',room or '')
                if match:buildings.add(match.group(1))
    except ScheduleSourceUnavailable:pass
    return sorted(buildings,key=lambda v:(not v.isdigit(),int(v) if v.isdigit() else 0,v))


@user_bp.route('/api/site-capture/settings')
@_assistant_login_required
def site_capture_settings(user):
    _enabled()
    return jsonify(success=True,data={'buildings':_schedule_building_codes()})


@user_bp.route('/api/site-capture/<int:capture_id>/map')
@_assistant_login_required
def site_capture_map(user,capture_id):
    from app.services.campus_buildings import load_buildings,nearby_buildings
    _enabled();record=_owned(capture_id,user)
    location=json.loads(record.location_json);codes=_schedule_building_codes()
    data=load_buildings()
    response=jsonify(success=True,data={'coordinate_system':'WGS84','buildings':data,
        'location':capture_service.normalize_location(location),
        'nearby':nearby_buildings(location,allowed_buildings=codes or None),
        'schedule_buildings':codes,'requires_confirmation':True})
    response.headers['Cache-Control']='private, no-store'
    return response


@user_bp.route('/api/site-capture',methods=['POST'])
@submission_required(api=True)
@_assistant_login_required
def site_capture_upload(user):
    _enabled()
    draft,payload=_draft(user)
    replacing = request.form.get('replace_capture_id')
    existing_id=payload.get('site_capture_id')
    old=_owned(existing_id,user) if existing_id else None
    new_record=request.form.get('new_record')=='1'
    if existing_id and not new_record and (str(existing_id)!=replacing or old.form_id):
        db.session.rollback()
        return jsonify(success=False,message='本次听课已有照片，请先完成或新建表单。'),409
    if new_record and old:
        previous_payload=dict(payload)
        payload={key:'' for key,value in payload.items() if isinstance(value,(str,int,float,bool))}
        payload.pop('site_capture_id',None)
        payload['listener_number']=user.number
    photo=request.files.get('photo')
    if not photo:return jsonify(success=False,message='请先拍摄门牌。'),400
    raw=photo.stream.read(6*1024*1024+1)
    if len(raw)>6*1024*1024:return jsonify(success=False,message='照片过大，请靠近门牌重新拍摄。'),413
    try:
        from PIL import Image, ImageOps
        image=Image.open(io.BytesIO(raw))
        if image.format not in {'JPEG','PNG','WEBP'} or image.width*image.height>20_000_000:raise ValueError('image dimensions')
        image=ImageOps.exif_transpose(image).convert('RGB');image.thumbnail((1600,1600))
    except Exception:
        db.session.rollback()
        return jsonify(success=False,message='照片无法读取，请重新拍摄。'),400
    received=capture_service.now_at_site()
    try:location=capture_service.normalize_location_evidence(json.loads(request.form.get('location') or '{}'))
    except (ValueError,TypeError):location={}
    folder=_folder();folder.mkdir(parents=True,exist_ok=True)
    key=f'{uuid.uuid4().hex}.jpg';path=folder/key
    image.save(path,'JPEG',quality=88)
    # Commit evidence and draft before OCR. A failed OCR never rolls back the photo.
    record=LectureSiteCapture(user_id=user.id,photo_key=key,received_at=received.isoformat(),
        location_json=json.dumps(location),period_number=capture_service.infer_period(received.strftime('%H:%M')))
    client_time=(request.form.get('captured_at') or '')[:40]
    try:
        if client_time:
            from datetime import datetime
            record.client_captured_at=datetime.fromisoformat(client_time.replace('Z','+00:00')).isoformat()
    except ValueError:pass
    try:
        db.session.add(record);db.session.flush()
        payload['site_capture_id']=record.id
        payload['lecture_date']=record.received_at[:10]
        payload.pop('assistant',None)
        for field in ('teacher_name','teacher_college','course_title','student_grade_class','lecture_location','start_period','end_period','class_period'):
            payload[field]=''
        save_lecture_form_draft_atomic(user.id,LECTURE_FORM_DRAFT_KEY,draft,payload)
        if old:
            if new_record:old.draft_json=json.dumps(previous_payload,ensure_ascii=False)
            else:old.is_archived=True
        record.draft_json=draft.payload_json
        db.session.commit()
    except LectureFormDraftConflict:
        db.session.rollback();path.unlink(missing_ok=True)
        return jsonify(success=False,code='draft_conflict',message='听课草稿已更新，请保留当前输入并刷新后重新拍摄。'),409
    except Exception:
        db.session.rollback();path.unlink(missing_ok=True);raise
    try:ocr=capture_service.recognize_door(path)
    except Exception:
        current_app.logger.exception('Door OCR unavailable for capture %s',record.id)
        ocr={'room_number':None,'status':'unavailable','alternatives':[]}
    # The upload may outlive the browser timeout. A recovered draft can already
    # have a human classroom, confirmed course, submitted form, or newer photo.
    # Compare persisted state in the UPDATE itself; a refreshed ORM object alone
    # would still allow another request to change it before this commit.
    capture_id=record.id
    db.session.query(LectureSiteCapture).filter(
        LectureSiteCapture.id==capture_id,
        LectureSiteCapture.form_id.is_(None),
        LectureSiteCapture.is_archived.is_(False),
        LectureSiteCapture.confirmed_candidate_id.is_(None),
    ).update({
        LectureSiteCapture.room_number:case(
            (or_(LectureSiteCapture.building.is_(None),LectureSiteCapture.building==''),ocr['room_number']),
            else_=LectureSiteCapture.room_number),
        LectureSiteCapture.ocr_status:ocr['status'],
        LectureSiteCapture.ocr_alternatives_json:json.dumps(
            {'hypotheses':ocr['hypotheses']} if 'hypotheses' in ocr else ocr['alternatives']),
    },synchronize_session=False)
    db.session.commit()
    db.session.refresh(record)
    return jsonify(success=True,data=_public(record)),201


@user_bp.route('/api/site-capture/<int:capture_id>',methods=['GET','PATCH'])
@submission_required(api=True, methods=('PATCH',))
@_assistant_login_required
def site_capture_context(user,capture_id):
    _enabled();record=_owned(capture_id,user)
    if request.method=='GET':return jsonify(success=True,data=_public(record))
    if record.form_id:return jsonify(success=False,message='这次记录已提交，请新建听课记录。'),409
    body=request.get_json(silent=True) or {}
    if not isinstance(body,dict):return jsonify(success=False,message='请求格式错误。'),400
    building=str(body.get('building') or '').strip();number=str(body.get('room_number') or '').strip()
    if not re.fullmatch(r'[\w\u4e00-\u9fff]{1,40}',building) or not re.fullmatch(r'[A-Za-z]?\d{3,4}',number):
        return jsonify(success=False,message='请确认楼栋和三至四位门牌号。'),400
    draft,payload=_draft(user)
    if payload.get('site_capture_id')!=record.id:return jsonify(success=False,message='请先打开这次听课的草稿。'),409
    availability=current_schedule_availability()
    changed=(record.building,record.room_number)!=(building,number)
    record.building=building;record.room_number=number
    if changed or not availability['candidates_ready']:
        record.confirmed_candidate_id=None;payload.pop('assistant',None)
        if changed and availability['candidates_ready']:
            for field in ('teacher_name','teacher_college','course_title','student_grade_class','start_period','end_period','class_period'):
                payload[field]=''
    query=_query(record);payload['lecture_location']=query.room;payload['lecture_date']=record.received_at[:10]
    try:
        save_lecture_form_draft_atomic(user.id,LECTURE_FORM_DRAFT_KEY,draft,payload)
        record.draft_json=draft.payload_json;db.session.commit()
    except LectureFormDraftConflict:
        db.session.rollback()
        return jsonify(success=False,code='draft_conflict',message='听课草稿已更新，请保留当前输入并刷新后重新核对教室。'),409
    except Exception:
        db.session.rollback();raise
    if not availability['candidates_ready']:
        return jsonify(success=True,data=_public(record),candidates=[],
                       message='教室已确认，请手动填写课程信息。')
    try:
        result=ListeningAssistantService(semester=get_current_teaching_semester()).search(query)
        candidates=sorted(result.candidates,key=lambda c:(not(record.period_number and c.period[0]<=record.period_number<=c.period[1]),c.period,c.course_title,c.candidate_id))
        public=[c.to_public_dict() for c in candidates]
        message='' if public else '现场记录已保存，暂未找到课程，可稍后补充。'
    except (ScheduleSourceUnavailable,ValueError):
        public=[];message='现场记录已保存，课程信息可稍后补充。'
    return jsonify(success=True,data=_public(record),candidates=public,message=message)


@user_bp.route('/api/site-capture/<int:capture_id>/photo')
@_assistant_login_required
def site_capture_photo(user,capture_id):
    _enabled();record=_owned(capture_id,user)
    response=send_file(_folder()/record.photo_key,mimetype='image/jpeg',conditional=True)
    response.headers['Cache-Control']='private, no-store';return response


@user_bp.route('/api/site-capture/<int:capture_id>/ocr-region',methods=['POST'])
@submission_required(api=True)
@_assistant_login_required
def site_capture_ocr_region(user,capture_id):
    from app.services.site_context_model import normalize_hypotheses
    _enabled();record=_owned(capture_id,user)
    if record.form_id or record.is_archived or record.confirmed_candidate_id:
        return jsonify(success=False,message='请先修改已确认的教室，或新建记录后再识别。'),409
    body=request.get_json(silent=True)
    if not isinstance(body,dict) or not isinstance(body.get('region'),dict):
        return jsonify(success=False,message='请框选门牌所在区域。'),400
    try:
        ocr=capture_service.recognize_door(_folder()/record.photo_key,region=body['region'])
    except ValueError as error:
        return jsonify(success=False,message=str(error)),400
    except Exception:
        current_app.logger.exception('Region OCR unavailable for capture %s',record.id)
        return jsonify(success=False,message='识别暂不可用，原照片已保留，可手填门牌或重拍。'),503
    # OCR can be slow; recheck persisted state before updating machine evidence.
    db.session.refresh(record)
    if record.form_id or record.is_archived or record.confirmed_candidate_id:
        return jsonify(success=False,message='记录已更新，请重新打开。'),409
    if not ocr.get('hypotheses'):
        return jsonify(success=True,data=_public(record),message='仍未读出门牌，请靠近重拍或手动输入。')
    old=capture_service.decode_ocr_evidence(record.ocr_alternatives_json)['hypotheses']
    hypotheses=normalize_hypotheses(old+ocr['hypotheses'])
    record.ocr_alternatives_json=json.dumps({'hypotheses':hypotheses,'manual_region':body['region']})
    record.ocr_status='needs_check'
    db.session.commit()
    return jsonify(success=True,data=_public(record),message='已重新识别，请核对门牌选项。')


@user_bp.route('/api/site-capture/<int:capture_id>/location',methods=['PATCH'])
@submission_required(api=True)
@_assistant_login_required
def site_capture_location(user,capture_id):
    from datetime import datetime
    _enabled();record=_owned(capture_id,user)
    age=(capture_service.now_at_site()-datetime.fromisoformat(record.received_at)).total_seconds()
    if record.form_id or record.is_archived or not 0<=age<=30:
        return jsonify(success=False,message='本次现场定位采集已结束。'),409
    body=request.get_json(silent=True)
    location=capture_service.normalize_location_evidence(body.get('location') if isinstance(body,dict) else None)
    if not location:return jsonify(success=False,message='定位数据无效。'),400
    existing=json.loads(record.location_json)
    merged=capture_service.merge_location_evidence(existing,location)
    if merged!=existing:
        record.location_json=json.dumps(merged);db.session.commit()
    return jsonify(success=True,data=_public(record))


@user_bp.route('/api/site-capture/<int:capture_id>/suggestions',methods=['GET','POST'])
@_assistant_login_required
def site_capture_suggestions(user,capture_id):
    from app.services.site_context_model import rank_site_candidates, validate_facts, FACT_KINDS
    _enabled();record=_owned(capture_id,user)
    availability=current_schedule_availability()
    if not availability['candidates_ready']:
        return jsonify(success=False,message=availability['candidate_message'],
                       data={'source_unavailable':True,'status':availability['status']}),503
    if record.is_archived:return jsonify(success=False,message='这次照片已被替换，请打开新的记录。'),409
    body=request.get_json(silent=True) if request.method=='POST' else {}
    if not isinstance(body,dict):return jsonify(success=False,message='请求格式错误。'),400
    try:
        facts=validate_facts(body.get('facts',{}))
        asked=body.get('asked',[])
        question_count=body.get('question_count',0)
        if type(question_count) is not int or not 0<=question_count<=2:
            raise ValueError('补问次数格式无效。')
        if not isinstance(asked,list) or len(asked)>4 or any(k not in FACT_KINDS for k in asked):
            raise ValueError('提问记录格式无效。')
    except ValueError as error:return jsonify(success=False,message=str(error)),400
    if request.method=='GET' and record.building:
        facts.setdefault('building',record.building)
        if record.room_number:facts.setdefault('room_number',record.room_number)
    clock=capture_service.clock_evidence(record.received_at,record.client_captured_at)
    kwargs={'location':json.loads(record.location_json),
            'door_hypotheses':capture_service.decode_ocr_evidence(record.ocr_alternatives_json)['hypotheses'],
            'clock':clock['clock'],'time_reliability':clock['reliability'],
            'confirmed_facts':facts,'asked':asked,'question_count':question_count}
    try:
        result=ListeningAssistantService(semester=get_current_teaching_semester()).search_partial(
            {'date':record.received_at[:10]},candidate_limit=None)
        ranking=rank_site_candidates(result.candidates,**kwargs)
        ranking['source_unavailable']=False
    except ScheduleSourceUnavailable:
        ranking=rank_site_candidates([],**kwargs)
        ranking['source_unavailable']=True
    # Human-readable reasons are displayed, never probability-like numerical scores.
    for candidate in ranking['candidates']:
        candidate.pop('match_components',None);candidate.pop('match_score',None)
        candidate.pop('site_support',None)
    return jsonify(success=True,data=ranking)


@user_bp.route('/api/site-capture/<int:capture_id>/confirm',methods=['POST'])
@submission_required(api=True)
@_assistant_login_required
def site_capture_confirm(user,capture_id):
    _enabled();record=_owned(capture_id,user)
    availability=current_schedule_availability()
    if not availability['candidates_ready']:
        return jsonify(success=False,message=availability['candidate_message'],
                       data={'source_unavailable':True,'status':availability['status']}),503
    if record.form_id:return jsonify(success=False,message='这次记录已提交。'),409
    body=request.get_json(silent=True) or {};draft,payload=_draft(user)
    if not isinstance(body,dict):return jsonify(success=False,message='请求格式错误。'),400
    if payload.get('site_capture_id')!=record.id:return jsonify(success=False,message='请打开这次记录的草稿。'),409
    try:
        query=_query(record)
        selection={'stage':'confirmed','source_kind':'primary','semester':get_current_teaching_semester(),
                   'candidate_id':body.get('candidate_id'),'query':{'lecture_date':record.received_at[:10],'room':query.room},'overrides':{},'template_version':'site-capture-v1'}
        selected=revalidate_selection(user,selection)
    except (AssistantSelectionError,ScheduleSourceUnavailable,ValueError):
        db.session.rollback();return jsonify(success=False,message='课程已变化，请重新查找并确认。'),409
    record.confirmed_candidate_id=selected.candidate.candidate_id
    selection['source_batch_id']=selected.source_batch_id
    selection['assistant_filled_fields']=['lecture_date','lecture_location','teacher_name','teacher_college','course_title','student_grade_class']
    selection['assistant_filled_groups']=['period']
    payload['assistant']=selection;payload.update(selected.field_snapshot)
    payload['start_period']=str(selected.candidate.period[0]);payload['end_period']=str(selected.candidate.period[1])
    try:
        save_lecture_form_draft_atomic(user.id,LECTURE_FORM_DRAFT_KEY,draft,payload)
        record.draft_json=draft.payload_json;db.session.commit()
    except LectureFormDraftConflict:
        db.session.rollback()
        return jsonify(success=False,code='draft_conflict',message='听课草稿已更新，请保留当前输入并刷新后重新确认课程。'),409
    except Exception:
        db.session.rollback();raise
    return jsonify(success=True,data=_public(record),draft=payload)


@user_bp.route('/api/site-capture/records')
@_assistant_login_required
def site_capture_records(user):
    _enabled()
    records=LectureSiteCapture.query.filter_by(user_id=user.id,form_id=None,is_archived=False).order_by(LectureSiteCapture.id.desc()).all()
    public=[]
    for record in records:
        row=_public(record)
        row['course_title']=_parse_draft_payload(LectureFormDraft(payload_json=record.draft_json)).get('course_title') or '待确认课程'
        public.append(row)
    return jsonify(success=True,data=public)


@user_bp.route('/api/site-capture/<int:capture_id>/resume',methods=['POST'])
@submission_required(api=True)
@_assistant_login_required
def site_capture_resume(user,capture_id):
    _enabled();record=_owned(capture_id,user)
    if record.form_id or record.is_archived:return jsonify(success=False,message='这次记录已提交或替换。'),409
    draft,current=_draft(user)
    old_id=current.get('site_capture_id')
    if old_id:
        old=_owned(old_id,user)
    # Per-photo drafts can sleep through an assistant schema change. Reuse the
    # ordinary draft GET parser so old assistant state cannot hide manual work.
    payload=_parse_draft_payload(LectureFormDraft(payload_json=record.draft_json))
    payload['site_capture_id']=record.id
    if record.confirmed_candidate_id and (payload.get('assistant') or {}).get('candidate_id')!=record.confirmed_candidate_id:
        record.confirmed_candidate_id=None
    try:
        save_lecture_form_draft_atomic(user.id,LECTURE_FORM_DRAFT_KEY,draft,payload)
        if old_id:old.draft_json=json.dumps(current,ensure_ascii=False)
        db.session.commit()
    except LectureFormDraftConflict:
        db.session.rollback()
        return jsonify(success=False,code='draft_conflict',message='听课草稿已更新，请保留当前输入并刷新后重新打开记录。'),409
    except Exception:
        db.session.rollback();raise
    return jsonify(success=True,data=_public(record))
