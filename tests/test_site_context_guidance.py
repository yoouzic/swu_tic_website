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


def test_missing_photo_and_location_can_confirm_timetable_building_without_confidence():
    r=rank_site_candidates([candidate('only','8-609')])
    assert 'guidance' in r
    assert r['guidance']['mode']=='clarify'
    assert r['question']['kind']=='building'
    assert [o['value'] for o in r['question']['options']]==['8']
    assert r['requires_confirmation'] is True
    assert r['candidates'][0]['match_components']['location']==0


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


def test_many_uncertain_buildings_keep_all_options_when_question_budget_remains():
    rows=[candidate(str(i),f'{i}-609',teacher=str(i)) for i in range(1,11)]
    r=rank_site_candidates(rows,door_hypotheses=[{'value':'0609','score':.99}],clock='09:00',
                          confirmed_facts={'period':'1-2'},asked=['period'],question_count=1)
    assert r['guidance']['mode']=='clarify'
    assert r['guidance']['question']['kind']=='building'
    assert {o['value'] for o in r['guidance']['question']['options']}=={str(i) for i in range(1,11)}
    assert r['question']==r['guidance']['question']


def test_skipping_only_discriminator_does_not_claim_scene_is_resolved():
    r=ranked([candidate('a','8-609'),candidate('b','25-609')],asked=['building'],question_count=1)
    assert r['guidance']['mode']=='expand'


def test_confirmed_building_resolves_location_warning_on_course_card():
    r=rank_site_candidates([candidate('a','8-609')],confirmed_facts={'building':'8'},
        door_hypotheses=[{'value':'0609','score':.99}],clock='09:00')
    c=r['candidates'][0]
    assert '楼栋已由你确认' in c['match_reasons']
    assert '无法凭定位确认该楼栋' not in c['match_uncertainties']


def screenshot_scene():
    # The nine exact 402 courses in the supplied timetable's 7-9 scene.
    buildings=['32','8','28','5','31','10','27','8','30']
    rows=[candidate(str(i),f'{building}-402',(7,9),teacher=f'教师{i}')
          for i,building in enumerate(buildings)]
    rows[2]['course_title']='数据结构'
    rows.extend(candidate(f'late{i}',f'{building}-402',(9,10),teacher=f'后续教师{i}')
                for i,building in enumerate(['5','9','9']))
    rows.extend([candidate('short','5-402',(7,8)),candidate('other_short','9-402',(7,8)),
                 candidate('earlier','24-402',(6,8))])
    return rows


def screenshot_ranked(**kwargs):
    return rank_site_candidates(screenshot_scene(),
        door_hypotheses=[{'value':'402','score':.7926047046979269,'method':'region_single_line'}],
        clock='15:48',**kwargs)


def test_missing_gps_screenshot_scene_asks_building_before_period():
    r=screenshot_ranked()
    assert r['guidance']['mode']=='clarify'
    assert r['question']==r['guidance']['question']
    q=r['guidance']['question']
    assert q['kind']=='building'
    assert '28' in {o['value'] for o in q['options']}
    assert len(q['options'])>6
    assert len(r['candidates'])==len(screenshot_scene())


def test_confirming_28_in_screenshot_scene_recommends_data_structures():
    r=screenshot_ranked(confirmed_facts={'building':'28'},asked=['building'],question_count=1)
    assert r['guidance']['mode']=='recommend'
    assert r['guidance']['candidate_ids']==['2']
    assert r['candidates'][0]['room']=='28-402'
    assert r['candidates'][0]['course_title']=='数据结构'
    assert r['question'] is None


def test_screenshot_scene_after_period_still_offers_all_eight_buildings():
    r=screenshot_ranked(confirmed_facts={'period':'7-9'},asked=['period'],question_count=1)
    assert r['guidance']['mode']=='clarify'
    assert r['guidance']['questions_remaining']==1
    assert r['question']==r['guidance']['question']
    assert {o['value'] for o in r['question']['options']}=={'5','8','10','27','28','30','31','32'}


def test_coarse_location_screenshot_scene_still_asks_building_first():
    r=screenshot_ranked(location=at_building('28',accuracy=500))
    assert r['guidance']['location_scoped'] is False
    assert r['question']==r['guidance']['question']
    assert r['question']['kind']=='building'
    assert '28' in {o['value'] for o in r['question']['options']}


