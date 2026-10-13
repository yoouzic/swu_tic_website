const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('app/static/js/listening-assistant.js', 'utf8');

test('cycling options covers the last short batch and wraps without spending a question', () => {
  const batches = [[{code:'A',value:'1'}, {code:'B',value:'2'}, {code:'C',value:'3'}], [{code:'A',value:'4'}]];
  const ctx = {guideState: {question: {options:batches[0],option_batches:batches},state:{question_count:1}}, renderQuestion(){}, isObject:v=>v&&typeof v==='object'};
  loadFunction('displayedQuestionOptions',ctx);
  loadFunction('cycleQuestionOptions',ctx)();
  assert.equal(ctx.displayedQuestionOptions()[0].value,'4');
  assert.equal(ctx.guideState.state.question_count,1);
  ctx.cycleQuestionOptions();
  assert.equal(ctx.displayedQuestionOptions()[0].value,'1');
});

test('later-batch A sends its displayed factual value instead of first-batch A', async () => {
  const ctx={guideState:{state:{stage:'question'},question:{kind:'teacher',options:[{code:'A',value:'教师甲'}],option_batches:[[{code:'A',value:'教师甲'}],[{code:'A',label:'教师乙',value:'教师乙'}]],batchIndex:1}},cleanText:v=>String(v||''),NO_OP_CODES:new Set(),ensureStarted:async()=>true,answerValue:o=>o.value,sendAnswer:async(...args)=>{ctx.sent=args;} };
  loadFunction('displayedQuestionOptions',ctx);
  await loadFunction('answerOption',ctx)('A');
  assert.equal(ctx.sent[0],null);
  assert.equal(ctx.sent[1],'教师乙');
  assert.equal(ctx.sent[2].answer_code,'D');
});

