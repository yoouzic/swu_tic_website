const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const html = fs.readFileSync('app/templates/user/lecture_form.html', 'utf8');
const raw = '2025机制01班;2025机制02班;2025机制03班;2025农机班';

function fixture(value = '') {
  const inputTag = html.match(/<input[^>]*id="student_grade_class"[^>]*>/)[0];
  const listeners = new Map();
  const ready = [];
  const form = {};
  const input = {value, dataset: /data-format-url=/.test(inputTag) ? {formatUrl: '/format'} : {},
    addEventListener(type, callback) {
      if (!listeners.has(type)) listeners.set(type, []);
      listeners.get(type).push(callback);
    },
    dispatchEvent(event) { for (const callback of listeners.get(event.type) || []) callback(event); },
  };
  let requests = 0;
  const window = {
    document: {readyState: 'loading', getElementById: id => id === 'student_grade_class' ? input : id === 'lectureForm' ? form : null,
      addEventListener(type, callback) { if (type === 'DOMContentLoaded') ready.push(callback); }},
    fetch: async () => { requests++; return {ok: true, json: async () => ({success: true, value: '2025级机械设计制造及其自动化01、02、03班；2025级农业机械化及其自动化班'})}; },
    Event: class { constructor(type) { this.type = type; } },
  };
  const context = {window, document: window.document, lectureScheduleReady: false, lectureRegistrationReady: false,
    updateDateWithWeekday() {}, updateClassPeriod() {},
    FormData: class { forEach(callback) { callback(input.value, 'student_grade_class'); } },
  };
  vm.createContext(context);
  if (/filename='js\/student-grade-class-input\.js'/.test(html)) {
    vm.runInContext(fs.readFileSync('app/static/js/student-grade-class-input.js', 'utf8'), context);
  }
  vm.runInContext(html.slice(html.indexOf('function parseAssistantPayloadObject(value)'),
                            html.indexOf('function applyLectureFormDraftData(data)')), context);
  return {input, context, requests: () => requests,
    async ready() { for (const callback of ready) callback(); await new Promise(setImmediate); },
    async blur() { input.dispatchEvent({type: 'blur'}); await new Promise(setImmediate); },
  };
}

test('new form preserves mechanism class text at page initialization and blur without a formatter request', async () => {
  const f = fixture(raw); await f.ready(); await f.blur();
  assert.equal(f.requests(), 0);
  assert.equal(f.input.value, raw);
  assert.equal(f.context.collectLectureFormDraftData().student_grade_class, raw);
});

test('new manual class text is submitted unchanged by the browser after blur', async () => {
  const f = fixture(); await f.ready(); f.input.value = raw; await f.blur();
  assert.equal(f.requests(), 0);
  assert.equal(f.input.value, raw);
  assert.equal(f.context.collectLectureFormDraftData().student_grade_class, raw);
});

test('professional class field opts out of saved browser values and automatic formatting', () => {
  const inputTag = html.match(/<input[^>]*id="student_grade_class"[^>]*>/)[0];
  assert.match(inputTag, /autocomplete="off"/);
  assert.doesNotMatch(inputTag, /data-format-url=/);
  assert.doesNotMatch(html, /filename='js\/student-grade-class-input\.js'/);
});

