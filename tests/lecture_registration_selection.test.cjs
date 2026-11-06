const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const html = fs.readFileSync('app/templates/user/lecture_form.html', 'utf8');
const short = '2024级农林经济01班';
const full = '2024级农林经济管理01班';

function fixture(value = short) {
  const events = [], requests = [], timers = [];
  const fields = Object.fromEntries(['student_grade_class', 'course_title', 'teacher_name', 'teacher_college',
    'lecture_location', 'registration_id', 'autoFillAlert', 'unusedRegistrationsModal'].map(id => [id, {
    id, value: id === 'student_grade_class' ? value : '', style: {},
    dispatchEvent(event) {
      events.push({field: id, type: event.type, bubbles: event.bubbles});
      if (context.handleFormContextChange && event.bubbles) context.handleFormContextChange({target: this});
    },
  }]));
  const context = {
    lectureFormIsEditMode: false, lectureFormDraftSubmitting: false, lectureFormDraftLoadSequence: 0,
    lectureFormDraftEditRevision: 0, lectureFormDraftDirty: false, lectureFormDraftSavePromise: null,
    lectureFormDraftSaveController: null, lectureFormDraftTimer: null, lectureFormDraftUrl: '/draft',
    document: {getElementById: id => fields[id]},
    fetch: (url, options = {}) => new Promise(resolve => requests.push({url, options, resolve})),
    applyLectureFormDraftData(data) { for (const [key, value] of Object.entries(data)) if (fields[key]) fields[key].value = value; },
    collectLectureFormDraftData: () => Object.fromEntries(Object.entries(fields).map(([key, field]) => [key, field.value])),
    window: {lectureFormSiteCaptureId: null,
      lectureFormDraftState: {revision: 'r0', epoch: 0, blocked: false, deleting: false, deletePromise: null},
      dispatchEvent() {}, clearTimeout() {}, setTimeout(fn) {timers.push(fn);}, appFeedback() {}},
    Event: class {constructor(type, options = {}) {this.type = type; Object.assign(this, options);}},
    CustomEvent: class {constructor(type, options = {}) {this.type = type; Object.assign(this, options);}}, AbortController,
    bootstrap: {Modal: {getInstance: () => ({hide() {}})}},
    setLectureDraftStatus() {}, formatLectureDraftTime() {return '';}, console,
  };
  vm.createContext(context);
  vm.runInContext(html.slice(html.indexOf('async function loadLectureFormDraft()'), html.indexOf('// 表单验证')), context);
  vm.runInContext(html.slice(html.indexOf('function selectRegistration(id,'), html.indexOf('{% endif %}', html.indexOf('function selectRegistration(id,'))), context);
  return {context, fields, events, requests, timers,
    reply(index, data) {requests[index].resolve({ok: true, json: async () => ({success: true, exists: true, draft_revision: 'r1', data})});},
    select(className = short) {context.selectRegistration(42, {course_title: '新选课程', student_grade_class: className});},
  };
}

function loadAssistantFunction(name, context) {
  const source = fs.readFileSync('app/static/js/listening-assistant.js', 'utf8');
  const start = source.search(new RegExp(`        (?:async )?function ${name}\\(`));
  assert.notEqual(start, -1);
  const rest = source.slice(start + 1).search(/\n        (?:(?:async )?function |root\.addEventListener\()/);
  vm.runInContext(source.slice(start, rest < 0 ? source.length : start + 1 + rest), context);
}

for (const initialClass of [short, '']) {
  test(`registration selection invalidates older draft even when class text stays the same (${initialClass || 'blank'})`, async () => {
    const f = fixture(initialClass);
    const loading = f.context.loadLectureFormDraft();
    f.select(initialClass);
    f.reply(0, {student_grade_class: full, course_title: '旧草稿课程'});
    assert.equal(await loading, false);
    assert.equal(f.fields.student_grade_class.value, initialClass);
    assert.equal(f.fields.course_title.value, '新选课程');
    assert.equal(f.fields.registration_id.value, 42);
    assert.equal(f.context.lectureFormDraftDirty, true);
    assert.ok(f.context.lectureFormDraftEditRevision > 0);
    assert.ok(f.events.some(event => event.field === 'student_grade_class' && event.type === 'input' && event.bubbles));
  });
}

test('saving a registration while an old draft returns cannot persist the old course or expanded class', async () => {
  const f = fixture();
  const loading = f.context.loadLectureFormDraft();
  f.select();
  const saving = f.context.saveLectureFormDraft();
  f.reply(0, {student_grade_class: full, course_title: '旧草稿课程'});
  await loading;
  f.reply(1, {});
  await new Promise(setImmediate);
  if (f.requests.length > 2) f.reply(2, {});
  assert.equal(await saving, true);
  const writes = f.requests.filter(request => request.options.method === 'PUT');
  assert.equal(writes.length, 1);
  const data = JSON.parse(writes[0].options.body).data;
  assert.equal(data.student_grade_class, short);
  assert.equal(data.course_title, '新选课程');
  assert.equal(data.registration_id, 42);
});

test('registration input notification clears a stale assistant confirmation without changing selected fields', () => {
  const f = fixture();
  f.context.form = {querySelector: selector => f.fields[selector.slice(1)] || null,
    dispatchEvent: event => f.events.push({type: event.type})};
  f.context.guideState = {state: {stage: 'done'}, requestId: 1, assistantFilledFields: new Set(), assistantFilledGroups: new Set()};
  f.context.elements = {payload: {value: 'old-confirmation'}};
  f.context.cleanText = value => String(value || '').trim();
  f.context.readSemester = () => '2026-2027-1';
  f.context.initialQuestion = () => ({});
  f.context.renderAll = () => {};
  f.context.setStatus = () => {};
  for (const name of ['formContextFingerprint', 'invalidateRequests', 'clearAssistantConfirmation',
    'resetGuideState', 'markAssistantStateStale', 'handleFormContextChange']) loadAssistantFunction(name, f.context);
  f.context.guideState.assistantQueryFingerprint = f.context.formContextFingerprint();
  f.select();
  assert.equal(f.context.elements.payload.value, '');
  assert.equal(f.context.guideState.state, null);
  assert.ok(f.context.guideState.requestId > 1);
  assert.equal(f.fields.student_grade_class.value, short);
  assert.equal(f.fields.course_title.value, '新选课程');
  assert.equal(f.fields.registration_id.value, 42);
  assert.ok(f.events.some(event => event.type === 'lecture-assistant-course-cleared'));
});
