const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const html = fs.readFileSync('app/templates/user/lecture_form.html', 'utf8');
const source = html.slice(html.indexOf('async function loadLectureFormDraft()'), html.indexOf('// 表单验证'));

function fixture(revision = 'r0') {
  const fields = {student_grade_class: {value: '2024级农林经济01班'}, course_title: {value: '当前课程'}, expected_draft_revision: {value: revision}};
  const requests = [], statuses = [], timers = [];
  const context = {
    lectureFormIsEditMode: false, lectureFormDraftSubmitting: false, lectureFormDraftLoadSequence: 0,
    lectureFormDraftEditRevision: 0, lectureFormDraftDirty: false, lectureFormDraftSavePromise: null,
    lectureFormDraftSaveController: null, lectureFormDraftTimer: null, lectureFormDraftUrl: '/draft',
    document: {getElementById: id => fields[id] || null, querySelector: () => ({content: 'test-csrf'})},
    fetch: (url, options = {}) => new Promise(resolve => requests.push({url, options, resolve})),
    applyLectureFormDraftData: data => {for (const [key, value] of Object.entries(data)) if (fields[key]) fields[key].value = value;},
    collectLectureFormDraftData: () => ({student_grade_class: fields.student_grade_class.value, course_title: fields.course_title.value}),
    window: {lectureFormSiteCaptureId: null,
      lectureFormDraftState: {revision, epoch: 0, blocked: false, deleting: false, deletePromise: null},
      clearTimeout() {}, setTimeout(fn) {timers.push(fn);}, dispatchEvent() {}},
    AbortController, Headers, CustomEvent: class {}, console: {error() {}},
    setLectureDraftStatus: (text, kind) => statuses.push({text, kind}), formatLectureDraftTime: () => '',
  };
  vm.createContext(context); vm.runInContext(source, context);
  context.window.LectureDraftRevision = {begin: context.beginLectureDraftMutation, accept: context.acceptLectureDraftMutation, reject: context.rejectLectureDraftMutation};
  return {context, fields, requests, statuses, timers, state: context.window.lectureFormDraftState,
    reply(index, body, status = 200) {requests[index].resolve({ok: status < 400, status, json: async () => body});},
  };
}

function mountSubmit(f, editMode = false) {
  const posts = [];
  const originalGet = f.context.document.getElementById;
  const button = {disabled: false, innerHTML: '提交'};
  let handler;
  const form = {dataset: {}, querySelectorAll: () => [], querySelector: () => button,
    addEventListener(_type, callback) {handler = callback;},
    requestSubmit() {dispatch();}};
  f.context.document.getElementById = id => {
    if (id === 'lectureForm') return form;
    if (originalGet(id)) return originalGet(id);
    return f.fields[id] ||= {value: id === 'course_feedback' ? '课'.repeat(50) : '有效内容',
      checked: id === 'tm_lecture', style: {}, classList: {add() {}, remove() {}}};
  };
  f.context.window.SwuFeedbackText = require('../app/static/js/feedback-text.js');
  f.context.window.appFeedback = () => {};
  f.context.syncCoursewareQualityWithTeachingMethod = () => {};
  f.context.updateDateWithWeekday = () => {};
  f.context.lectureFormIsEditMode = editMode;
  vm.runInContext(html.slice(html.indexOf('// 表单验证'),html.indexOf('// 电话号码验证')),f.context);
  function dispatch() {
    let prevented = false;
    handler.call(form,{submitter:button,preventDefault(){prevented=true;}});
    if (!prevented) posts.push({revision:f.fields.expected_draft_revision.value});
    return prevented;
  }
  return {form, button, posts, submit:dispatch};
}

test('newer draft rejected after typing never upgrades the old tab token or retries its write', async () => {
  const f = fixture();
  const loading = f.context.loadLectureFormDraft();
  f.fields.student_grade_class.value = '本人刚改的简称'; f.context.scheduleLectureFormDraftSave();
  f.reply(0, {success: true, exists: true, draft_revision: 'r1', data: {student_grade_class: '旧草稿全称'}});
  assert.equal(await loading, false);
  assert.equal(f.state.revision, 'r0');
  const saving = f.context.saveLectureFormDraft();
  assert.equal(f.requests[1].options.headers['X-Lecture-Draft-Revision'], 'r0');
  f.reply(1, {success: false, code: 'draft_conflict'}, 409);
  assert.equal(await saving, false);
  assert.equal(f.fields.student_grade_class.value, '本人刚改的简称');
  assert.equal(f.state.blocked, true);
  assert.equal(await f.context.saveLectureFormDraft(), false);
  assert.equal(f.requests.length, 2);
});