function pendingDraft(value = '') {
  const input = {value};
  const revisionInput = {value: 'r0'};
  const statuses = [], timers = [];
  let complete;
  const context = {
    lectureFormIsEditMode: false, lectureFormDraftLoadSequence: 0, lectureFormDraftEditRevision: 0,
    lectureFormDraftSubmitting: false, lectureFormDraftDirty: false, lectureFormDraftTimer: null,
    lectureFormDraftUrl: '/draft', document: {getElementById: id =>
      id === 'student_grade_class' ? input : id === 'expected_draft_revision' ? revisionInput : null},
    fetch: () => new Promise(resolve => { complete = resolve; }),
    applyLectureFormDraftData: data => { input.value = data.student_grade_class; },
    window: {lectureFormDraftState: {revision: 'r0', epoch: 0, blocked: false, deleting: false, deletePromise: null},
      dispatchEvent() {}, clearTimeout() {}, setTimeout(callback, delay) { timers.push({callback, delay}); return timers.length; }},
    CustomEvent: class {}, saveLectureFormDraft() {},
    setLectureDraftStatus(message, level) { statuses.push({message, level}); },
    formatLectureDraftTime() { return ''; }, console,
  };
  vm.createContext(context);
  vm.runInContext(html.slice(html.indexOf('async function loadLectureFormDraft()'),
                            html.indexOf('async function saveLectureFormDraft()')), context);
  vm.runInContext(html.slice(html.indexOf('function scheduleLectureFormDraftSave()'),
                            html.indexOf('async function deleteLectureFormDraft()')), context);
  return {input, context, statuses, timers, load: options => context.loadLectureFormDraft(options),
    complete: (saved = '2025级机械设计制造及其自动化01班') => complete({ok: true, json: async () => ({success: true, exists: true, draft_revision: 'r1',
      data: {student_grade_class: saved}})}),
  };
}

test('pending new-form draft cannot overwrite class changes that emitted no input event', async () => {
  const f = pendingDraft(); const loading = f.load(); f.input.value = raw; f.complete();
  assert.equal(await loading, false);
  assert.equal(f.input.value, raw);
});

test('unchanged new form still restores its saved class draft', async () => {
  const f = pendingDraft(); const loading = f.load(); f.complete();
  assert.equal(await loading, true);
  assert.equal(f.input.value, '2025级机械设计制造及其自动化01班');
});

test('automatic initial draft load preserves class autofilled before the request', async () => {
  const f = pendingDraft(raw); const loading = f.load({preserveClassInput: true}); f.complete();
  assert.equal(await loading, true);
  assert.equal(f.input.value, raw);
  assert.match(html, /loadLectureFormDraft\(\{preserveClassInput:\s*true\}\)/);
});

test('preserving a class different from the saved draft schedules persistence and stays pending', async () => {
  const f = pendingDraft(raw); const loading = f.load({preserveClassInput: true}); f.complete();
  assert.equal(await loading, true);
  assert.equal(f.input.value, raw);
  assert.equal(f.context.lectureFormDraftDirty, true);
  assert.equal(f.timers.length, 1);
  assert.equal(f.timers[0].delay, 1500);
  assert.equal(f.statuses.at(-1).level, 'pending');
});

test('a preserved class already matching the saved draft does not trigger an extra save', async () => {
  const f = pendingDraft(raw); const loading = f.load({preserveClassInput: true}); f.complete(raw);
  assert.equal(await loading, true);
  assert.equal(f.context.lectureFormDraftDirty, false);
  assert.equal(f.timers.length, 0);
  assert.equal(f.statuses.at(-1).level, 'success');
});

test('explicit record loading can restore its class when there is no intervening edit', async () => {
  const f = pendingDraft(raw); const loading = f.load(); f.complete();
  assert.equal(await loading, true);
  assert.equal(f.input.value, '2025级机械设计制造及其自动化01班');
});

test('only fresh automatic loads preserve existing class input; error pages retain submitted data', () => {
  const start = html.lastIndexOf("document.addEventListener('DOMContentLoaded', function() {");
  const boot = html.slice(start, html.indexOf("    form.addEventListener('input'", start)) + '});';
  for (const hasSubmittedData of [false, true]) {
    const calls = [];
    const context = {lectureFormIsEditMode: false, document: {
      getElementById: () => ({}), addEventListener: (_event, callback) => callback(),
    }, loadLectureFormDraft: options => calls.push(options)};
    if (hasSubmittedData) context.initialFormData = {student_grade_class: raw};
    vm.runInNewContext(boot, context);
    assert.equal(calls.length, hasSubmittedData ? 0 : 1);
    if (calls.length) assert.equal(calls[0].preserveClassInput, true);
  }
});
