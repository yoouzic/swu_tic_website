"""Behavioural coverage for uncertain location suggestions, independent of Flask/DB."""
from math import nan

import pytest

from app.services import campus_buildings as mapping


def fix(lat=29.825, lon=106.42, accuracy=35):
    return {'latitude': lat, 'longitude': lon, 'accuracy': accuracy}


def square(code='8'):
    return {'type': 'Feature', 'geometry': {'type': 'Polygon', 'coordinates': [[
        [106.4199, 29.8249], [106.4201, 29.8249],
        [106.4201, 29.8251], [106.4199, 29.8251], [106.4199, 29.8249],
    ]]}, 'properties': {'code': code, 'name': f'{code}教学楼',
                         'campus': 'beibei', 'source_url': 'https://www.openstreetmap.org/way/1',
                         'verification': 'public_map_unverified'}}


def dataset(*features):
    return {'type': 'FeatureCollection', 'features': list(features)}


def test_fix_inside_footprint_is_a_suggestion_not_confirmation():
    result = mapping.nearby_buildings(fix(), dataset=dataset(square()))
    assert result['status'] == 'ok'
    assert result['buildings'][0]['code'] == '8'
    assert result['buildings'][0]['distance_m'] == 0
    assert result['requires_confirmation'] is True


def test_distance_is_to_wall_not_centroid():
    result = mapping.nearby_buildings(fix(lon=106.4202), dataset=dataset(square()))
    assert 9 < result['buildings'][0]['distance_m'] < 11


def test_reported_25_keeps_8_as_a_nearby_alternative():
    data = mapping.load_buildings()
    features = {f['properties']['code']: f for f in data['features']}
    lon, lat = mapping.representative_point(features['25'])
    result = mapping.nearby_buildings(fix(lat, lon), dataset=data)
    assert result['buildings'][0]['code'] == '25'
    assert '8' in {b['code'] for b in result['buildings']}
    assert result['radius_m'] == 250


def test_low_accuracy_expands_neighbourhood_without_truncating():
    features = []
    for code in range(1, 9):
        f = square(str(code))
        for p in f['geometry']['coordinates'][0]:
            p[0] += code * .001
        features.append(f)
    data = dataset(*features)
    tight = mapping.nearby_buildings(fix(), dataset=data)
    broad = mapping.nearby_buildings(fix(accuracy=800), dataset=data)
    assert len(broad['buildings']) > len(tight['buildings'])
    assert len(broad['buildings']) == 8
    assert broad['radius_m'] == 850


@pytest.mark.parametrize('location', [None, {}, fix(accuracy=0), fix(accuracy=nan),
                                      fix(lat=100), fix(lon=True), {'latitude':29.82}])
def test_invalid_fixes_leave_manual_path(location):
    result = mapping.nearby_buildings(location, dataset=dataset(square()))
    assert result['status'] == 'location_unavailable'
    assert result['buildings'] == []


def test_very_coarse_fix_is_not_a_precise_building_suggestion():
    assert mapping.nearby_buildings(fix(accuracy=2000))['status'] == 'location_too_coarse'


def test_no_faraway_nearest_building_fallback():
    result = mapping.nearby_buildings(fix(lat=29.7), dataset=dataset(square()))
    assert result['status'] == 'no_mapped_building_nearby'
    assert result['buildings'] == []


def test_other_campus_never_reuses_beibei_codes():
    result = mapping.nearby_buildings(fix(), campus='rongchang', dataset=dataset(square()))
    assert result['status'] == 'campus_unmapped'
    assert result['buildings'] == []


def test_unmapped_schedule_buildings_remain_explicit():
    result = mapping.nearby_buildings(fix(), allowed_buildings=['08', '46'], dataset=dataset(square()))
    assert [b['code'] for b in result['buildings']] == ['8']
    assert result['unmapped_buildings'] == ['46']


def test_coordinate_system_mismatch_is_not_silently_used():
    with pytest.raises(ValueError, match='WGS84'):
        mapping.nearby_buildings(fix(), coordinate_system='GCJ02')