def test_single_photo_supported_course_without_location_confirms_building():
    r=rank_site_candidates([candidate('only','28-402',(7,9))],
        door_hypotheses=[{'value':'402','score':.79}],clock='15:48')
    assert r['guidance']['mode']=='clarify'
    assert r['question']['kind']=='building'
    assert [o['value'] for o in r['question']['options']]==['28']


def test_single_course_with_valid_nearby_location_keeps_direct_confirmation():
    r=rank_site_candidates([candidate('only','28-402',(7,9))],location=at_building('28'),
        door_hypotheses=[{'value':'402','score':.79}],clock='15:48')
    assert r['guidance']['mode']=='recommend'
    assert r['question'] is None
    assert r['question']==r['guidance']['question']


def test_ambiguous_photo_requires_room_confirmation_before_course_recommendation():
    r=rank_site_candidates([candidate('only','28-609',(7,9))],location=at_building('28'),
        door_hypotheses=[{'value':'0609','score':.998},{'value':'6090','score':.997}],clock='15:48')
    assert r['guidance']['mode']=='clarify'
    assert r['question']==r['guidance']['question']
    assert r['question']['kind']=='room_number'
    assert {o['value'] for o in r['question']['options']}=={'609','6090'}


def test_missing_location_skipped_building_is_not_asked_again():
    r=screenshot_ranked(asked=['building'],question_count=1)
    assert r['question']==r['guidance']['question']
    assert r['question'] is None or r['question']['kind']!='building'
    assert r['guidance']['questions_used']==1


def test_question_budget_exhaustion_removes_both_question_payloads():
    r=screenshot_ranked(asked=['building','period'],question_count=2)
    assert r['guidance']['mode']=='expand'
    assert r['question'] is None
    assert r['guidance']['question'] is None


def test_skipped_ambiguous_room_does_not_repeat_or_claim_a_resolved_scene():
    r=rank_site_candidates([candidate('only','28-609',(7,9))],location=at_building('28'),
        door_hypotheses=[{'value':'0609','score':.998},{'value':'6090','score':.997}],clock='15:48',
        asked=['room_number'],question_count=1)
    assert r['guidance']['mode']=='expand'
    assert r['question'] is None


def test_missing_location_and_ocr_ask_current_timetable_buildings_before_period():
    rows=[candidate(str(i),f'{building}-402',(7,9),teacher=f'教师{i}')
          for i,building in enumerate(['5','8','10','27','28','30','31','32'])]
    r=rank_site_candidates(rows)
    assert r['guidance']['mode']=='clarify'
    assert r['question']==r['guidance']['question']
    assert r['question']['kind']=='building'
    assert {o['value'] for o in r['question']['options']}=={'5','8','10','27','28','30','31','32'}
    assert len(r['candidates'])==len(rows)
    assert all(c['match_score']==0 for c in r['candidates'])


def test_missing_all_sensor_evidence_building_skip_and_budget_are_respected():
    rows=[candidate('a','28-402',(7,9)),candidate('b','8-402',(7,9))]
    for asked,count in [(['building'],1),([],2)]:
        r=rank_site_candidates(rows,asked=asked,question_count=count)
        assert r['guidance']['mode']=='expand'
        assert r['question'] is None
        assert len(r['candidates'])==2


def test_more_than_six_teachers_still_ask_the_only_remaining_discriminator():
    rows=[candidate(str(i),'28-402',(7,9),teacher=f'教师{i}') for i in range(8)]
    r=rank_site_candidates(rows,confirmed_facts={'building':'28','room_number':'402','period':'7-9'},
                          asked=['building'],question_count=1)
    assert r['guidance']['mode']=='clarify'
    assert r['question']['kind']=='teacher_name'
    assert len(r['question']['options'])==8
    assert len(r['candidates'])==8


def test_more_than_six_room_options_remain_available_with_nearby_time_support():
    rows=[candidate(str(i),f'28-{400+i}',(7,9)) for i in range(8)]
    r=rank_site_candidates(rows,location=at_building('28'),clock='15:48',
                          confirmed_facts={'building':'28','period':'7-9'},asked=['building'],question_count=1)
    assert r['guidance']['mode']=='clarify'
    assert r['question']['kind']=='room_number'
    assert len(r['question']['options'])==8
    assert len(r['candidates'])==8
