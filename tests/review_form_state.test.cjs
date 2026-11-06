const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const template = fs.readFileSync('app/templates/admin/review_form.html', 'utf8');
const source = template.match(/<script>([\s\S]*?)<\/script>/)[1];

// Execute the real page script with only its browser boundary represented here.
function page() {
  const elements = new Map();
  const state = {rows: [], shown: [], focused: null, reported: [], feedback: [], requests: [], intervals: [], timers: []};
  function element(id, attrs = '') {
    const classes = new Set();
    const listeners = new Map();
    return {
      id, value: 'valid', textContent: '', style: {}, dataset: {}, disabled: false,
      required: /\brequired\b/.test(attrs), readOnly: /\breadonly\b/.test(attrs),
      pattern: attrs.match(/pattern="([^"]*)"/)?.[1] || '',
      minLength: Number(attrs.match(/minlength="(\d+)"/)?.[1] || 0),
      type: attrs.match(/type="([^"]*)"/)?.[1] || 'text',
      customValidity: '',
      classList: {add: value => classes.add(value), remove: value => classes.delete(value), contains: value => classes.has(value), toggle(value, force) { const next = force === undefined ? !classes.has(value) : force; if (next) classes.add(value); else classes.delete(value); return next; }},
      setAttribute(key, value) { this[key] = value; }, removeAttribute(key) { delete this[key]; },
      setCustomValidity(value) { this.customValidity = value; },
      checkValidity() { return !this.customValidity && (!this.required || this.value.trim()) && (!this.pattern || new RegExp(this.pattern).test(this.value)) && this.value.length >= this.minLength; },
      reportValidity() { state.reported.push(id); return this.checkValidity(); },
      focus() { state.focused = id; }, scrollIntoView() {},
      addEventListener(type, callback) {
        if (!listeners.has(type)) listeners.set(type, []);
        listeners.get(type).push(callback);
      },
      dispatchEvent(event) { for (const callback of listeners.get(event.type) || []) callback(event); },
      appendChild() {}, replaceChildren() {}, append() {},
      closest() { return {appendChild() {}, querySelector() { return null; }}; },
    };
  }
  for (const match of template.matchAll(/<(?:input|textarea|select)\b([^>]*)>/g)) {
    const id = match[1].match(/\bid="([^"]+)"/)?.[1];
    if (id) elements.set(id, element(id, match[1]));
  }
  const get = id => { if (!elements.has(id)) elements.set(id, element(id)); return elements.get(id); };
  const required = () => [...elements.values()].filter(el => el.required);
  const form = get('reviewForm');
  form.querySelectorAll = () => required();
  form.checkValidity = () => required().every(el => el.checkValidity());
  form.reportValidity = () => { const invalid = required().find(el => !el.checkValidity()); return invalid ? invalid.reportValidity() : true; };
  Object.defineProperty(get('scoringTableBody'), 'innerHTML', {set() {state.rows = [];}, get() {return '';}});
  get('scoringTableBody').appendChild = row => state.rows.push(row);
  get('scoringSection').style.display = 'none';
  get('reviewFormContent').style.display = 'block';
  get('listener_number').value = 'A100';
  get('lecture_date').value = '2026-10-02';
  get('start_period').value = '3'; get('end_period').value = '4';
  get('contact_phone1').value = '13800000001'; get('contact_phone2').value = '13800000002';
  get('course_feedback').value = '课程重点讲解清楚，教师结合实际案例解释知识，并引导学生讨论。板书结构完整，课堂互动及时，有助于理解和掌握课程内容。';
  function makeRow() {
    const fields = new Map();
    return {set innerHTML(html) {
      for (const input of html.matchAll(/<input\b([^>]*)>/g)) {
        const attrs = input[1];
        const cls = attrs.match(/class="([^"]*)"/)[1].split(' ').find(value => value.startsWith('score-'));
        fields.set('.' + cls, {value: attrs.match(/value="([^"]*)"/)?.[1] || ''});
      }
    }, querySelector: selector => fields.get(selector), remove() {state.rows = state.rows.filter(row => row !== this);} };
  }
  const document = {
    getElementById: get, addEventListener() {}, createElement: tag => tag === 'tr' ? makeRow() : element('generated'),
    querySelector(selector) { const name = selector.match(/\[name="([^"]+)"\]/)?.[1]; return name ? get(name) : null; },
    querySelectorAll(selector) { return selector === '#scoringTableBody tr' ? state.rows : required(); },
  };
  const context = vm.createContext({
    document, console: {error() {}, warn() {}, log() {}}, URLSearchParams,
    window: {SwuFeedbackText: require('../app/static/js/feedback-text.js'), setTimeout(fn) {state.timers.push(fn); return state.timers.length;}, clearTimeout() {}, setInterval(fn) {state.intervals.push(fn); return 1;}, requestAnimationFrame(fn) {fn(); return 1;}, location: {}, setAdminFeedback(id, message) {state.feedback.push(message);}},
    bootstrap: {Modal: {getOrCreateInstance: modal => ({show() {state.shown.push(modal.id);}, hide() {}})}},
    FormData: class {entries() {return [...elements.values()].map(el => [el.id, el.value]);}},
    fetch: async (url, options) => {state.requests.push({url, options}); return {ok: true, json: async () => ({success: true})};},
  });
  vm.runInContext(source, context);
  vm.runInContext("formId = 1; userPermission = '审表_中心'; userPermissionLabel = '中心级审核'; originalFormData = {};", context);
  // Ignore unrelated field diffs when testing score state or validation.
  context.detectChanges = () => [];
  return {get, state, context, run: code => vm.runInContext(code, context), json: code => JSON.parse(JSON.stringify(vm.runInContext(code, context)))};
}

