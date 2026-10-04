"""OCR direction and strict room-label parsing, independent of timetable answers."""
from unittest import mock

import numpy as np
import pytest
from PIL import Image

from app.services import lecture_site_capture as capture
from tests.test_lecture_site_capture import setup_capture


@pytest.mark.parametrize('line,expected',[
    ('（112講義室）','112'), ('教室 ３０２','302'), ('Room 0609','0609'),
    ('Classroom: 1208','1208'), ('0612 教室','0612'), ('(312)','312'),
])
def test_complete_room_labels_keep_only_the_actual_digits(line,expected):
    assert capture.room_numbers([(line,.97)])['room_number']==expected


@pytest.mark.parametrize('line',[
    '座位350人','电话13812345678','2026-10-03','O6O9','room 12',
    'foo 302 bar','Room 302 capacity 350','302/303','Room 12345',
    'Classroom 2016-10','www.example.com/302','联系电话：302',
])
def test_noise_and_letter_confusions_do_not_become_numeric_door_candidates(line):
    assert capture.room_numbers([(line,.999)])['alternatives']==[]


def test_detected_line_reverse_reading_is_retained_and_blocks_wrong_autofill(tmp_path):
    array=np.zeros((180,300,3),dtype=np.uint8)
    array[:,150:]=255
    path=tmp_path/'door.png';Image.fromarray(array).save(path)
    reads=[]
    def engine(image,**kwargs):
        if kwargs.get('use_det') is False:
            flipped=image[image.shape[0]//2,2,0]>image[image.shape[0]//2,-3,0]
            reads.append(flipped)
            return [['0609' if flipped else '6090',.997]],None
        return [[[[40,50],[260,50],[260,100],[40,100]],'6090',.998]],None
    with mock.patch.object(capture,'_engine',return_value=engine):
        result=capture.recognize_door(path)
    assert set(result['alternatives'])=={'0609','6090'}
    assert result['room_number'] is None and result['status']=='needs_check'
    assert reads==[False,True]


def test_whole_numeric_read_does_not_silently_rotate_upright_glyphs(tmp_path):
    path=tmp_path/'door.png';Image.new('RGB',(300,180),'white').save(path)
    whole_options=[]
    def engine(image,**kwargs):
        if kwargs.get('use_det') is False:return [['0609',.996]],None
        whole_options.append(kwargs)
        value='0609' if kwargs.get('use_cls') is False else '6090'
        return [[[[40,50],[200,50],[200,100],[40,100]],value,.997]],None
    with mock.patch.object(capture,'_engine',return_value=engine):
        result=capture.recognize_door(path)
    assert result['alternatives']==['0609']
    assert whole_options==[{'use_cls':False}]


def test_portrait_manual_box_is_aligned_as_a_line_and_both_directions_read(tmp_path):
    path=tmp_path/'sideways.png';Image.new('RGB',(300,600),'white').save(path)
    shapes=[]
    def engine(image,**kwargs):
        shapes.append(image.shape[:2])
        return ([['302',.99]] if image.shape[1]>image.shape[0] else []),None
    with mock.patch.object(capture,'_engine',return_value=engine):
        result=capture.recognize_door(path,region={'x':.2,'y':.1,'width':.2,'height':.7})
    assert result['alternatives']==['302']
    assert len(shapes)==2 and all(height<width for height,width in shapes)


@pytest.mark.parametrize('weak_score',[.72,.56])
def test_weak_rotated_noise_is_retained_but_does_not_veto_a_strong_direct_read(tmp_path,weak_score):
    array=np.zeros((180,300,3),dtype=np.uint8);array[:,150:]=255
    path=tmp_path/'door.png';Image.fromarray(array).save(path)
    def engine(image,**kwargs):
        if kwargs.get('use_det') is False:
            flipped=image[image.shape[0]//2,2,0]>image[image.shape[0]//2,-3,0]
            return [['7190',weak_score] if flipped else ['0612',.996]],None
        return [[[[40,50],[260,50],[260,100],[40,100]],'0612',.997]],None
    with mock.patch.object(capture,'_engine',return_value=engine):
        result=capture.recognize_door(path)
    assert result['room_number']=='0612'
    assert result['alternatives']==['0612','7190']
    assert any(h['value']=='7190' and h['score']==weak_score for h in result['hypotheses'])
    assert capture.decode_ocr_evidence(result)['alternatives']==result['alternatives']


def test_saved_ocr_evidence_keeps_the_direct_orientation_of_near_ties():
    import json
    evidence={'hypotheses':[
        {'value':'0609','score':.997,'method':'whole'},
        {'value':'6090','score':.998,'method':'region_single_line_rot180'},
    ]}
    first=capture.decode_ocr_evidence(json.dumps(evidence))
    second=capture.decode_ocr_evidence(json.dumps(first))
    assert first['alternatives']==second['alternatives']==['0609','6090']
    assert first['hypotheses'][0]['score']==.997
    assert first['hypotheses'][1]['score']==.998


def test_real_photo_upload_reload_and_guidance_keep_identical_ocr_order(setup_capture):
    import json
    from pathlib import Path
    from datetime import datetime,timezone,timedelta
    from app.models import db,LectureSiteCapture
    path=Path(__file__).resolve().parents[1]/'output/2026-10-03-photo-guided-audit/ocr/samples/supplied-1-perspective.jpg'
    if not path.exists():pytest.skip('Local real-photo regression fixture is not distributed with repository')
    case=setup_capture
    instant=datetime(2026,4,7,9,0,tzinfo=timezone(timedelta(hours=8)))
    with path.open('rb') as photo, mock.patch.object(capture,'now_at_site',return_value=instant):
        response=case.client.post('/user/api/site-capture',data={'photo':(photo,'capture.jpg')},content_type='multipart/form-data')
    assert response.status_code==201,response.get_json()
    public=response.get_json()['data'];record=db.session.get(LectureSiteCapture,public['id'])
    direct=capture.recognize_door(Path(case.app.config['LECTURE_CAPTURE_FOLDER'])/record.photo_key)
    assert direct['hypotheses'][0]['value']=='0609'
    assert public['alternatives'][0]=='0609'
    assert public['ocr_status']==direct['status']
    assert public['room_number']==direct['room_number']
    reload=case.client.get(f"/user/api/site-capture/{record.id}").get_json()['data']
    assert reload['alternatives']==public['alternatives']
    evidence=capture.decode_ocr_evidence(record.ocr_alternatives_json)
    from app.services.site_context_model import rank_site_candidates
    guide=rank_site_candidates([],door_hypotheses=evidence['hypotheses'])
    assert guide['question']['options'][0]['value']=='609'


@pytest.mark.parametrize('score',[True,False,float('nan'),float('inf'),-1,1.1])
def test_invalid_confidence_is_not_claimed_as_success(score):
    assert capture.room_numbers([('302',score)])['room_number'] is None