function loadFunction(name, context) {
  const start = source.search(new RegExp(`        (?:async )?function ${name}\\(`));
  assert.notEqual(start, -1);
  const rest = source.slice(start + 1).search(/\n        (?:(?:async )?function |root\.addEventListener\()/);
  const end = rest < 0 ? source.length : start + 1 + rest;
  vm.runInNewContext(source.slice(start, end), context);
  return context[name];
}

test('skip sends an explicit nonfactual answer and retains a back-history entry',async()=>{
  const ctx={guideState:{question:{kind:'teacher'}},ensureStarted:async()=>true,
    sendAnswer:async(...args)=>{ctx.sent=args;}};
  await loadFunction('skipQuestion',ctx)();
  assert.equal(ctx.sent[0],'SKIP');assert.equal(ctx.sent[1],null);assert.equal(ctx.sent[2].skipped,true);
});

test('initial memory choice is not accidentally applied to a different server question',async()=>{
  const ctx={guideState:{state:null,question:{kind:'memory',options:[{code:'A',value:'date'}]}},
    cleanText:v=>String(v||''),NO_OP_CODES:new Set(),answerValue:o=>o.value,
    ensureStarted:async()=>{throw new Error('must start the displayed memory question');},
    startGuide:async facts=>{ctx.facts=facts;ctx.guideState.state={stage:'question'};return true;},
    sendAnswer:async(...args)=>{ctx.sent=args;}};
  loadFunction('displayedQuestionOptions',ctx);await loadFunction('answerOption',ctx)('A');
  assert.deepEqual(JSON.parse(JSON.stringify(ctx.facts)),{});
  assert.equal(ctx.sent[2].kind,'memory');
});

test('skipped answers can never become query facts during history recovery',()=>{
  const ctx={FACT_KINDS:['date','teacher'],cleanText:v=>String(v||'').trim()};
  const result=loadFunction('knownFactsFromHistory',ctx)([{kind:'date',value:'已跳过',skipped:true},{kind:'teacher',value:'甲'}]);
  assert.deepEqual(JSON.parse(JSON.stringify(result)),{teacher:'甲'});
});

test('typing I do not know uses skip rather than sending a false teacher name',async()=>{
  const ctx={guideState:{question:{kind:'teacher'}},elements:{customInput:{value:'我不知道'}},
    cleanText:v=>String(v||'').trim(),ensureStarted:async()=>true,
    skipQuestion:async()=>{ctx.skipped=true;},sendAnswer:async()=>{throw new Error('must not send unknown as a fact');}};
  vm.runInNewContext(source.match(/const UNCERTAIN_VALUES =[^;]+;/)[0]+'this.UNCERTAIN_VALUES=UNCERTAIN_VALUES;',ctx);
  await loadFunction('submitCustomAnswer',ctx)();assert.equal(ctx.skipped,true);
});

test('return restores the exact previous question rather than losing skipped dimensions',async()=>{
  const previous={state:{stage:'question',asked_question_kinds:['date','teacher'],known_facts:{}},
    question:{kind:'teacher'},candidates:[],history:[{kind:'date',skipped:true}]};
  const ctx={guideState:{history:[{kind:'date',skipped:true},{kind:'teacher',skipped:true,previous}]},
    invalidateRequests(){},clearAssistantConfirmation(){},renderAll(){},setStatus(){},
    knownFactsFromHistory:()=>{throw new Error('must restore previous question');}};
  await loadFunction('returnToPrevious',ctx)();
  assert.equal(ctx.guideState.question.kind,'teacher');assert.equal(ctx.guideState.history.length,1);
});

test('only configured semester is used, including when configuration is empty', () => {
  const elements = {semester: {value: ''}};
  const ctx = {elements, root: {dataset: {assistantSemester: '2026-2027-1'}}, cleanText: v => String(v || '').trim(), readRememberedSemester: () => '2026-2027', rememberSemester() {}};
  loadFunction('hydrateRememberedSemester', ctx)();
  assert.equal(elements.semester.value, '2026-2027-1');
  elements.semester.value = '';
  ctx.root.dataset.assistantSemester = '';
  loadFunction('hydrateRememberedSemester', ctx)();
  assert.equal(elements.semester.value, '');
});

test('unmatched date explains the result and offers correction inside the assistant', () => {
  const elements = {manual: {}, manualMessage: {}, editAnswer: {}};
  const ctx = {elements, guideState: {
    state: {stage: 'manual', known_facts: {date: '2025-09-18'}},
    candidates: [], manualRequested: false, recovery: {question: {kind: 'date'}},
  }, cleanText: v => String(v || '').trim()};
  loadFunction('renderManual', ctx)();
  assert.equal(elements.manual.hidden, false);
  assert.match(elements.manualMessage.textContent, /2025-09-18/);
  assert.match(elements.manualMessage.textContent, /没有找到/);
  assert.equal(elements.editAnswer.hidden, false);
});

test('pending guide action gives immediate feedback and ignores a duplicate click', async () => {
  let finish;
  let calls = 0;
  const button = {textContent: '下一步', dataset: {}, disabled: false};
  const ctx = {elements: {}, guideState: {}, root: {setAttribute() {}, querySelectorAll: () => [button]},
    setStatus(message) { ctx.status = message; }, setBusy: null,
  };
  loadFunction('setBusy', ctx);
  const run = loadFunction('runGuideAction', ctx);
  const pending = run(button, () => { calls++; return new Promise(resolve => { finish = resolve; }); });
  assert.equal(button.disabled, true);
  assert.match(button.textContent, /查询中/);
  assert.match(ctx.status, /正在/);
  await run(button, () => { calls++; });
  assert.equal(calls, 1);
  finish();
  await pending;
  assert.equal(button.disabled, false);
  assert.equal(button.textContent, '下一步');
});

test('newly available back navigation is not disabled again by the request cleanup',async()=>{
  const back={disabled:true,textContent:'返回上一问',dataset:{}},button={disabled:false,textContent:'选择',dataset:{}};
  const ctx={elements:{back},guideState:{history:[],state:{stage:'question'}},
    root:{setAttribute(){},querySelectorAll:()=>[button,back]},setStatus(){}};
  loadFunction('setBusy',ctx);
  await loadFunction('runGuideAction',ctx)(button,async()=>{ctx.guideState.history.push({kind:'memory'});back.disabled=false;});
  assert.equal(back.disabled,false);
});

test('editing an unmatched answer restores the question and discards the failed answer', () => {
  const previous = {state: {stage: 'question'}, question: {kind: 'date'}, candidates: [], history: [{kind: 'memory'}]};
  const ctx = {guideState: {recovery: previous, history: [{kind: 'memory'}, {kind: 'date', value: '2025-09-18'}]},
    invalidateRequests() {}, renderAll() {}, setStatus() {},
  };
  loadFunction('editLastAnswer', ctx)();
  assert.equal(ctx.guideState.question.kind, 'date');
  assert.equal(ctx.guideState.state.stage, 'question');
  assert.equal(ctx.guideState.history.length, 1);
  assert.equal(ctx.guideState.customMode, true);
});

test('a stalled request times out with a retry message', async () => {
  let fireTimeout;
  let cleared = false;
  const controller = new AbortController();
  const ctx = {Headers, document: {querySelector: () => null},
    window: {setTimeout(callback) { fireTimeout = callback; return 1; }, clearTimeout() { cleared = true; }},
    fetch(url, options) { return new Promise((resolve, reject) => {
      options.signal.addEventListener('abort', () => reject(Object.assign(new Error('aborted'), {name: 'AbortError'})));
    }); },
  };
  const request = loadFunction('requestJson', ctx)('/test', {}, {controller, signal: controller.signal});
  fireTimeout();
  await assert.rejects(request, /查询时间较长.*重试/);
  assert.equal(cleared, true);
});

test('empty history adds no user-facing block', () => {
  const history = {hidden: false, replaceChildren() {}};
  const ctx = {elements: {history}, guideState: {history: [], state: null}, FACT_KINDS: ['date', 'teacher', 'room'], appendText() {}};
  loadFunction('renderHistory', ctx)();
  assert.equal(history.hidden, true);
});

test('source failure gives an actionable Chinese message', () => {
  const elements = {status: {dataset: {}}, settings: {open: false}, errorActions: {hidden: true}};
  const ctx = {elements};
  loadFunction('setStatus', ctx)('authoritative schedule source is unavailable', 'error');
  assert.match(elements.status.textContent, /课表/);
  assert.doesNotMatch(elements.status.textContent, /authoritative|server|snapshot/);
  assert.match(elements.status.textContent, /联系管理员/);
  assert.doesNotMatch(elements.status.textContent, /核对学期/);
  assert.equal(elements.errorActions.hidden, false);
});

test('routine guide messages do not repeat the question', () => {
  const elements = {status: {dataset: {}}, errorActions: {}};
  const ctx = {elements};
  loadFunction('setStatus', ctx)('状态：请选择一个答案。', 'info');
  assert.equal(elements.status.hidden, true);
});

test('starting over ignores the old filled-course query facts', async () => {
  let facts;
  const ctx = {guideState: {state: null, formFactsDismissed: true},
    readKnownFacts: () => ({date: '2026-03-20', teacher: '旧教师'}),
    startGuide(values) { facts = values; return true; },
  };
  await loadFunction('ensureStarted', ctx)();
  assert.equal(Object.keys(facts).length, 0);
});

function locationConfirmationContext(candidate, formRoom = '') {
  const node = () => ({hidden: true, value: '', dataset: {}, replaceChildren() {}, appendChild() {}, classList: {toggle() {}}});
  const elements = {};
  ['candidateConfirm', 'candidateSummary', 'source', 'conflict', 'backupAckWrap', 'backupAck',
    'roomChoiceWrap', 'locationOverrideWrap', 'locationOverride', 'dateOverrideWrap', 'dateOverride',
    'periodOverrideWrap', 'periodOverride', 'confirm', 'reviewHint'].forEach(key => { elements[key] = node(); });
  const roomField = {value: formRoom};
  const ctx = {elements, roomField,
    guideState: {state: {stage: 'confirm', candidate_ids: [candidate.candidate_id], known_facts: {}},
      candidates: [candidate], selectedCandidateId: candidate.candidate_id, rejectedIds: [], history: [],
      assistantFilledFields: new Set(), assistantFilledGroups: new Set()},
    root: {querySelector: () => ctx.roomChoice || null},
    form: {querySelector: () => roomField},
    document: {createElement: node}, cleanText: v => String(v || '').trim(),
    appendText() {}, appendCandidateMeta() {}, renderCandidateComparison() {}, displayPeriod: v => v,
    SOURCE_LABELS: {primary: '全校课表', backup: '旧课表'}, CONFLICT_LABELS: {},
    markAssistantStateStale() { throw new Error('confirmation input must not invalidate the candidate'); },
  };
  loadFunction('updateConfirmControls', ctx);
  loadFunction('collectOverrides', ctx);
  loadFunction('handleInput', ctx);
  loadFunction('handleFormContextChange', ctx);
  return ctx;
}

test('missing timetable room is confirmed through an independent actual-location input', () => {
  const candidate = {candidate_id: 'missing-room', room: '', source_kind: 'primary', conflicts: ['location_needs_confirmation']};
  const ctx = locationConfirmationContext(candidate);
  loadFunction('renderCandidateConfirm', ctx)();
  assert.equal(ctx.elements.locationOverrideWrap.hidden, false);
  assert.equal(ctx.elements.roomChoiceWrap.hidden, true);
  assert.equal(ctx.elements.confirm.disabled, true);
  assert.match(ctx.elements.reviewHint.textContent, /实际听课地点/);

  const input = ctx.elements.locationOverride;
  input.value = '  北碚校区实习基地  ';
  input.matches = selector => selector.includes('[data-assistant-location-override]');
  ctx.handleInput({target: input});
  ctx.handleFormContextChange({target: input});
  assert.equal(ctx.elements.confirm.disabled, false);
  assert.equal(ctx.guideState.state.stage, 'confirm');
  assert.equal(ctx.guideState.selectedCandidateId, candidate.candidate_id);
  assert.equal(ctx.roomField.value, '');
  assert.deepEqual(JSON.parse(JSON.stringify(ctx.collectOverrides(candidate))), {lecture_location: '北碚校区实习基地'});

  input.value = '   ';
  ctx.handleInput({target: input});
  assert.equal(ctx.elements.confirm.disabled, true);
});

test('actual-location input retains same-candidate edits and clears them for another candidate', () => {
  const candidate = {candidate_id: 'missing-room-a', room: '', conflicts: ['location_needs_confirmation']};
  const ctx = locationConfirmationContext(candidate);
  const render = loadFunction('renderCandidateConfirm', ctx);
  render();
  ctx.elements.locationOverride.value = '第一实习点';
  render();
  assert.equal(ctx.elements.locationOverride.value, '第一实习点');

  const nextCandidate = {...candidate, candidate_id: 'missing-room-b'};
  ctx.guideState.candidates = [nextCandidate];
  ctx.guideState.state.candidate_ids = [nextCandidate.candidate_id];
  render();
  assert.equal(ctx.elements.locationOverride.value, '');
  assert.equal(ctx.elements.confirm.disabled, true);
});

test('starting a new search clears the unconfirmed actual-location input', () => {
  const ctx = locationConfirmationContext({candidate_id: 'missing-room', room: '', conflicts: ['location_needs_confirmation']});
  ctx.elements.locationOverride.value = '上一次实习点';
  ctx.elements.locationOverride.dataset.candidateId = 'missing-room';
  Object.assign(ctx, {invalidateRequests() {}, clearAssistantConfirmation() {}, initialQuestion: () => ({kind: 'memory'})});
  loadFunction('resetGuideState', ctx)();
  assert.equal(ctx.elements.locationOverride.value, '');
  assert.equal(ctx.elements.locationOverride.dataset.candidateId, undefined);
});

test('a supplied timetable room keeps the existing choice between timetable and manual room', () => {
  const candidate = {candidate_id: 'known-room', room: '32教101', conflicts: ['location_mismatch']};
  const ctx = locationConfirmationContext(candidate, '31教102');
  ctx.elements.locationOverride.value = '上次实习点';
  loadFunction('renderCandidateConfirm', ctx)();
  assert.equal(ctx.elements.locationOverrideWrap.hidden, true);
  assert.equal(ctx.elements.roomChoiceWrap.hidden, false);
  assert.equal(ctx.elements.confirm.disabled, true);

  ctx.roomChoice = {value: 'candidate'};
  ctx.updateConfirmControls();
  assert.equal(ctx.elements.confirm.disabled, false);
  assert.deepEqual(JSON.parse(JSON.stringify(ctx.collectOverrides(candidate))), {room: '32教101'});
  ctx.roomChoice = {value: 'manual'};
  assert.deepEqual(JSON.parse(JSON.stringify(ctx.collectOverrides(candidate))), {lecture_location: '31教102'});
});

test('independent actual-location confirmation uses the existing payload and draft override contract', () => {
  const candidate = {candidate_id: 'missing-room', room: '', teacher_name: '教师甲', lecture_date: '2026-10-13',
    source_kind: 'primary', conflicts: ['location_needs_confirmation']};
  const ctx = locationConfirmationContext(candidate);
  ctx.elements.locationOverride.value = '实习基地';
  Object.assign(ctx, {isObject: v => v && typeof v === 'object', readSemester: () => '2026-2027-1',
    TEMPLATE_VERSION: 'test', ASSISTANT_FILLED_FIELD_WHITELIST: [], ASSISTANT_FILLED_GROUP_WHITELIST: []});
  loadFunction('queryForConfirmation', ctx);
  const payload = loadFunction('buildConfirmationPayload', ctx)(candidate);
  assert.equal(payload.overrides.lecture_location, '实习基地');
  assert.equal(payload.query.room, undefined);
  assert.equal(ctx.roomField.value, '');

  ctx.elements.payload = {value: ''};
  loadFunction('storeAssistantPayload', ctx)({}, payload);
  const stored = JSON.parse(ctx.elements.payload.value);
  assert.equal(stored.stage, 'confirmed');
  assert.equal(stored.candidate_id, candidate.candidate_id);
  assert.deepEqual(stored.overrides, {lecture_location: '实习基地'});
});
