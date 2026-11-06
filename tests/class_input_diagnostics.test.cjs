const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('app/static/js/class-input-diagnostics.js', 'utf8');

function fixture(ErrorConstructor = Error) {
  const ready = [], controls = [], copied = [];
  function Input() { this.stored = ''; this.listeners = {}; }
  Object.defineProperty(Input.prototype, 'value', {
    get() { return this.stored; }, set(value) { this.stored = String(value); },
  });
  Input.prototype.addEventListener = function(type, callback) { this.listeners[type] = callback; };
  Input.prototype.insertAdjacentElement = function(_where, element) { controls.push(element); };
  const input = new Input();
  const window = {HTMLInputElement: Input,
    location: {pathname: '/user/submit_form', search: '?class_trace=1&secret=omit'},
    navigator: {userAgent: 'Test Browser', clipboard: {writeText: async text => copied.push(text)}},
    document: {readyState: 'loading', documentElement: {},
      getElementById(id) { assert.equal(id, 'student_grade_class'); return input; },
      addEventListener(type, callback) { if (type === 'DOMContentLoaded') ready.push(callback); },
      createElement() { return {children: [], listeners: {},
        append(...children) { this.children.push(...children); },
        addEventListener(type, callback) { this.listeners[type] = callback; }}; },
    },
    MutationObserver: class { observe() {} disconnect() {} },
  };
  vm.runInNewContext(source, {window, Date, Error: ErrorConstructor, Object, String});
  return {input, ready: () => ready.forEach(callback => callback()), controls, copied,
    snapshot: () => window.SwuClassInputDiagnostics.snapshot(),
    nativeSet: value => Object.getOwnPropertyDescriptor(Input.prototype, 'value').set.call(input, value)};
}

test('diagnostics observe script writes and native input without changing their values', () => {
  const f = fixture();
  f.input.value = '2024级农林经济管理01班';
  assert.equal(f.input.value, '2024级农林经济管理01班');
  const scripted = f.snapshot().events.find(event => event.kind === 'script-value');
  assert.equal(scripted.before, '');
  assert.equal(scripted.after, f.input.value);
  assert.ok(scripted.stack.length);
  f.nativeSet('2024级农林经济01班');
  f.input.listeners.input({isTrusted: true, inputType: 'insertText'});
  assert.equal(f.snapshot().events.at(-1).trusted, true);
  assert.equal(f.snapshot().events.at(-1).value, '2024级农林经济01班');
});

test('diagnostic records are bounded and omit URL query strings', async () => {
  const f = fixture();
  for (let i = 0; i < 100; i++) f.input.value = '班'.repeat(2000) + i;
  assert.equal(f.input.value.length, 2002);
  const result = f.snapshot();
  assert.equal(result.events.length, 80);
  assert.equal(result.value.length, 256);
  assert.equal(result.value_length, 2002);
  assert.equal(result.events.at(-1).after_length, 2002);
  assert.equal(result.events.at(-1).after.length, 256);
  assert.equal(JSON.stringify(result).includes('secret'), false);
  assert.equal(f.copied.length, 0);
  f.ready();
  await f.controls[0].children[1].listeners.click();
  assert.equal(f.copied.length, 1);
  assert.equal(JSON.parse(f.copied[0]).field, 'student_grade_class');
});

test('diagnostics are opt-in and perform no network or storage writes', () => {
  const template = fs.readFileSync('app/templates/user/lecture_form.html', 'utf8');
  assert.match(template, /if request\.args\.get\('class_trace'\) == '1'[^\n]+class-input-diagnostics\.js/);
  assert.doesNotMatch(source, /\bfetch\s*\(|XMLHttpRequest|sendBeacon|localStorage|sessionStorage/);
});

test('stack redaction removes page queries while retaining the writer line and column', () => {
  class BrowserError {
    constructor() { this.stack = 'Error\n at value\n at https://tb.yoouzic.top/user/submit_form?secret=omit:123:45'; }
  }
  const f = fixture(BrowserError);
  f.input.value = '2024级农林经济01班';
  assert.equal(f.snapshot().events.at(-1).stack[0], ' at https://tb.yoouzic.top/user/submit_form:123:45');
});