test('review feedback threshold excludes punctuation and emoji but counts mixed letters and digits', () => {
  const p = page();
  for (const [value, expected] of [
    ['课'.repeat(49) + '，。!?\n🙂', false],
    ['课'.repeat(20) + 'Abcde'.repeat(3) + '12345'.repeat(3) + '，\n🙂', true],
    ['𠮷'.repeat(49) + '，', false],
    ['𠮷'.repeat(50), true],
  ]) {
    p.get('course_feedback').value = value;
    assert.equal(p.run('validateReviewForm()'), expected);
  }
});

test('cancel and reopen retains manually entered score values and auto marker', () => {
  const p = page();
  p.run('submitReview(); addScoringRow("手动补充扣分原因", 2, 1, false); cancelSubmit(); submitReview();');
  assert.deepEqual(p.json('collectScoreData()'), [{reason: '手动补充扣分原因', department_score: '2', personal_score: '1', is_auto: false}]);
});

test('hidden score draft survives the next autosave before confirmation opens', async () => {
  const p = page();
  p.run('applyReviewDraftData({score_data: [{reason: "已恢复评分", department_score: 2, personal_score: 1, is_auto: true}]}); startReviewDraftAutosave();');
  await p.state.intervals[0]();
  // The timer invokes the actual draft-saving function; wait for its fetch body.
  assert.equal(p.state.requests.length, 1);
  const saved = JSON.parse(p.state.requests[0].options.body);
  assert.equal(saved.score_data.length, 1);
  assert.equal(saved.score_data[0].reason, '已恢复评分');
  assert.equal(saved.score_data[0].is_auto, true);
});

test('deleting the final score stays deleted when reopening confirmation', () => {
  const p = page();
  p.run('applyReviewDraftData({score_data: [{reason: "删除评分", department_score: 2, personal_score: 1}]}); submitReview();');
  p.state.rows[0].remove();
  p.run('cancelSubmit(); submitReview();');
  assert.deepEqual(p.json('collectScoreData()'), []);
});

