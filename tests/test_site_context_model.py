from datetime import date
import math

from app.services import site_context_model as model
from app.services.campus_buildings import load_buildings, representative_point


def candidate(cid, room, period=(1,2), teacher='张老师'):
    return {'candidate_id':cid,'room':room,'period':list(period),'teacher_name':teacher,
            'course_title':'课程'+cid,'lecture_date':'2026-04-07','source_kind':'primary',
            'student_grade_class':'2025级1班','conflicts':[]}


def at_building(code='25', accuracy=35):
    f=next(f for f in load_buildings()['features'] if f['properties']['code']==code)
    lon,lat=representative_point(f)
    return {'latitude':lat,'longitude':lon,'accuracy':accuracy}


def test_8_can_beat_25_when_door_and_time_support_it():
    rows=[candidate('twentyfive','25-501'),candidate('eight','8-609'),
          candidate('wrongtime','25-609',(7,8))]
    r=model.rank_site_candidates(rows,location=at_building(),
        door_hypotheses=[{'value':'0609','score':.998}],clock='09:00')
    assert r['candidates'][0]['candidate_id']=='eight'
    assert len(r['candidates'])==3
    assert r['score_kind']=='uncalibrated_match'
    assert r['requires_confirmation'] is True


def test_correlated_duplicate_fixes_do_not_create_false_certainty():
    point=at_building();many={**point,'samples':[dict(point,timestamp=100+i) for i in range(10)]}
    a=model.location_summary(point);b=model.location_summary(many)
    assert a['scale_m']==b['scale_m']


def test_isolated_jump_inflates_uncertainty_without_dragging_centre():
    point=at_building();jump=at_building('10')
    summary=model.location_summary({**point,'samples':[point,point,jump]})
    assert abs(summary['latitude']-point['latitude'])<1e-6
    assert summary['unstable'] is True


def test_missing_map_or_location_never_excludes_primary_course():
    rows=[candidate('unknown','46-609'),candidate('eight','8-609')]
    r=model.rank_site_candidates(rows,door_hypotheses=[{'value':'0609','score':.99}],clock='09:00')
    assert {c['candidate_id'] for c in r['candidates']}=={'unknown','eight'}
    assert all(math.isfinite(c['match_score']) for c in r['candidates'])


def test_confirmed_building_overrides_wrong_location():
    rows=[candidate('eight','8-609'),candidate('twentyfive','25-609')]
    r=model.rank_site_candidates(rows,location=at_building(),confirmed_facts={'building':'08'})
    assert [c['candidate_id'] for c in r['candidates']]==['eight']


def test_confirmed_room_overrides_ocr_and_preserves_complete_period():
    rows=[candidate('correct','8-612',(1,2)),candidate('wrong','8-609',(1,2))]
    r=model.rank_site_candidates(rows,door_hypotheses=[{'value':'0609','score':.99}],
        confirmed_facts={'room_number':'0612'},clock='09:00')
    assert r['candidates'][0]['period']==[1,2]
    assert r['candidates'][0]['candidate_id']=='correct'


def test_repeated_ocr_hypotheses_do_not_add_votes():
    rows=[candidate('a','8-609'),candidate('b','8-612')]
    h={'value':'0609','score':.99}
    one=model.rank_site_candidates(rows,door_hypotheses=[h])
    many=model.rank_site_candidates(rows,door_hypotheses=[h]*10)
    assert [c['match_score'] for c in one['candidates']]==[c['match_score'] for c in many['candidates']]


def test_6090_0609_conflict_remains_explicit():
    r=model.rank_site_candidates([candidate('a','8-609')],
        door_hypotheses=[{'value':'6090','score':.998},{'value':'0609','score':.996}])
    assert '门牌读数需核对' in r['uncertainties']
    assert r['requires_confirmation'] is True


def test_break_and_delayed_photo_are_soft_time_evidence():
    rows=[candidate('previous','8-609',(1,2)),candidate('next','8-609',(3,4))]
    r=model.rank_site_candidates(rows,clock='09:45')
    assert len(r['candidates'])==2
    assert r['candidates'][0]['candidate_id']=='previous'


def test_active_question_asks_building_when_it_is_only_distinction():
    r=model.rank_site_candidates([candidate('a','8-609'),candidate('b','25-609'),
                                 candidate('c','8-609'),candidate('d','25-609')])
    assert r['question']['kind']=='building'
    assert {o['value'] for o in r['question']['options']}=={'8','25'}


def test_active_question_avoids_known_or_already_asked_kinds():
    rows=[candidate('a','8-609',teacher='甲'),candidate('b','8-612',teacher='乙'),
          candidate('c','8-609',teacher='甲'),candidate('d','8-612',teacher='乙')]
    r=model.rank_site_candidates(rows,confirmed_facts={'building':'8'},asked=['room_number'])
    assert r['question']['kind']=='teacher_name'
    exhausted=model.rank_site_candidates(rows,asked=['building','room_number','period','teacher_name'])
    assert exhausted['question'] is None


