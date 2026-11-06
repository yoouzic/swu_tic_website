const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const html = fs.readFileSync('app/templates/user/lecture_form.html', 'utf8');
const start = html.indexOf('var initialFormData =');
const initialScript = html.slice(start, html.indexOf('</script>', start))
  .replace(/{% if form and form\.status == '已驳回' %}[\s\S]*?{% endif %}/, '');

function fixture(initial = {}, options = {}) {
  const listeners = new Map();
  const timers = [];
  const fields = {
    student_grade_class: {value: '', defaultValue: '', type: 'text'},
    course_title: {value: '', defaultValue: '', type: 'text'},
    expected_form_id: {value: '', defaultValue: '', type: 'hidden'},
    expected_form_updated_at: {value: '', defaultValue: '', type: 'hidden'},
    assistant_payload: {value: '', defaultValue: '', type: 'hidden'},
    registration_id: {value: '', defaultValue: '', type: 'hidden'},
    site_capture_id: {value: '', defaultValue: '', type: 'hidden'},
  };
  const form = {
    querySelectorAll: () => Object.values(fields),
    addEventListener(type, callback) {
      if (!listeners.has(type)) listeners.set(type, new Set());
      listeners.get(type).add(callback);
    },
    removeEventListener(type, callback) { listeners.get(type)?.delete(callback); },
  };
  Object.entries(fields).forEach(([name, field]) => { field.form = form; field.name = name; });
  let ready;
  const events = [];
  const context = {
    lectureFormIsEditMode: options.editMode ?? true,
    lectureScheduleReady: true, lectureRegistrationReady: true, lectureCaptureReady: true,
    lectureCurrentSemester: '2026-2027-1', lectureCurrentScheduleBatchId: '1',
    console: {log() {}},
    window: {lectureFormSiteCaptureId: null, dispatchEvent(event) { events.push(event); }},
    CSS: {escape: value => value},
    FormData: class { forEach(callback) {for (const field of Object.values(fields)) callback(field.value,field.name);} },
    CustomEvent: class { constructor(type, detail) { this.type = type; Object.assign(this, detail); } },
    document: {
      addEventListener(type, callback) { if (type === 'DOMContentLoaded') ready = callback; },
      getElementById: id => id === 'lectureForm' ? form : fields[id] || null,
      querySelectorAll(selector) { const match = selector.match(/^\[name="(.+)"\]$/); return match && fields[match[1]] ? [fields[match[1]]] : []; },
    },
    setTimeout(callback) { timers.push(callback); },
    updateDateWithWeekday() {}, updateClassPeriod() {}, refreshPhoneValidation() {},
  };
  vm.createContext(context);
  vm.runInContext(html.slice(html.indexOf('function parseAssistantPayloadObject(value)'),
                            html.indexOf('async function loadLectureFormDraft()')), context);
  vm.runInContext(initialScript.replace('{{ form_data | tojson }}', JSON.stringify({
    student_grade_class: '2024级计算机科学与技术01班',
    course_title: '原有课程', expected_form_id: 42, expected_form_updated_at: 'version-42',
    ...initial,
  })), context);
  return {
    fields, context, events,
    ready: () => ready(),
    restore: () => timers.shift()(),
    edit(id, value) {
      const target = fields[id]; target.value = value;
      for (const callback of listeners.get('input') || []) callback({target});
    },
    reset() {
      for (const callback of listeners.get('reset') || []) callback({target: form});
      for (const field of Object.values(fields)) field.value = field.defaultValue;
    },
    selectContext() {
      for (const callback of listeners.get('lecture-form-context-selected') || []) callback({target: form});
    },
  };
}

test('untouched edit fields and version identity still restore', () => {
  const f = fixture(); f.ready(); f.restore();
  assert.equal(f.fields.student_grade_class.value, '2024级计算机科学与技术01班');
  assert.equal(f.fields.course_title.value, '原有课程');
  assert.equal(f.fields.expected_form_id.value, 42);
  assert.equal(f.fields.expected_form_updated_at.value, 'version-42');
});

