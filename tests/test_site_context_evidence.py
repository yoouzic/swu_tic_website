import json
from unittest import mock

import pytest
from PIL import Image

from app.services import lecture_site_capture as capture


def test_auto_region_keeps_conflicting_6090_and_0609(tmp_path):
    path=tmp_path/'door.jpg';Image.new('RGB',(300,180),'white').save(path)
    calls=[]
    def engine(image,**kwargs):
        calls.append(kwargs)
        if kwargs.get('use_det') is False:return [['0609',.996]],None
        return [[[[40,50],[200,50],[200,100],[40,100]],'6090',.998]],None
    with mock.patch.object(capture,'_engine',return_value=engine):result=capture.recognize_door(path)
    assert result['room_number'] is None and result['status']=='needs_check'
    assert set(result['alternatives'])=={'0609','6090'}
    assert {h['method'] for h in result['hypotheses']}=={'whole','region_single_line'}
    assert calls[-1]=={'use_det':False,'use_cls':False}


def test_same_photo_same_reading_is_not_counted_twice(tmp_path):
    path=tmp_path/'door.jpg';Image.new('RGB',(300,180),'white').save(path)
    def engine(image,**kwargs):
        if kwargs.get('use_det') is False:return [['0612',.996]],None
        return [[[[40,50],[200,50],[200,100],[40,100]],'0612',.99]],None
    with mock.patch.object(capture,'_engine',return_value=engine):result=capture.recognize_door(path)
    assert result['alternatives']==['0612'] and len(result['hypotheses'])==1
    assert result['hypotheses'][0]['score']==.996


def test_legacy_and_structured_ocr_json_are_compatible():
    old=capture.decode_ocr_evidence(json.dumps(['601','602']))
    new=capture.decode_ocr_evidence(json.dumps({'hypotheses':[{'value':'0609','score':.99,'method':'whole'}]}))
    assert old['alternatives']==['601','602']
    assert new['alternatives']==['0609']
    assert capture.decode_ocr_evidence('not json')['alternatives']==[]


def test_timestamped_samples_are_bounded_and_stale_samples_rejected():
    now=1_700_000_000_000
    best={'latitude':29.82,'longitude':106.42,'accuracy':35}
    samples=[dict(best,timestamp=now-i*100) for i in range(20)]
    samples.append(dict(best,timestamp=now-90_000))
    result=capture.normalize_location_evidence({**best,'samples':samples},now_ms=now)
    assert len(result['samples'])==12
    assert all(now-p['timestamp']<=30000 for p in result['samples'])


def test_late_location_merge_keeps_best_fix_and_observes_same_accuracy_samples():
    base={'latitude':29.82,'longitude':106.42,'accuracy':12,
          'samples':[{'latitude':29.82,'longitude':106.42,'accuracy':12,'timestamp':1000}]}
    newer={'latitude':29.82001,'longitude':106.42,'accuracy':40,
           'samples':[{'latitude':29.82001,'longitude':106.42,'accuracy':40,'timestamp':2000}]}
    result=capture.merge_location_evidence(base,newer)
    assert result['accuracy']==12 and len(result['samples'])==2


def test_upload_clock_is_only_a_soft_hint_and_stale_capture_is_weakened():
    good=capture.clock_evidence('2026-04-07T09:01:00+08:00','2026-04-07T01:00:00Z')
    assert good['clock']=='09:00' and good['reliability']==1
    stale=capture.clock_evidence('2026-04-07T15:00:00+08:00','2026-04-07T01:00:00Z')
    assert stale['clock'] is None and stale['reliability']==0
    assert capture.clock_evidence('2026-04-07T09:00:00+08:00',None)['reliability']<1


def test_manual_region_recovers_when_whole_photo_has_no_numeric_detection(tmp_path):
    path=tmp_path/'door.jpg';Image.new('RGB',(300,180),'white').save(path)
    original=path.read_bytes();shapes=[]
    def engine(image,**kwargs):
        shapes.append(image.shape)
        assert kwargs=={'use_det':False,'use_cls':False}
        return [['0609',.99]],None
    with mock.patch.object(capture,'_engine',return_value=engine):
        result=capture.recognize_door(path,region={'x':.1,'y':.2,'width':.6,'height':.3})
    assert result['alternatives']==['0609']
    assert result['hypotheses'][0]['method']=='manual_region'
    assert shapes[0][:2]==(54,180)
    assert path.read_bytes()==original


@pytest.mark.parametrize('region',[
    {'x':-.1,'y':0,'width':.5,'height':.5},
    {'x':.8,'y':0,'width':.5,'height':.5},
    {'x':True,'y':0,'width':.5,'height':.5},
    {'x':0,'y':0,'width':.001,'height':.5},
    {'x':0,'y':0,'width':float('nan'),'height':.5},
])
def test_manual_region_rejects_invalid_or_tiny_selection(tmp_path,region):
    path=tmp_path/'door.jpg';Image.new('RGB',(300,180),'white').save(path)
    with pytest.raises(ValueError):capture.recognize_door(path,region=region)


def test_auto_region_rechecks_text_box_even_if_whole_ocr_contains_letters(tmp_path):
    path=tmp_path/'door.jpg';Image.new('RGB',(300,180),'white').save(path)
    def engine(image,**kwargs):
        if kwargs.get('use_det') is False:return [['0609',.99]],None
        return [[[[40,50],[200,50],[200,100],[40,100]],'O6O9',.7]],None
    with mock.patch.object(capture,'_engine',return_value=engine):result=capture.recognize_door(path)
    assert '0609' in result['alternatives']


def test_auto_region_finds_sign_shape_when_text_detector_misses_everything(tmp_path):
    from PIL import ImageDraw
    path=tmp_path/'door.jpg';image=Image.new('RGB',(800,600),'#ddd5bb');draw=ImageDraw.Draw(image)
    draw.rectangle((220,240,580,330),fill='#e6d9b6',outline='#292c2d',width=7)
    draw.text((320,270),'0609',fill='black');image.save(path)
    seen=[]
    def engine(image,**kwargs):
        if kwargs.get('use_det') is False:
            seen.append(image.shape);return [['0609',.98]],None
        return None,None
    with mock.patch.object(capture,'_engine',return_value=engine):result=capture.recognize_door(path)
    assert '0609' in result['alternatives']
    assert any(h['method']=='visual_region' for h in result['hypotheses'])
    # At most four geometry proposals, with two real orientation reads each.
    assert seen and len(seen)<=8
    assert result['regions'] and all(0<=r['x']<=1 for r in result['regions'])


def test_blank_photo_does_not_invent_a_sign_region(tmp_path):
    path=tmp_path/'blank.jpg';Image.new('RGB',(600,400),'white').save(path)
    with mock.patch.object(capture,'_engine',return_value=lambda image,**kw:(None,None)):
        result=capture.recognize_door(path)
    assert result['alternatives']==[]
    assert result.get('regions',[])==[]
