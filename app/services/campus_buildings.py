"""WGS84 campus building suggestions, independent of application/database state."""
from __future__ import annotations

import json
import math
from pathlib import Path

DATA_PATH = Path(__file__).resolve().parents[1] / 'data' / 'swu_beibei_buildings.geojson'
EARTH_RADIUS_M = 6371000
NEIGHBOURHOOD_FLOOR_M = 250
ACCURACY_BUFFER_M = 50
MAX_USEFUL_ACCURACY_M = 1000


def load_buildings():
    """Load a local snapshot; no network call or user location transmission."""
    return json.loads(DATA_PATH.read_text(encoding='utf-8'))


def _project(point, latitude, longitude):
    lon, lat = point
    return (math.radians(lon - longitude) * EARTH_RADIUS_M * math.cos(math.radians(latitude)),
            math.radians(lat - latitude) * EARTH_RADIUS_M)


def _inside(points):
    inside = False
    for (ax, ay), (bx, by) in zip(points, points[1:] + points[:1]):
        if (ay > 0) != (by > 0) and 0 < ax + (bx - ax) * (-ay) / (by - ay):
            inside = not inside
    return inside


def _distance(feature, latitude, longitude):
    rings = [[_project(p, latitude, longitude) for p in ring]
             for ring in feature['geometry']['coordinates']]
    if _inside(rings[0]) and not any(_inside(hole) for hole in rings[1:]):
        return 0.0
    best = math.inf
    for points in rings:
        for (ax, ay), (bx, by) in zip(points, points[1:] + points[:1]):
            dx, dy = bx - ax, by - ay
            norm = dx * dx + dy * dy
            t = min(1, max(0, -(ax * dx + ay * dy) / norm)) if norm else 0
            best = min(best, math.hypot(ax + t * dx, ay + t * dy))
    return best


def representative_point(feature):
    """Return a reproducible point in/on the footprint for labelled replay presets."""
    ring = feature['geometry']['coordinates'][0][:-1]
    lon = sum(p[0] for p in ring) / len(ring)
    lat = sum(p[1] for p in ring) / len(ring)
    return (lon, lat) if _distance(feature, lat, lon) < 1e-6 else tuple(ring[0])


def nearby_buildings(location, *, campus='beibei', allowed_buildings=None,
                     coordinate_system='WGS84', dataset=None):
    """Return *all* nearby suggestions; location never confirms or excludes a course.

    Distances use a local equirectangular projection centred on the observation,
    appropriate for this campus-sized snapshot. The neighbourhood floor is a
    provisional UI buffer, not a measured confidence interval.
    """
    if coordinate_system != 'WGS84':
        raise ValueError('location and building dataset must both use WGS84')
    data = load_buildings() if dataset is None else dataset
    features = [f for f in data['features'] if f['properties']['campus'] == campus]
    mapped_codes = {f['properties']['code'] for f in features}
    allowed = None if allowed_buildings is None else {
        str(int(str(v))) if str(v).isdigit() else str(v) for v in allowed_buildings
    }
    result = {'status': 'location_unavailable', 'campus': campus,
              'coordinate_system': 'WGS84', 'radius_m': None, 'buildings': [],
              'requires_confirmation': True,
              'unmapped_buildings': sorted((allowed or set()) - mapped_codes)}
    if not isinstance(location, dict):
        return result
    bounds = {'latitude': (-90, 90), 'longitude': (-180, 180), 'accuracy': (0, 100000)}
    for key, (lo, hi) in bounds.items():
        value = location.get(key)
        if (type(value) not in (float, int) or not math.isfinite(value)
                or not lo <= value <= hi or (key == 'accuracy' and value <= 0)):
            return result
    if location['accuracy'] > MAX_USEFUL_ACCURACY_M:
        result['status'] = 'location_too_coarse'
        return result
    if not features:
        result['status'] = 'campus_unmapped'
        return result
    radius = max(NEIGHBOURHOOD_FLOOR_M, location['accuracy'] + ACCURACY_BUFFER_M)
    result['radius_m'] = radius
    by_code = {}
    for feature in features:
        props = feature['properties']
        if allowed is not None and props['code'] not in allowed:
            continue
        distance = _distance(feature, location['latitude'], location['longitude'])
        if distance > radius:
            continue
        item = {key: props[key] for key in ('code', 'name', 'source_url', 'verification')}
        item['distance_m'] = round(distance, 1)
        if item['code'] not in by_code or item['distance_m'] < by_code[item['code']]['distance_m']:
            by_code[item['code']] = item
    result['buildings'] = sorted(by_code.values(), key=lambda b: (b['distance_m'], int(b['code'])))
    result['status'] = 'ok' if result['buildings'] else 'no_mapped_building_nearby'
    return result