for (const [field, badValue] of [['student_signature1', ''], ['student_signature2', ' '], ['contact_phone1', 'abc'], ['contact_phone2', '1380000000'], ['course_feedback', '好'], ['suggestions', ''], ['course_changes', '']]) {
  test(`invalid ${field} prevents confirmation and locates the field without clearing draft`, () => {
    const p = page();
    p.get(field).value = badValue;
    p.get('reviewComment').value = '尚未提交的审核意见';
    p.run('submitReview()');
    assert.equal(p.state.shown.includes('submitConfirmModal'), false);
    assert.equal(p.state.focused, field);
    assert.ok(p.state.feedback.length > 0);
    assert.equal(p.get(field).classList.contains('is-invalid'), true);
    assert.equal(p.get(field)['aria-invalid'], 'true');
    assert.equal(p.get(field).value, badValue);
    assert.equal(p.get('reviewComment').value, '尚未提交的审核意见');
  });
}

test('native constraints still prevent confirmation when a required field is invalid', () => {
  const p = page();
  p.get('course_title').checkValidity = () => false;
  p.run('submitReview()');
  assert.equal(p.state.shown.includes('submitConfirmModal'), false);
  assert.equal(p.state.focused, 'course_title');
});

test('listener number is readonly', () => {
  const p = page();
  assert.equal(p.get('listener_number').readOnly, true);
});

