import html
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


def luminance(color):
    channels = [int(value) / 255 for value in re.findall(r'\d+', color)[:3]]
    linear = [value / 12.92 if value <= .04045 else ((value + .055) / 1.055) ** 2.4 for value in channels]
    return sum(value * weight for value, weight in zip(linear, (.2126, .7152, .0722)))


def contrast(foreground, background):
    light, dark = sorted((luminance(foreground), luminance(background)), reverse=True)
    return (light + .05) / (dark + .05)


class CampusThemeBrowserTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        browser = os.environ.get('UI_TEST_BROWSER') or next((str(path) for path in (
            Path('C:/Program Files/Google/Chrome/Application/chrome.exe'),
            Path('C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe'),
        ) if path.is_file()), None) or shutil.which('chromium') or shutil.which('google-chrome')
        if not browser:
            raise unittest.SkipTest('Set UI_TEST_BROWSER to a Chromium browser executable')
        bootstrap = (ROOT / 'app/static/vendor/bootstrap/css/bootstrap.min.css').as_uri()
        stylesheet = (ROOT / 'app/static/css/style.css').as_uri()
        service_template = (ROOT / 'app/templates/admin/_settings_automation.html').read_text(encoding='utf-8')
        service_script = (ROOT / 'app/static/js/automation-center.js').read_text(encoding='utf-8')
        initial_badge = re.search(r'class="([^"]+)" data-service-value', service_template).group(1)
        runtime_badge = re.search(r'badge.className = `([^`]+)`', service_script).group(1).replace('${variant}', 'success')
        mobile_fixture = ('<!doctype html><html><head><link rel="stylesheet" href="BOOTSTRAP">'
                          '<link rel="stylesheet" href="STYLESHEET"></head><body><main class="app-content">'
                          '<section class="workspace-metrics">' +
                          ''.join('<div class="metric metric--plain workspace-metric"><strong>0</strong>'
                                  '<span>工作指标</span></div>' for _ in range(4)) +
                          '</section></main></body></html>')
        fixture = '''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<link rel="stylesheet" href="BOOTSTRAP"><link rel="stylesheet" href="STYLESHEET">
</head><body><div class="app-shell"><aside class="app-sidebar">
<a class="app-nav__link" href="#">听课与填报</a></aside><div class="app-frame">
<main class="app-content"><div class="workspace"><header class="page-header"><h1>今日工作</h1></header>
<div class="workspace-grid"><section class="workspace-panel">优先处理</section></div></div>
<button class="btn btn-primary" id="primary">开始填报</button>
<span class="bg-primary text-white" id="badge">已选择</span>
<div class="surface-panel" id="surface">听课记录</div></main></div></div>
<div class="auth-layout"><section class="auth-intro"><span class="auth-kicker">教学信息中心</span>
<h1>认真听见，共同成长。</h1><p>校园听课工作台</p></section><section class="auth-panel">
<input class="form-control" id="account" placeholder="请输入账号"><button class="btn btn-primary">登录工作台</button>
<button class="btn btn-link" id="text-action">登录帮助</button><a id="text-link" href="#">登录帮助</a>
<button class="btn btn-outline-secondary" id="secondary">显示密码</button>
</section></div><table class="table table-sm"><tbody><tr><td id="compact-cell">课程</td></tr></tbody></table>
<div class="offcanvas offcanvas-end" id="drawer"></div>
<iframe id="mobile-probe" style="width:390px;height:500px;border:0" srcdoc="MOBILE_FIXTURE"></iframe>
<div class="card" id="round-card"><div class="card-header" id="round-header">部门</div><div class="card-body">成员</div><div class="card-footer" id="round-footer">操作</div></div>
<span id="service-initial" class="INITIAL_BADGE">检查中</span><span id="service-ready" class="RUNTIME_BADGE">ready</span>
<pre id="probe"></pre><script>
window.addEventListener('load', () => {
    const read = (selector, pseudo = null) => {
        const style = getComputedStyle(document.querySelector(selector), pseudo);
        return Object.fromEntries(['color', 'backgroundColor', 'backgroundImage', 'animationName',
            'animationDuration', 'animationDelay', 'animationIterationCount', 'transitionDuration', 'transform',
            'borderTopLeftRadius', 'borderBottomLeftRadius', 'overflow', 'pointerEvents', 'minHeight', 'opacity', 'paddingTop', 'paddingBottom'].map(key => [key, style[key]]));
    };
    const mobile = document.getElementById('mobile-probe').contentDocument;
    const metrics = [...mobile.querySelectorAll('.workspace-metric')].map(el => {
        const style = el.ownerDocument.defaultView.getComputedStyle(el);
        return {top: el.getBoundingClientRect().top, left: style.borderLeftWidth,
            right: style.borderRightWidth, paddingLeft: style.paddingLeft, paddingRight: style.paddingRight};
    });
    const before = read('.auth-intro h1');
    document.getAnimations().forEach(animation => animation.finish());
    const primary = document.getElementById('primary');
    const box = primary.getBoundingClientRect();
    const hit = document.elementFromPoint(box.x + box.width / 2, box.y + box.height / 2);
    document.getElementById('probe').textContent = JSON.stringify({
        serviceInitial: read('#service-initial'), serviceReady: read('#service-ready'), card: read('#round-card'), cardHeader: read('#round-header'), cardFooter: read('#round-footer'), sidebar: read('.app-sidebar'), nav: read('.app-nav__link'), primary: read('#primary'),
        badge: read('#badge'), surface: read('#surface'), account: read('#account'),
        headlineMotion: before, headlineAfter: read('.auth-intro h1'),
        supportingMotion: read('.auth-intro > p'),
        intro: read('.auth-intro'), texture: read('.auth-intro', '::before'),
        main: read('.app-content'), clickable: hit === primary || primary.contains(hit),
        accountHeight: document.getElementById('account').getBoundingClientRect().height,
        placeholder: read('#account', '::placeholder'), textAction: read('#text-action'),
        textLink: read('#text-link'), compactCell: read('#compact-cell'), drawer: read('#drawer'),
        metrics, mobileWidth: mobile.documentElement.clientWidth,
        reduced: matchMedia('(prefers-reduced-motion: reduce)').matches
    });
});
</script></body></html>'''.replace('MOBILE_FIXTURE', html.escape(mobile_fixture, quote=True)).replace('BOOTSTRAP', bootstrap).replace('STYLESHEET', stylesheet).replace('INITIAL_BADGE', initial_badge).replace('RUNTIME_BADGE', runtime_badge)
        cls.results = {}
        for reduced in (False, True):
            with tempfile.TemporaryDirectory(prefix='swu-ui-test-', ignore_cleanup_errors=True) as folder:
                page = Path(folder) / 'fixture.html'
                page.write_text(fixture, encoding='utf-8')
                command = [browser, '--headless', '--no-first-run', '--disable-background-networking',
                           '--disable-extensions', '--disable-sync', '--window-size=1280,1000',
                           '--virtual-time-budget=1000', f'--user-data-dir={folder}/profile', '--dump-dom']
                if reduced:
                    command.append('--force-prefers-reduced-motion')
                result = subprocess.run(command + [page.as_uri()], capture_output=True, timeout=45)
                output = result.stdout.decode('utf-8', errors='replace')
                match = re.search(r'<pre id="probe">(.*?)</pre>', output, re.S)
                if result.returncode or not match or not match.group(1):
                    raise RuntimeError('Browser probe failed: ' + result.stderr.decode('utf-8', errors='replace')[-1200:])
                cls.results[reduced] = json.loads(html.unescape(match.group(1)))

    def test_light_navigation_keeps_readable_link_contrast(self):
        result = self.results[False]
        self.assertGreater(luminance(result['sidebar']['backgroundColor']), .7)
        self.assertGreaterEqual(contrast(result['nav']['color'], result['sidebar']['backgroundColor']), 4.5)

    def test_primary_buttons_and_legacy_badges_keep_text_contrast(self):
        for selector in ('primary', 'badge'):
            with self.subTest(component=selector):
                style = self.results[False][selector]
                self.assertGreaterEqual(contrast(style['color'], style['backgroundColor']), 4.5)

    def test_login_controls_have_comfortable_hit_targets(self):
        self.assertGreaterEqual(self.results[False]['accountHeight'], 44)

    def test_entrance_is_short_finite_and_leaves_content_in_place(self):
        result = self.results[False]
        motion = result['headlineMotion']
        self.assertNotEqual(motion['animationName'], 'none')
        self.assertGreater(float(motion['animationDuration'].removesuffix('s')), 0)
        self.assertLessEqual(float(motion['animationDuration'].removesuffix('s')), .5)
        self.assertEqual(motion['animationIterationCount'], '1')
        self.assertEqual(result['headlineAfter']['transform'], 'none')
        self.assertEqual(result['main']['transform'], 'none')

    def test_reduced_motion_disables_visible_entrance_and_control_transitions(self):
        result = self.results[True]
        self.assertTrue(result['reduced'])
        for key in ('headlineMotion', 'supportingMotion', 'primary'):
            for duration in result[key]['transitionDuration'].split(',') + result[key]['animationDuration'].split(','):
                self.assertLessEqual(float(duration.strip().removesuffix('s')), .001)
            self.assertEqual(result[key]['animationDelay'], '0s')

    def test_placeholder_keeps_readable_contrast_on_paper_inputs(self):
        result = self.results[False]
        placeholder = result['placeholder']
        foreground = [int(value) for value in re.findall(r'\d+', placeholder['color'])[:3]]
        background = [int(value) for value in re.findall(r'\d+', result['account']['backgroundColor'])[:3]]
        opacity = float(placeholder['opacity'])
        blended = [round(front * opacity + back * (1 - opacity)) for front, back in zip(foreground, background)]
        self.assertGreaterEqual(contrast('rgb(%s)' % ','.join(map(str, blended)), result['account']['backgroundColor']), 4.5)

    def test_text_actions_use_the_same_theme_as_links(self):
        self.assertEqual(self.results[False]['textAction']['color'], self.results[False]['textLink']['color'])

    def test_compact_tables_keep_compact_cell_spacing(self):
        for side in ('paddingTop', 'paddingBottom'):
            self.assertLessEqual(float(self.results[False]['compactCell'][side].removesuffix('px')), 4)

    def test_drawers_use_short_transitions_with_the_shipped_bootstrap(self):
        duration = float(self.results[False]['drawer']['transitionDuration'].removesuffix('s'))
        self.assertGreater(duration, 0)
        self.assertLessEqual(duration, .28)

    def test_mobile_metric_cards_keep_two_columns_and_balanced_edges(self):
        result = self.results[False]
        self.assertEqual(result['mobileWidth'], 390)
        metrics = result['metrics']
        self.assertEqual(len(metrics), 4)
        self.assertEqual(metrics[0]['top'], metrics[1]['top'])
        self.assertGreater(metrics[2]['top'], metrics[0]['top'])
        self.assertEqual(metrics[2]['top'], metrics[3]['top'])
        for metric in metrics:
            self.assertEqual(metric['left'], metric['right'])
            self.assertEqual(metric['paddingLeft'], metric['paddingRight'])
            self.assertGreater(float(metric['left'].removesuffix('px')), 0)

    def test_paper_cards_keep_controls_clear_and_clickable(self):
        result = self.results[False]
        self.assertEqual(result['texture']['pointerEvents'], 'none')
        self.assertNotEqual(result['texture']['backgroundImage'], 'none')
        self.assertIn('cotton-paper.webp', result['surface']['backgroundImage'])
        self.assertEqual(result['account']['backgroundImage'], 'none')
        self.assertEqual(result['primary']['backgroundImage'], 'none')
        self.assertTrue(result['clickable'])

    def test_card_sections_follow_surface_radius_without_clipping_menus(self):
        result = self.results[False]
        radius = float(result['card']['borderTopLeftRadius'].removesuffix('px'))
        self.assertGreaterEqual(float(result['cardHeader']['borderTopLeftRadius'].removesuffix('px')), radius - 1)
        self.assertGreaterEqual(float(result['cardFooter']['borderBottomLeftRadius'].removesuffix('px')), radius - 1)
        self.assertEqual(result['card']['overflow'], 'visible')

    def test_automation_badges_use_supported_readable_status_backgrounds(self):
        for key in ('serviceInitial', 'serviceReady'):
            style = self.results[False][key]
            self.assertNotEqual(style['backgroundColor'], 'rgba(0, 0, 0, 0)')
            self.assertGreaterEqual(contrast(style['color'], style['backgroundColor']), 4.5)


if __name__ == '__main__':
    unittest.main()
