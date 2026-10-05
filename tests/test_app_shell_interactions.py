from pathlib import Path
import json
import shutil
import subprocess
import unittest


class AppShellInteractionTest(unittest.TestCase):
    def test_mobile_drawer_manages_focus_background_and_scroll_state(self):
        node = shutil.which('node')
        self.assertIsNotNone(node, 'Node.js is required for the app shell interaction test')
        script = Path('app/static/js/app-shell.js').read_text(encoding='utf-8')
        harness = f"""
const assert = require('assert');
const documentEvents = {{}};

function target(name) {{
  const classes = new Set();
  return {{
    name,
    events: {{}},
    attrs: {{}},
    inert: false,
    classList: {{
      add: (value) => classes.add(value),
      remove: (value) => classes.delete(value),
      contains: (value) => classes.has(value),
      toggle: (value, force) => force === undefined
        ? (classes.has(value) ? (classes.delete(value), false) : (classes.add(value), true))
        : (force ? classes.add(value) : classes.delete(value), force),
    }},
    setAttribute(key, value) {{ this.attrs[key] = String(value); }},
    removeAttribute(key) {{ delete this.attrs[key]; }},
    getAttribute(key) {{ return this.attrs[key]; }},
    addEventListener(type, handler) {{ this.events[type] = handler; }},
    focus() {{ document.activeElement = this; }},
  }};
}}

const toggle = target('toggle');
const firstLink = target('first-link');
const lastLink = target('last-link');
const backdrop = target('backdrop');
const frame = target('frame');
const sidebar = target('sidebar');
sidebar.querySelector = () => firstLink;
sidebar.querySelectorAll = (selector) => selector === 'a[href]' ? [firstLink, lastLink] : [firstLink, lastLink];

const shell = target('shell');
shell.querySelector = (selector) => ({{
  '[data-shell-toggle]': toggle,
  '.app-sidebar': sidebar,
  '.app-frame': frame,
}}[selector] || null);
shell.querySelectorAll = (selector) => selector === '[data-shell-close]' ? [backdrop] : [];

const userMenu = target('user-menu');
const userToggle = target('user-toggle');
const userLink = target('user-link');
userMenu.querySelector = () => userToggle;
userMenu.contains = (element) => element === userLink || element === userToggle;
const body = target('body');
global.document = {{
  body,
  activeElement: toggle,
  addEventListener(type, handler) {{ documentEvents[type] = handler; }},
  querySelector(selector) {{ return selector === '.app-shell' ? shell : null; }},
  querySelectorAll(selector) {{ return selector === '.app-user-menu' ? [userMenu] : []; }},
  getElementById() {{ return null; }},
}};
const media = {{ matches: true, addEventListener(type, fn) {{ this.changed = fn; }} }};
global.window = {{ matchMedia: () => media }};

eval({json.dumps(script)});
documentEvents.DOMContentLoaded();

toggle.events.click();
assert.equal(shell.classList.contains('is-nav-open'), true);
assert.equal(toggle.getAttribute('aria-expanded'), 'true');
assert.equal(body.classList.contains('is-shell-nav-open'), true);
assert.equal(frame.inert, true);
assert.equal(frame.getAttribute('aria-hidden'), 'true');
assert.equal(document.activeElement, firstLink);

document.activeElement = lastLink;
let tabPrevented = false;
documentEvents.keydown({{ key: 'Tab', shiftKey: false, preventDefault() {{ tabPrevented = true; }} }});
assert.equal(tabPrevented, true);
assert.equal(document.activeElement, firstLink);

documentEvents.keydown({{ key: 'Escape', preventDefault() {{}} }});
assert.equal(shell.classList.contains('is-nav-open'), false);
assert.equal(toggle.getAttribute('aria-expanded'), 'false');
assert.equal(body.classList.contains('is-shell-nav-open'), false);
assert.equal(frame.inert, false);
assert.equal(frame.getAttribute('aria-hidden'), undefined);
assert.equal(document.activeElement, toggle);

toggle.events.click();
media.matches = false;
if (media.changed) media.changed(media);
assert.equal(frame.inert, false, 'desktop content must unlock after crossing the nav breakpoint');
assert.equal(body.classList.contains('is-shell-nav-open'), false);
assert.equal(shell.classList.contains('is-nav-open'), false);

document.activeElement = userLink;
userMenu.events['hidden.bs.dropdown']?.();
assert.equal(document.activeElement, userToggle, 'closing the user menu restores keyboard focus');
document.activeElement = toggle;
userMenu.events['hidden.bs.dropdown']?.();
assert.equal(document.activeElement, toggle, 'outside clicks must not steal focus');
"""
        result = subprocess.run([node, '-e', harness], capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)


if __name__ == '__main__':
    unittest.main()
