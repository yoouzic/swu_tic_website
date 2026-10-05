"""The capture stylesheet must preserve the shared shell at every breakpoint."""
import html
import json
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class SiteCaptureThemeBrowserTest(unittest.TestCase):
    def test_capture_inherits_shared_shell_and_controls(self):
        browser = next((str(p) for p in (
            Path('C:/Program Files/Google/Chrome/Application/chrome.exe'),
            Path('C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe'),
        ) if p.is_file()), None) or shutil.which('chromium')
        if not browser:
            self.skipTest('Chromium is required')
        links = ''.join(f'<link rel="stylesheet" href="{(ROOT / p).as_uri()}">' for p in (
            'app/static/vendor/bootstrap/css/bootstrap.min.css',
            'app/static/css/style.css', 'app/static/css/listening-assistant.css',
            'app/static/css/site-capture.css'))
        fixture = '''<!doctype html><meta charset="utf-8">LINKS
<body><div class="app-shell"><aside class="app-sidebar">导航</aside>
<div class="app-frame"><header class="app-topbar">听课工作台</header>
<main class="app-content"><header class="page-header"><h1>填写听课表单</h1></header>
<section class="card site-capture"><div class="card-body"><h2>现场听课记录</h2>
<button class="btn btn-primary">拍门牌</button></div></section></main></div></div>
<pre id="probe"></pre><script>
onload = () => {
  const read = () => Object.fromEntries(['body','.app-shell','.app-sidebar',
    '.app-topbar','.app-content','.page-header','.btn-primary'].map(selector => {
      const s = getComputedStyle(document.querySelector(selector));
      return [selector, Object.fromEntries(['backgroundColor','backgroundImage','color',
        'gridTemplateColumns','width','maxWidth','paddingTop','paddingBottom','position',
        'top','borderRadius','marginBottom'].map(k => [k,s[k]]))];
    }));
  const baseline = read();
  document.body.className = 'lecture-form-page';
  document.getElementById('probe').textContent = JSON.stringify({baseline, capture: read()});
};</script>'''.replace('LINKS', links)
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as folder:
            page = Path(folder) / 'fixture.html'
            page.write_text(fixture, encoding='utf-8')
            for width in (1440, 1024, 768, 390):
                result = subprocess.run([browser, '--headless', '--no-first-run',
                    '--disable-background-networking', '--disable-extensions', '--disable-sync',
                    f'--user-data-dir={folder}/profile', f'--window-size={width},900',
                    '--virtual-time-budget=1000', '--dump-dom', page.as_uri()],
                    capture_output=True, timeout=45)
                output = result.stdout.decode('utf-8', errors='replace')
                match = re.search(r'<pre id="probe">(.*?)</pre>', output, re.S)
                self.assertIsNotNone(match, result.stderr.decode('utf-8', errors='replace')[-500:])
                probe = json.loads(html.unescape(match.group(1)))
                for selector, baseline in probe['baseline'].items():
                    with self.subTest(width=width, selector=selector):
                        self.assertEqual(baseline, probe['capture'][selector])