def test_importer_requires_explicit_number_and_keeps_source():
    from tools.build_campus_buildings import extract_buildings
    import xml.etree.ElementTree as ET
    raw = ET.fromstring('''<osm><node id="1" lat="29.82" lon="106.42"/>
      <node id="2" lat="29.83" lon="106.42"/><node id="3" lat="29.83" lon="106.43"/>
      <way id="11" version="4"><nd ref="1"/><nd ref="2"/><nd ref="3"/><nd ref="1"/>
        <tag k="building" v="university"/><tag k="name" v="8教学楼"/></way>
      <way id="12"><nd ref="1"/><nd ref="2"/><nd ref="3"/><nd ref="1"/>
        <tag k="building" v="university"/><tag k="name" v="数学学院"/></way>
      <way id="13"><nd ref="1"/><nd ref="2"/><nd ref="3"/><nd ref="1"/>
        <tag k="building" v="university"/><tag k="name" v="动物科技学院"/>
        <tag k="alt_name" v="西南大学37教学楼,宏朴楼"/></way></osm>''')
    result = extract_buildings(raw)
    assert [f['properties']['code'] for f in result['features']] == ['8', '37']
    assert result['features'][0]['properties']['osm_version'] == '4'
    assert result['features'][0]['properties']['source_url'].endswith('/way/11')


def test_numbered_relation_joins_reversed_segments_and_preserves_courtyard():
    from tools.build_campus_buildings import extract_buildings
    import xml.etree.ElementTree as ET
    raw = ET.fromstring('''<osm>
      <node id="1" lat="0" lon="0"/><node id="2" lat="0" lon="4"/>
      <node id="3" lat="4" lon="4"/><node id="4" lat="4" lon="0"/>
      <node id="5" lat="1" lon="1"/><node id="6" lat="1" lon="2"/>
      <node id="7" lat="2" lon="2"/><node id="8" lat="2" lon="1"/>
      <way id="a"><nd ref="1"/><nd ref="2"/><nd ref="3"/></way>
      <way id="b"><nd ref="1"/><nd ref="4"/><nd ref="3"/></way>
      <way id="c"><nd ref="5"/><nd ref="6"/><nd ref="7"/><nd ref="8"/><nd ref="5"/></way>
      <relation id="r"><member type="way" ref="b" role="outer"/>
        <member type="way" ref="a" role="outer"/><member type="way" ref="c" role="inner"/>
        <tag k="type" v="multipolygon"/><tag k="building" v="university"/>
        <tag k="name" v="31教学楼"/></relation></osm>''')
    result = extract_buildings(raw)['features']
    assert len(result) == 1
    assert result[0]['properties']['code'] == '31'
    assert len(result[0]['geometry']['coordinates']) == 2
    assert result[0]['properties']['source_url'].endswith('/relation/r')


def test_institution_correspondence_requires_exact_object_name_and_documentation():
    from tools.build_campus_buildings import extract_buildings
    import xml.etree.ElementTree as ET
    raw = ET.fromstring('''<osm><node id="1" lat="29" lon="106"/>
      <node id="2" lat="30" lon="106"/><node id="3" lat="30" lon="107"/>
      <way id="11"><nd ref="1"/><nd ref="2"/><nd ref="3"/><nd ref="1"/>
        <tag k="building" v="university"/><tag k="name" v="田家炳教育书院"/></way></osm>''')
    match = {'way:11': {'code': '11', 'expected_name': '田家炳教育书院',
        'evidence_url': 'https://www.swu.edu.cn/info/1153/2334.htm', 'reason': '官网明确楼名与楼号'}}
    assert len(extract_buildings(raw, correspondences=match)['features']) == 1
    match['way:11']['expected_name'] = '另一栋楼'
    with pytest.raises(ValueError, match='name'):
        extract_buildings(raw, correspondences=match)


def test_courtyard_is_not_inside_building_area():
    building = square()
    building['geometry']['coordinates'].append([
        [106.41997,29.82497],[106.42003,29.82497],[106.42003,29.82503],
        [106.41997,29.82503],[106.41997,29.82497]])
    assert mapping._distance(building,29.825,106.42) > 2
