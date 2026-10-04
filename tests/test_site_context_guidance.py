from tests.test_site_context_model import candidate, at_building
from app.services.site_context_model import rank_site_candidates


def ranked(rows,**kwargs):
    return rank_site_candidates(rows,location=at_building('25'),
        door_hypotheses=[{'value':'0609','score':.99}],clock='09:00',**kwargs)


def test_default_keeps_nearby_eight_and_moves_far_rooms_to_expansion():
    r=ranked([candidate('eight','8-609'),candidate('far38','38-609'),candidate('far32','32-609')])
    assert 'guidance' in r
    assert r['guidance']['mode']=='recommend'
    assert r['guidance']['candidate_ids']==['eight']
    assert len(r['candidates'])==3


def test_two_nearby_matching_rooms_ask_building_instead_of_forcing_three_cards():
    r=ranked([candidate('eight','8-609'),candidate('twentyfive','25-609')])
    assert 'guidance' in r
    assert r['guidance']['mode']=='clarify'
    q=r['guidance']['question']
    assert q['kind']=='building'
    assert {o['value'] for o in q['options']}=={'8','25'}


def test_human_building_overrides_gps_and_stops_clarification():
    r=ranked([candidate('far','38-609'),candidate('eight','8-609')],confirmed_facts={'building':'38'})
    assert 'guidance' in r
    assert r['guidance']['mode']=='recommend' and r['guidance']['candidate_ids']==['far']


def test_no_evidence_single_candidate_is_not_presented_as_reliable():
    r=rank_site_candidates([candidate('only','8-609')])
    assert 'guidance' in r
    assert r['guidance']['mode']=='expand'
    assert r['guidance']['candidate_ids']==[]


def test_two_used_questions_stop_even_if_old_fact_was_removed():
    r=ranked([candidate('eight','8-609'),candidate('other','25-609')],question_count=2,asked=[])
    assert r['guidance']['mode']=='expand'
    assert r['guidance']['question'] is None
    assert len(r['candidates'])==2


def test_missing_gps_asks_between_supported_buildings_not_arbitrary_course_names():
    r=rank_site_candidates([candidate('a','8-609'),candidate('b','38-609')],
        door_hypotheses=[{'value':'0609','score':.99}],clock='09:00')
    assert 'guidance' in r
    assert r['guidance']['mode']=='clarify'
    assert r['guidance']['question']['kind']=='building'


def test_unknown_map_building_remains_reachable_but_is_not_nearby_recommendation():
    r=ranked([candidate('near','8-609'),candidate('unknown','46-609')])
    assert 'guidance' in r
    assert r['guidance']['candidate_ids']==['near']
    assert len(r['candidates'])==2


def test_weak_door_reading_gets_a_supported_confirmation_not_direct_recommendation():
    r=rank_site_candidates([candidate('a','8-609')],location=at_building('8'),
        door_hypotheses=[{'value':'0609','score':.4}],clock='09:00')
    assert 'guidance' in r
    assert r['guidance']['mode']=='clarify'
    assert r['guidance']['question']['kind']=='room_number'


def test_same_room_and_period_can_show_two_courses_without_an_extra_teacher_question():
    r=ranked([candidate('a','8-609',teacher='甲'),candidate('b','8-609',teacher='乙')])
    assert r['guidance']['mode']=='recommend'
    assert set(r['guidance']['candidate_ids'])=={'a','b'}


def test_too_many_uncertain_buildings_do_not_become_confident_cards_when_no_short_question_exists():
    rows=[candidate(str(i),f'{i}-609',teacher=str(i)) for i in range(1,11)]
    r=rank_site_candidates(rows,door_hypotheses=[{'value':'0609','score':.99}],clock='09:00',
                          confirmed_facts={'period':'1-2'},asked=['period'],question_count=1)
    assert r['guidance']['mode']=='expand'


def test_skipping_only_discriminator_does_not_claim_scene_is_resolved():
    r=ranked([candidate('a','8-609'),candidate('b','25-609')],asked=['building'],question_count=1)
    assert r['guidance']['mode']=='expand'


def test_confirmed_building_resolves_location_warning_on_course_card():
    r=rank_site_candidates([candidate('a','8-609')],confirmed_facts={'building':'8'},
        door_hypotheses=[{'value':'0609','score':.99}],clock='09:00')
    c=r['candidates'][0]
    assert '楼栋已由你确认' in c['match_reasons']
    assert '该楼栋定位证据不足' not in c['match_uncertainties']