test('successful own write advances the token before flushing newer typing', async () => {
  const f = fixture();
  const saving = f.context.saveLectureFormDraft();
  f.fields.student_grade_class.value = '保存期间新输入'; f.context.scheduleLectureFormDraftSave();
  f.reply(0, {success: true, draft_revision: 'r1'}); await new Promise(setImmediate);
  assert.equal(f.requests[1].options.headers['X-Lecture-Draft-Revision'], 'r1');
  assert.equal(JSON.parse(f.requests[1].options.body).data.student_grade_class, '保存期间新输入');
  f.reply(1, {success: true, draft_revision: 'r2'});
  assert.equal(await saving, true);
  assert.equal(f.state.revision, 'r2');
  assert.equal(f.fields.expected_draft_revision.value, 'r2');
  assert.ok(f.requests.every(request => request.options.method === 'PUT'));
});

test('accepted explicit draft load updates the token and clears the conflict', async () => {
  const f = fixture(); f.state.blocked = true;
  const loading = f.context.loadLectureFormDraft();
  f.reply(0, {success: true, exists: true, draft_revision: 'r7', data: {student_grade_class: '已核对的新草稿'}});
  assert.equal(await loading, true);
  assert.equal(f.state.revision, 'r7'); assert.equal(f.state.blocked, false);
  assert.equal(f.fields.expected_draft_revision.value, 'r7');
});

test('a GET issued before an own successful write cannot roll the token or text backward', async () => {
  const f = fixture();
  const loading = f.context.loadLectureFormDraft();
  const saving = f.context.saveLectureFormDraft();
  f.reply(1, {success: true, draft_revision: 'r1'}); assert.equal(await saving, true);
  f.reply(0, {success: true, exists: true, draft_revision: 'r0', data: {student_grade_class: '旧全称'}});
  assert.equal(await loading, false);
  assert.equal(f.state.revision, 'r1');
  assert.equal(f.fields.student_grade_class.value, '2024级农林经济01班');
});

test('an obsolete mutation response cannot promote a token after record context changed', async () => {
  const f = fixture(); const saving = f.context.saveLectureFormDraft();
  f.state.epoch++; f.state.revision = 'other-record';
  f.reply(0, {success: true, draft_revision: 'obsolete'});
  assert.equal(await saving, false); assert.equal(f.state.revision, 'other-record');
});

test('an obsolete conflict cannot block saving the successfully loaded newer context', async () => {
  const f = fixture(); const oldSaving = f.context.saveLectureFormDraft();
  const loading = f.context.loadLectureFormDraft();
  f.reply(1, {success: true, exists: true, draft_revision: 'new-context', data: {student_grade_class: '当前新班级'}});
  assert.equal(await loading, true);
  const statusCount = f.statuses.length;
  f.reply(0, {success: false, code: 'draft_conflict'}, 409);
  assert.equal(await oldSaving, false);
  assert.equal(f.state.blocked, false); assert.equal(f.state.revision, 'new-context');
  assert.equal(f.statuses.length, statusCount);
  const saving = f.context.saveLectureFormDraft();
  assert.equal(f.requests[2].options.headers['X-Lecture-Draft-Revision'], 'new-context');
  f.reply(2, {success: true, draft_revision: 'next'}); assert.equal(await saving, true);
});

test('a successful response missing its revision stops replay and preserves input', async () => {
  const f = fixture(); const saving = f.context.saveLectureFormDraft();
  f.reply(0, {success: true});
  assert.equal(await saving, false);
  assert.equal(f.state.blocked, true); assert.equal(f.state.revision, 'r0');
  assert.match(f.statuses.at(-1).text, /版本未能确认/);
  assert.equal(await f.context.saveLectureFormDraft(), false);
  assert.equal(f.requests.length, 1);
  assert.equal(f.fields.student_grade_class.value, '2024级农林经济01班');
});

test('delete waits for one in-flight write and uses that successful token exactly once', async () => {
  const f = fixture(); const saving = f.context.saveLectureFormDraft();
  const first = f.context.deleteLectureFormDraft(); const second = f.context.deleteLectureFormDraft();
  assert.equal(f.requests.length, 1);
  assert.equal(f.requests[0].options.signal.aborted, false);
  f.reply(0, {success: true, draft_revision: 'r1'}); assert.equal(await saving, false);
  await new Promise(setImmediate);
  assert.equal(f.requests.length, 2);
  assert.equal(f.requests[1].options.method, 'DELETE');
  assert.equal(f.requests[1].options.headers['X-Lecture-Draft-Revision'], 'r1');
  f.reply(1, {success: true, draft_revision: 'none'});
  assert.equal(await first, true); assert.equal(await second, true);
  assert.equal(f.state.revision, 'none');
});

test('typing while delete is pending is saved afterward against none', async () => {
  const f = fixture(); const deleting = f.context.deleteLectureFormDraft();
  f.fields.student_grade_class.value = '新表单班级'; f.context.scheduleLectureFormDraftSave();
  f.reply(0, {success: true, draft_revision: 'none'}); assert.equal(await deleting, true);
  assert.equal(f.context.lectureFormDraftDirty, true);
  const saving = f.context.saveLectureFormDraft();
  assert.equal(f.requests[1].options.headers['X-Lecture-Draft-Revision'], 'none');
  f.reply(1, {success: true, draft_revision: 'new'}); assert.equal(await saving, true);
});