test('listener number is excluded from the displayed corrections', () => {
  const p = page();
  p.run('originalFormData = {listener_number: "A100"};');
  p.get('listener_number').value = 'CORRECTED';
  vm.runInContext(source.match(/function detectChanges\(\) \{[\s\S]*?(?=\n\/\/ 显示变化内容)/)[0], p.context);
  assert.equal(p.json('detectChanges()').some(change => change.field === 'listener_number'), false);
});

test('draft restore then reopen keeps the automatic scoring marker', () => {
  const p = page();
  p.run('applyReviewDraftData({score_data: [{reason: "恢复自动原因", department_score: 2, personal_score: 1, is_auto: true}]}); submitReview();');
  assert.equal(p.json('collectScoreData()')[0].is_auto, true);
});

test('correcting a validation error clears its mark and keeps the review draft', () => {
  const p = page();
  p.get('contact_phone2').value = 'abc';
  p.get('reviewComment').value = '手工审核意见';
  p.run('submitReview()');
  assert.equal(p.get('contact_phone2').classList.contains('is-invalid'), true);
  p.get('contact_phone2').value = '13800000002';
  p.run('submitReview()');
  assert.equal(p.get('contact_phone2').classList.contains('is-invalid'), false);
  assert.equal(p.get('contact_phone2')['aria-invalid'], undefined);
  assert.equal(p.state.shown.includes('submitConfirmModal'), true);
  assert.equal(p.get('reviewComment').value, '手工审核意见');
});

test('autosave retains partially filled score rows before the reason is entered', () => {
  const p = page();
  p.run('submitReview(); addScoringRow("", 2, 1, false); cancelSubmit();');
  const scores = p.json('collectReviewDraftData().score_data');
  assert.equal(scores.length, 1);
  assert.equal(scores[0].reason, '');
  assert.equal(scores[0].department_score, '2');
});

test('review conflict response displays the server reason and retains the draft', async () => {
  const p = page();
  const results = [];
  p.context.showResult = (...args) => results.push(args);
  p.context.fetch = async () => ({ok: false, status: 409, json: async () => ({success: false, message: '该表单已有更新版本，请刷新后重新审核'})});
  p.get('reviewComment').value = '本次修正仍需保留';
  p.run('confirmSubmit()');
  await new Promise(setImmediate);
  assert.equal(results[0][2], '该表单已有更新版本，请刷新后重新审核');
  assert.equal(p.get('reviewComment').value, '本次修正仍需保留');
});

test('server field errors are located without discarding the review draft', async () => {
  const p = page();
  p.context.showResult = () => {};
  p.context.fetch = async () => ({ok: false, status: 400, json: async () => ({success: false, code: 'invalid_form', message: '请修正联系电话2', field_errors: {contact_phone2: '联系电话2须为11位手机号'}})});
  p.get('reviewComment').value = '保留手工意见';
  p.run('confirmSubmit()');
  await new Promise(setImmediate);
  assert.equal(p.get('contact_phone2').classList.contains('is-invalid'), true);
  assert.equal(p.state.focused, 'contact_phone2');
  assert.equal(p.get('contact_phone2').customValidity, '联系电话2须为11位手机号');
  assert.equal(p.get('reviewComment').value, '保留手工意见');
});

test('valid fields allow confirmation without sending a review mutation', () => {
  const p = page();
  p.run('submitReview()');
  assert.equal(p.state.shown.includes('submitConfirmModal'), true);
  assert.equal(p.state.requests.length, 0);
});

test('reference lookup reuses cached results until a lookup field changes', async () => {
  const p = page();
  const lookupRequests = () => p.state.requests.filter(request => request.url === '/admin/api/review/reference_data').length;

  p.run("showReferenceData({focus() {}})");
  await new Promise(setImmediate);
  assert.equal(lookupRequests(), 1);

  p.run("showReferenceData({focus() {}})");
  await new Promise(setImmediate);
  assert.equal(lookupRequests(), 1);

  p.get('teacher_name').value = '王老师';
  p.run("showReferenceData({focus() {}})");
  await new Promise(setImmediate);
  assert.equal(lookupRequests(), 2);
});

function deferRequests(p) {
  const requests = [];
  p.context.fetch = (url, options) => new Promise(resolve => requests.push({url, options, resolve}));
  return (index, body, ok = true) => requests[index].resolve({ok, json: async () => body});
}

function reviewDraft(value) {
  return {success: true, exists: true, stale: false, data: {
    form_data: {student_grade_class: value}, review_comment: '旧审核意见', score_data: [],
  }};
}

function reviewRecord(value) {
  return {success: true, form: {id: 1, student_grade_class: value, status: '待审核'}, score_data: [], permission: '审表_中心'};
}

test('late review draft preserves a manually corrected class after an input event', async () => {
  const p = page(); const reply = deferRequests(p);
  p.run("originalFormData = {id:1, student_grade_class:'2024级农林经济管理01班'}; populateForm(originalFormData)");
  const loading = p.run('loadReviewFormDraft()');
  p.get('student_grade_class').value = '2024级农林经济01班';
  p.run('scheduleReviewFormDraftSave()');
  reply(0, reviewDraft('2024级农林经济管理01班'));
  await loading;
  assert.equal(p.get('student_grade_class').value, '2024级农林经济01班');
});

test('late review draft also preserves browser value changes without input events', async () => {
  const p = page(); const reply = deferRequests(p);
  const loading = p.run('loadReviewFormDraft()');
  p.get('student_grade_class').value = '2024级农林经济01班';
  reply(0, reviewDraft('2024级农林经济管理01班'));
  await loading;
  assert.equal(p.get('student_grade_class').value, '2024级农林经济01班');
  assert.equal(p.state.timers.length, 1);
});

test('unchanged review fields still restore their current draft', async () => {
  const p = page(); const reply = deferRequests(p);
  const loading = p.run('loadReviewFormDraft()');
  reply(0, reviewDraft('2024级农林经济01班'));
  await loading;
  assert.equal(p.get('student_grade_class').value, '2024级农林经济01班');
  assert.equal(p.get('reviewComment').value, '旧审核意见');
});

test('only the latest review draft request can restore data', async () => {
  const p = page(); const reply = deferRequests(p);
  const older = p.run('loadReviewFormDraft()');
  const newer = p.run('loadReviewFormDraft()');
  reply(1, reviewDraft('新草稿')); await newer;
  reply(0, reviewDraft('旧草稿')); await older;
  assert.equal(p.get('student_grade_class').value, '新草稿');
});

test('deleting a review draft invalidates an in-flight restore', async () => {
  const p = page(); const reply = deferRequests(p);
  const loading = p.run('loadReviewFormDraft()');
  const deleting = p.run('deleteReviewFormDraft()');
  p.get('student_grade_class').value = '保留当前班级';
  reply(0, reviewDraft('旧草稿')); await loading;
  reply(1, {success:true}); await deleting;
  assert.equal(p.get('student_grade_class').value, '保留当前班级');
});

test('resetting a review invalidates an in-flight restore', async () => {
  const p = page(); const reply = deferRequests(p);
  p.context.showReviewActionDialog = (_message, callback) => callback();
  p.run("originalFormData = {id:1, student_grade_class:'原始班级', review_comment:'原始意见'}");
  const loading = p.run('loadReviewFormDraft()');
  p.run('resetForm()');
  reply(0, reviewDraft('旧草稿')); await loading;
  assert.equal(p.get('student_grade_class').value, '原始班级');
  assert.equal(p.get('reviewComment').value, '原始意见');
  reply(1, {success:true}); await new Promise(setImmediate);
});

test('review draft from a previous form cannot populate a different form', async () => {
  const p = page(); const reply = deferRequests(p);
  const loading = p.run('loadReviewFormDraft()');
  p.run('formId = 2');
  reply(0, reviewDraft('上一张表')); await loading;
  assert.equal(p.get('student_grade_class').value, 'valid');
});

test('failed review draft response never restores a success-shaped payload', async () => {
  const p = page(); const reply = deferRequests(p);
  const loading = p.run('loadReviewFormDraft()');
  reply(0, reviewDraft('失败请求的草稿'), false); await loading;
  assert.equal(p.get('student_grade_class').value, 'valid');
});

test('older form response cannot replace a newer loaded review record', async () => {
  const p = page(); const reply = deferRequests(p);
  const older = p.run('loadFormData()');
  const newer = p.run('loadFormData()');
  reply(1, reviewRecord('新服务器版本')); await newer; await new Promise(setImmediate);
  reply(0, reviewRecord('旧服务器版本')); await older; await new Promise(setImmediate);
  assert.equal(p.get('student_grade_class').value, '新服务器版本');
});

test('reloading a review preserves edits made while the request is pending', async () => {
  const p = page(); const reply = deferRequests(p);
  const initial = p.run('loadFormData()');
  reply(0, reviewRecord('原始班级')); await initial; await new Promise(setImmediate);
  reply(1, {success:true,exists:false}); await new Promise(setImmediate);
  const loading = p.run('loadFormData()');
  p.get('student_grade_class').value = '手改班级';
  p.run('scheduleReviewFormDraftSave()');
  reply(2, reviewRecord('服务器旧值')); await loading; await new Promise(setImmediate);
  assert.equal(p.get('student_grade_class').value, '手改班级');
});

test('reloading a review schedules saving browser value changes without an input event', async () => {
  const p = page(); const reply = deferRequests(p);
  const initial = p.run('loadFormData()');
  reply(0, reviewRecord('原始班级')); await initial; await new Promise(setImmediate);
  reply(1, {success:true,exists:false}); await new Promise(setImmediate);
  const loading = p.run('loadFormData()');
  p.get('student_grade_class').value = '浏览器填写班级';
  reply(2, reviewRecord('服务器旧值')); await loading;
  assert.equal(p.get('student_grade_class').value, '浏览器填写班级');
  assert.equal(p.state.timers.length, 1);
  assert.equal(p.get('draftSaveStatus').dataset.state, 'secondary');
});

test('repeated setup binds each review input listener only once', () => {
  const p = page();
  p.run('setupFormChangeDetection(); setupFormChangeDetection()');
  p.get('student_grade_class').dispatchEvent({type:'input'});
  assert.equal(p.state.timers.length, 1);
});