test('delayed initial restore preserves an intervening manual class correction', () => {
  const f = fixture(); f.ready(); f.edit('student_grade_class', '2024级计科01班'); f.restore();
  assert.equal(f.fields.student_grade_class.value, '2024级计科01班');
  assert.equal(f.fields.course_title.value, '原有课程');
  assert.equal(f.fields.expected_form_id.value, 42);
});

test('browser text autofill before DOM ready is preserved', () => {
  const f = fixture(); f.fields.student_grade_class.value = '2024级计科01班';
  f.ready(); f.restore();
  assert.equal(f.fields.student_grade_class.value, '2024级计科01班');
});

test('programmatic draft restore while waiting is not overwritten', () => {
  const f = fixture(); f.ready();
  f.fields.student_grade_class.value = '2024级计科01班'; f.restore();
  assert.equal(f.fields.student_grade_class.value, '2024级计科01班');
});

test('typing and clearing before delayed restore keeps the intentional empty field', () => {
  const f = fixture(); f.ready(); f.edit('student_grade_class', '临时');
  f.edit('student_grade_class', ''); f.restore();
  assert.equal(f.fields.student_grade_class.value, '');
  assert.equal(f.fields.expected_form_updated_at.value, 'version-42');
});

test('new photo form error restores sanitized capture metadata without undoing corrected text', () => {
  const f = fixture({site_capture_id: '7', csrf_token: 'old-posted-secret'}, {editMode: false});
  f.ready(); f.edit('student_grade_class', '2024级计科01班'); f.edit('course_title', '用户修正课程'); f.restore();
  assert.equal(f.fields.student_grade_class.value, '2024级计科01班');
  assert.equal(f.fields.course_title.value, '用户修正课程');
  assert.equal(f.context.window.lectureFormSiteCaptureId, 7);
  assert.equal(f.events.length, 1);
  assert.equal(f.events[0].type, 'lecture-form-draft-loaded');
  assert.equal(f.events[0].detail.data.site_capture_id, 7);
  for (const field of ['csrf_token', 'unique_id', 'expected_form_id', 'expected_form_updated_at']) {
    assert.equal(Object.hasOwn(f.events[0].detail.data, field), false);
  }
  assert.equal(f.fields.expected_form_id.value, '');
});

for (const editMode of [false, true]) {
  test(`reset cancels delayed initial data and metadata restoration (editMode=${editMode})`, () => {
    const f = fixture({site_capture_id: '7'}, {editMode});
    f.ready(); f.reset(); f.restore();
    assert.equal(f.fields.student_grade_class.value, '');
    assert.equal(f.fields.course_title.value, '');
    assert.equal(f.fields.expected_form_id.value, '');
    assert.equal(f.fields.expected_form_updated_at.value, '');
    assert.equal(f.context.window.lectureFormSiteCaptureId, null);
    assert.equal(f.events.length, 0);
  });
}

test('explicit registration selection preserves new fields while restoring current required photo metadata', () => {
  const f = fixture({site_capture_id: '7', registration_id: 'old-registration'}, {editMode: false});
  f.ready();
  f.fields.registration_id.value = 'new-registration';
  f.fields.course_title.value = '刚选择的课程';
  f.selectContext(); f.restore();
  assert.equal(f.fields.registration_id.value, 'new-registration');
  assert.equal(f.fields.course_title.value, '刚选择的课程');
  assert.equal(f.fields.student_grade_class.value, '');
  assert.equal(f.context.window.lectureFormSiteCaptureId, 7);
  assert.equal(f.fields.site_capture_id.value, '7');
  assert.equal(f.events.length, 1);
  assert.equal(f.events[0].detail.data.registration_id, 'new-registration');
  assert.equal(f.events[0].detail.data.course_title, '刚选择的课程');
  assert.equal(f.events[0].detail.data.assistant, undefined);
});