test('an unknown or blocked revision never triggers a blind GET before writing', async () => {
  for (const revision of [null, 'r0']) {
    const f = fixture(revision); f.state.blocked = revision !== null;
    assert.equal(await f.context.saveLectureFormDraft(), false);
    assert.equal(f.requests.length, 0);
  }
});

test('valid submit waits for the pending own PUT and submits only once with its new revision', async () => {
  const f = fixture(); const ui = mountSubmit(f);
  const saving = f.context.saveLectureFormDraft();
  ui.submit(); ui.submit();
  assert.equal(ui.posts.length,0);
  f.reply(0,{success:true,draft_revision:'r1'}); await saving; await new Promise(setImmediate);
  assert.deepEqual(ui.posts,[{revision:'r1'}]);
  assert.equal(f.requests.length,1);
});

test('submit flush failure preserves text and never sends the form', async () => {
  const f = fixture(); const ui = mountSubmit(f);
  f.fields.student_grade_class.value='提交时应保留的班级'; f.context.scheduleLectureFormDraftSave();
  ui.submit();
  f.reply(0,{success:false,code:'draft_conflict'},409); await new Promise(setImmediate);
  assert.equal(ui.posts.length,0); assert.equal(ui.button.disabled,false);
  assert.equal(f.fields.student_grade_class.value,'提交时应保留的班级');
});

test('existing-form edit does not flush an unrelated ordinary draft before submission', () => {
  const f = fixture(); const ui = mountSubmit(f,true);
  f.context.lectureFormDraftDirty=true;
  ui.submit();
  assert.equal(ui.posts.length,1); assert.equal(f.requests.length,0);
});

function mountPhotoRequest(f, file, nextFunction) {
  const source = fs.readFileSync(file, 'utf8');
  const start = source.indexOf('async function request(url,options={})');
  const end = source.indexOf(nextFunction, start);
  Object.assign(f.context, {formRevision: 0, record: {id: 7}, latest: {record: {id: 7}},
    setTimeout() {return 1;}, clearTimeout() {}});
  vm.runInContext(source.slice(start, end), f.context);
}

test('photo PATCH then confirmation sends the newly accepted revision at each step', async () => {
  const f = fixture(); mountPhotoRequest(f, 'app/static/js/site-context-guide.js', 'function render(state)');
  const patch = f.context.request('/user/api/site-capture/7', {method: 'PATCH'});
  assert.equal(f.requests[0].options.headers.get('X-Lecture-Draft-Revision'), 'r0');
  f.reply(0, {success: true, draft_revision: 'r1', data: {id: 7}}); await patch;
  const confirm = f.context.request('/user/api/site-capture/7/confirm', {method: 'POST'});
  assert.equal(f.requests[1].options.headers.get('X-Lecture-Draft-Revision'), 'r1');
  f.reply(1, {success: true, draft_revision: 'r2', data: {id: 7}}); await confirm;
  assert.equal(f.state.revision, 'r2');
});

test('a non-version photo conflict does not block saving the current draft', async () => {
  const f = fixture(); mountPhotoRequest(f, 'app/static/js/site-context-guide.js', 'function render(state)');
  const confirmation = f.context.request('/user/api/site-capture/7/confirm', {method: 'POST'});
  f.reply(0, {success: false, code: 'course_changed', message: '课程已变化，请重新选择'}, 409);
  await assert.rejects(confirmation, /课程已变化/);
  assert.equal(f.state.blocked, false); assert.equal(f.state.revision, 'r0');
  const saving = f.context.saveLectureFormDraft();
  assert.equal(f.requests[1].options.headers['X-Lecture-Draft-Revision'], 'r0');
  f.reply(1, {success: true, draft_revision: 'r1'}); assert.equal(await saving, true);
});

for (const [file, nextFunction] of [['app/static/js/site-context-guide.js', 'function render(state)'], ['app/static/js/site-capture.js', 'async function action(button,fn)']]) {
  test(`new photo script on old HTML gives a refresh message before writing (${file})`, async () => {
    const f = fixture(); mountPhotoRequest(f, file, nextFunction);
    delete f.context.window.LectureDraftRevision;
    await assert.rejects(f.context.request('/user/api/site-capture/7/confirm', {method: 'POST'}), /页面版本已更新/);
    assert.equal(f.requests.length, 0);
  });
  test(`late photo mutation cannot update revision after context switches (${file})`, async () => {
    const f = fixture(); mountPhotoRequest(f, file, nextFunction);
    const request = f.context.request('/user/api/site-capture/7/confirm', {method: 'POST'});
    f.context.latest.record.id = 8; f.context.record.id = 8;
    f.reply(0, {success: true, draft_revision: 'old-result', data: {id: 7}});
    await assert.rejects(request, /记录已切换/);
    assert.equal(f.state.revision, 'r0');
  });
}