def test_empty_candidates_offer_manual_without_fabricated_options():
    r=model.rank_site_candidates([])
    assert r['candidates']==[] and r['question'] is None and r['manual_available'] is True


def test_unstable_location_has_weaker_weight_than_stable_fix():
    stable=model.location_summary(at_building())
    broad=model.location_summary(at_building(accuracy=800))
    assert broad['reliability']<stable['reliability']


def test_bad_location_and_invalid_ocr_scores_are_ignored():
    r=model.rank_site_candidates([candidate('a','8-609')],
        location={'latitude':True,'longitude':106.4,'accuracy':0},
        door_hypotheses=[{'value':'0609','score':float('nan')},{'value':'12345678','score':.99}])
    assert math.isfinite(r['candidates'][0]['match_score'])


def test_backup_and_invalid_period_are_not_promoted_by_model():
    backup={**candidate('backup','8-609'),'source_kind':'backup'}
    invalid={**candidate('bad','8-609'),'period':[1,99]}
    r=model.rank_site_candidates([backup,invalid,candidate('valid','8-609')])
    assert [c['candidate_id'] for c in r['candidates']]==['valid']


def test_outside_known_campus_downgrades_location_to_missing_evidence():
    r=model.rank_site_candidates([candidate('a','8-609')],
        location={'latitude':30.5,'longitude':106.4,'accuracy':20})
    assert r['candidates'][0]['match_components']['location']==0


def test_other_campus_candidate_does_not_use_beibei_building_geometry():
    row={**candidate('other','8-609'),'campus':'rongchang'}
    r=model.rank_site_candidates([row],location=at_building())
    assert r['candidates'][0]['match_components']['location']==0


def test_exact_class_end_is_nearby_time_not_in_class():
    r=model.rank_site_candidates([candidate('a','8-609')],clock='09:40')
    assert '拍照时段吻合' not in r['candidates'][0]['match_reasons']


def test_missing_geometry_is_neutral_not_a_perfect_location_match():
    r=model.rank_site_candidates([candidate('unknown','46-609'),candidate('mapped','8-609')],
        location=at_building('8'),door_hypotheses=[{'value':'0609','score':.99}],clock='09:00')
    assert r['candidates'][0]['candidate_id']=='mapped'
    assert r['candidates'][1]['candidate_id']=='unknown'


def test_period_outside_supplied_clock_table_is_preserved_without_fabricated_time():
    r=model.rank_site_candidates([candidate('a','8-609',(15,16))],clock='09:00')
    assert r['candidates'][0]['match_components']['time']==0
    assert r['candidates'][0]['requires_manual_time'] is True


def test_human_confirmed_room_resolves_machine_disagreement():
    r=model.rank_site_candidates([candidate('a','8-612')],
        door_hypotheses=[{'value':'0609','score':.99},{'value':'6090','score':.99}],
        confirmed_facts={'room_number':'0612'})
    assert '门牌读数需核对' not in r['uncertainties']
    assert r['candidates'][0]['match_components']['door']==0


def test_conflicting_photo_drives_concrete_door_options_not_popular_online_locations():
    rows=[candidate('real','8-609')]+[candidate(str(i),'Online Learning',teacher=str(i)) for i in range(100)]
    r=model.rank_site_candidates(rows,door_hypotheses=[{'value':'6090','score':.998},{'value':'0609','score':.997}])
    assert r['question']['kind']=='room_number'
    assert {o['label'] for o in r['question']['options']}=={'6090','0609'}


def test_numeric_door_question_never_offers_online_learning_as_a_room_number():
    rows=[candidate('a','8-609'),candidate('b','8-612')]+[candidate(str(i),'Online Learning') for i in range(100)]
    r=model.rank_site_candidates(rows,door_hypotheses=[{'value':'0609','score':.99}])
    if r['question'] and r['question']['kind']=='room_number':
        assert all(o['value']!='Online Learning' for o in r['question']['options'])


def test_two_buildings_are_easier_to_answer_than_twenty_teachers():
    rows=[candidate(str(i),('8' if i<10 else '25')+'-609',teacher=str(i)) for i in range(20)]
    r=model.rank_site_candidates(rows,door_hypotheses=[{'value':'0609','score':.99}],clock='09:00')
    assert r['question']['kind']=='building'
    assert len(r['question']['options'])==2


def test_missing_map_does_not_beat_matching_biased_building_by_default():
    rows=[candidate('unknown','46-609'),candidate('eight','8-609')]
    r=model.rank_site_candidates(rows,location=at_building('25'),
        door_hypotheses=[{'value':'0609','score':.99}],clock='09:00')
    assert r['candidates'][0]['candidate_id']=='eight'
    assert all(c['match_components']['location']>=0 for c in r['candidates'])


def test_three_courses_go_directly_to_cards_unless_photo_reads_conflict():
    rows=[candidate(str(i),'8-609',teacher=str(i)) for i in range(3)]
    assert model.rank_site_candidates(rows)['question'] is None
    r=model.rank_site_candidates(rows,door_hypotheses=[{'value':'0609','score':.99},{'value':'6090','score':.99}])
    assert r['question']['kind']=='room_number'
