"""Loopback-only offline review of campus mapping; never imports Flask or a database."""
import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import sys
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.services.campus_buildings import load_buildings, nearby_buildings, representative_point


def replay_cases():
    data = load_buildings()
    indexed = {f['properties']['code']: f for f in data['features']}
    points = {code: representative_point(indexed[code]) for code in ('8', '25')}
    cases = []
    for label, code, accuracy in [('8教现场', '8', 35), ('8教偏到25教', '25', 35),
                                  ('25教附近・误差200米', '25', 200),
                                  ('25教附近・误差800米', '25', 800)]:
        lon, lat = points[code]
        location = {'latitude': lat, 'longitude': lon, 'accuracy': accuracy}
        cases.append({'label': label, 'input': location, 'result': nearby_buildings(location)})
    for label, location in [('校外位置', {'latitude':29.7,'longitude':106.42,'accuracy':35}),
                             ('定位失败', None),
                             ('定位精度不足', {'latitude':29.825,'longitude':106.42,'accuracy':2000})]:
        cases.append({'label':label,'input':location,'result':nearby_buildings(location)})
    return cases


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        path = urlparse(self.path)
        content_type = 'application/json; charset=utf-8'
        if path.path == '/':
            body = (ROOT / 'tools' / 'campus_mapping_preview.html').read_bytes()
            content_type = 'text/html; charset=utf-8'
        elif path.path == '/dataset.json':
            body = json.dumps(load_buildings(), ensure_ascii=False).encode('utf-8')
        elif path.path == '/replay.json':
            body = json.dumps(replay_cases(), ensure_ascii=False).encode('utf-8')
        elif path.path == '/nearby':
            q = parse_qs(path.query)
            try:
                location = {key: float(q[key][0]) for key in ('latitude','longitude','accuracy')}
            except (ValueError, KeyError):
                location = None
            result = nearby_buildings(location, campus=q.get('campus',['beibei'])[0])
            body = json.dumps(result, ensure_ascii=False).encode('utf-8')
        else:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(body)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=5098)
    parser.add_argument('--replay-output', type=Path)
    args = parser.parse_args()
    if args.replay_output:
        args.replay_output.parent.mkdir(parents=True, exist_ok=True)
        args.replay_output.write_text(json.dumps(replay_cases(), ensure_ascii=False, indent=2),encoding='utf-8')
        print(args.replay_output)
        return
    server = ThreadingHTTPServer(('127.0.0.1', args.port), Handler)
    print(f'Campus mapping preview: http://127.0.0.1:{args.port}', flush=True)
    server.serve_forever()


if __name__ == '__main__':
    main()
