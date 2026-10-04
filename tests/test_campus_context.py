import xml.etree.ElementTree as ET
from tools import build_campus_buildings as build


def test_context_keeps_roads_and_landmarks_without_inventing_building_codes():
    root=ET.fromstring('''<osm><node id="1" lon="106.42" lat="29.82"/>
    <node id="2" lon="106.421" lat="29.82"/><node id="3" lon="106.42" lat="29.821"/>
    <way id="4"><nd ref="1"/><nd ref="2"/><tag k="highway" v="footway"/><tag k="name" v="校园路"/></way>
    <way id="5"><nd ref="1"/><nd ref="2"/><nd ref="3"/><nd ref="1"/><tag k="building" v="yes"/><tag k="name" v="某学院"/></way>
    <way id="6"><nd ref="1"/><nd ref="999"/><tag k="highway" v="path"/></way></osm>''')
    assert hasattr(build,'extract_context')
    result=build.extract_context(root)
    assert len(result['features'])==2
    assert result['features'][0]['geometry']['type']=='LineString'
    assert result['features'][1]['properties']['kind']=='building'
    assert all('code' not in f['properties'] for f in result['features'])
    assert result['metadata']['coordinate_system']=='WGS84'
