"""Import a saved public OSM response, retaining explicitly numbered footprints."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import xml.etree.ElementTree as ET


def _join_rings(parts):
    """Join OSM member ways, allowing reverse direction; reject incomplete chains."""
    pending = [list(part) for part in parts]
    rings = []
    while pending:
        ring = pending.pop(0)
        while ring and ring[-1] != ring[0]:
            for i, part in enumerate(pending):
                if part[0] == ring[-1]:
                    ring.extend(part[1:])
                elif part[-1] == ring[-1]:
                    ring.extend(reversed(part[:-1]))
                else:
                    continue
                pending.pop(i)
                break
            else:
                return None
        if len(ring) < 4:
            return None
        rings.append(ring)
    return rings


def extract_buildings(root, correspondences=None):
    correspondences = correspondences or {}
    nodes = {n.attrib['id']: [float(n.attrib['lon']), float(n.attrib['lat'])]
             for n in root.findall('node')}
    ways = {w.attrib['id']: w for w in root.findall('way')}
    features = []

    def add(element, rings):
        tags = {t.attrib['k']: t.attrib['v'] for t in element.findall('tag')}
        key = element.tag + ':' + element.attrib['id']
        evidence = correspondences.get(key)
        explicit = ' '.join(tags.get(k, '') for k in ('name', 'alt_name'))
        codes = {str(int(v)) for v in re.findall(r'(\d+)\s*教学楼', explicit)}
        if evidence:
            if tags.get('name') != evidence['expected_name']:
                raise ValueError('OSM name changed for ' + key)
            if not evidence.get('evidence_url') or not evidence.get('reason'):
                raise ValueError('Missing correspondence evidence for ' + key)
            if codes and codes != {evidence['code']}:
                raise ValueError('Conflicting explicit number for ' + key)
            codes = {evidence['code']}
        if len(codes) != 1 or tags.get('building') not in ('university', 'yes', 'school'):
            return
        code = codes.pop()
        if any(f['properties']['code'] == code for f in features):
            return
        if not rings or any(ref not in nodes for ring in rings for ref in ring):
            return
        props = {'campus': 'beibei', 'code': code, 'name': tags.get('name', ''),
                 'aliases': list(dict.fromkeys([code, code.zfill(2), f'{code}教',
                     f'{code.zfill(2)}教', f'{code}教学楼', tags.get('name', '')])),
                 'source_url': f"https://www.openstreetmap.org/{element.tag}/{element.attrib['id']}",
                 'osm_version': element.attrib.get('version'),
                 'osm_updated_at': element.attrib.get('timestamp'), 'osm_tags': tags,
                 'verification': 'public_map_unverified'}
        if evidence:
            props['correspondence'] = evidence
            if evidence.get('model_variants'):
                props['model_variants'] = evidence['model_variants']
        features.append({'type': 'Feature', 'id': f'beibei:{code}:{key}',
            'geometry': {'type': 'Polygon', 'coordinates': [[nodes[r] for r in ring] for ring in rings]},
            'properties': props})

    for way in ways.values():
        refs = [nd.attrib['ref'] for nd in way.findall('nd')]
        if len(refs) >= 4 and refs[0] == refs[-1]:
            add(way, [refs])
    for relation in root.findall('relation'):
        parts = {'outer': [], 'inner': []}
        valid = True
        for member in relation.findall('member'):
            if member.attrib.get('type') != 'way':
                continue
            role = member.attrib.get('role') or 'outer'
            if role not in parts or member.attrib['ref'] not in ways:
                valid = False
                break
            parts[role].append([n.attrib['ref'] for n in ways[member.attrib['ref']].findall('nd')])
        if not valid:
            continue
        outer, inner = _join_rings(parts['outer']), _join_rings(parts['inner'])
        # Multiple disjoint exteriors need MultiPolygon support; never silently discard one.
        if outer and len(outer) == 1 and inner is not None:
            add(relation, outer + inner)
    features.sort(key=lambda f: (int(f['properties']['code']), f['id']))
    return {'type': 'FeatureCollection', 'metadata': {
        'coordinate_system': 'WGS84', 'campus': 'SWU Beibei',
        'generated_at': datetime.now(timezone.utc).isoformat(),
        'source_snapshot': 'Saved OSM response; generation does not refresh source geometry',
        'source_url': 'https://api.openstreetmap.org/api/0.6/map?bbox=106.411,29.813,106.436,29.841',
        'attribution': '© OpenStreetMap contributors', 'license': 'ODbL-1.0',
        'license_url': 'https://www.openstreetmap.org/copyright',
        'verification': 'Community map and documented building correspondence; not a campus survey',
    }, 'features': features}


def extract_context(root):
    """Background only: unnumbered geometry is never promoted to a teaching building."""
    nodes={n.attrib['id']:[float(n.attrib['lon']),float(n.attrib['lat'])] for n in root.findall('node')}
    features=[]
    for way in root.findall('way'):
        tags={t.attrib['k']:t.attrib['v'] for t in way.findall('tag')}
        kind=('water' if tags.get('natural')=='water' or 'water' in tags else
              'road' if 'highway' in tags else 'building' if 'building' in tags else
              'green' if tags.get('leisure') in ('park','garden','pitch') else None)
        if kind is None:continue
        refs=[n.attrib['ref'] for n in way.findall('nd')]
        if len(refs)<2 or any(r not in nodes for r in refs):continue
        polygon=kind!='road'
        if polygon and (len(refs)<4 or refs[0]!=refs[-1]):continue
        coords=[nodes[r] for r in refs]
        features.append({'type':'Feature','geometry':{'type':'Polygon' if polygon else 'LineString',
            'coordinates':[coords] if polygon else coords},'properties':{
            'kind':kind,'name':tags.get('name',''),'highway':tags.get('highway',''),
            'source_url':f"https://www.openstreetmap.org/way/{way.attrib['id']}"}})
    metadata=extract_buildings(root)['metadata']
    metadata['generated_at']=datetime.now(timezone.utc).isoformat()
    metadata['source_snapshot']='Saved OSM response; generation does not refresh source geometry'
    metadata['description']='Context geometry only; not verified building-number mappings'
    return {'type':'FeatureCollection','metadata':metadata,'features':features}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('input', type=Path)
    parser.add_argument('--output', type=Path, default=Path('app/data/swu_beibei_buildings.geojson'))
    parser.add_argument('--context',action='store_true',help='Export background geometry instead of numbered buildings')
    parser.add_argument('--correspondences', type=Path, help='Reviewed object-to-building correspondence manifest')
    args = parser.parse_args()
    root = ET.parse(args.input).getroot()
    data = extract_context(root) if args.context else extract_buildings(root, json.loads(args.correspondences.read_text(encoding='utf-8')) if args.correspondences else None)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(data, ensure_ascii=False, indent=None if args.context else 2,
                                     separators=(',',':') if args.context else None) + '\n', encoding='utf-8')
    print('Imported features:',len(data['features']))


if __name__ == '__main__':
    main()
