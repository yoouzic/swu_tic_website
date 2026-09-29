'use strict';

/*
 * Browser acceptance check for the adaptive listening assistant.
 *
 * The script never starts the application, imports a workbook, guesses
 * credentials, or downloads a browser. With explicit loopback credentials it
 * installs local API fixtures and exercises the rendered question flow. With
 * no such inputs it performs static checks and reports browser scenarios as
 * NOT_RUN.
 */

const fs = require('node:fs');
const path = require('node:path');
const {URL} = require('node:url');

const REPO_ROOT = path.resolve(__dirname, '..', '..');
const TEMPLATE = path.join(REPO_ROOT, 'app', 'templates', 'user', 'lecture_form.html');
const SCRIPT = path.join(REPO_ROOT, 'app', 'static', 'js', 'listening-assistant.js');
const STYLE = path.join(REPO_ROOT, 'app', 'static', 'css', 'listening-assistant.css');
const TIMEOUT_MS = 12_000;
const SCENARIO_NAMES = [
  'initial-memory-question-a-b-c-d',
  'date-and-teacher-answers-stay-single-question',
  'custom-answer-renders-as-safe-text',
  'three-round-candidate-to-confirm',
  'four-round-uncertain-path-goes-manual',
  'singleton-still-offers-none',
  'back-clears-later-guide-state',
  'backup-ack-and-room-choice-gate-confirm',
  'date-and-period-conflict-overrides-gate-confirm',
  'keyboard-focus-320px-and-console-clean',
];

const scenarios = [];
const consoleErrors = [];
let horizontalOverflow = null;
let runtimePath = null;
let browserStarted = false;
let runtimeSelectionError = null;

function readUtf8(filePath) { return fs.readFileSync(filePath, 'utf8'); }
function assert(condition, message) { if (!condition) throw new Error(message); }
function pass(name, details) { scenarios.push({name, status: 'PASS', ...(details || {})}); }
function notRun(name, reason) { scenarios.push({name, status: 'NOT_RUN', reason}); }
function blocked(name, reason) { scenarios.push({name, status: 'BLOCKED', reason}); }
function failed(name, reason) { scenarios.push({name, status: 'FAIL', reason}); }
function hasScenario(name) { return scenarios.some((item) => item.name === name); }

function staticContractChecks() {
  const template = readUtf8(TEMPLATE);
  const assistantScript = readUtf8(SCRIPT);
  const style = readUtf8(STYLE);
  for (const hook of [
    'data-listening-assistant',
    'id="assistantSemester"',
    'data-assistant-question',
    'data-assistant-options',
    'data-assistant-custom-input',
    'data-assistant-candidate-confirm',
    'data-assistant-source',
    'data-assistant-conflict',
    'data-assistant-backup-ack',
    'data-assistant-room-choice',
    'data-assistant-date-override',
    'data-assistant-period-override',
    'data-assistant-fallback-panel',
    'data-assistant-manual-view',
    'aria-live',
  ]) assert(template.includes(hook), 'template is missing ' + hook);
  for (const endpoint of [
    '/user/api/listening-assistant/guide/start',
    '/user/api/listening-assistant/guide/answer',
    '/user/api/listening-assistant/fallback',
    '/user/api/listening-assistant/confirm',
  ]) assert(assistantScript.includes(endpoint), 'assistant script is missing ' + endpoint);
  for (const marker of [
    '你还记得哪类信息？',
    "code: 'A'", "code: 'B'", "code: 'C'",
    "dataset.assistantOption = 'D'",
    'MAX_QUESTIONS = 4',
    'question_kind', 'option_code', 'custom_value',
    'backupSourceBatchId', 'acknowledged_source',
    'overrides.lecture_date', 'guide_state', 'answer_code',
  ]) assert(assistantScript.includes(marker), 'assistant script is missing ' + marker);
  assert(!/window\.(alert|confirm)\s*\(/.test(assistantScript), 'assistant must not use window.alert/confirm');
  assert(!assistantScript.includes('innerHTML'), 'assistant must render user text without innerHTML');
  assert(style.includes('@media'), 'assistant style should include responsive rules');
  assert(style.includes('min-width: 0'), 'assistant style should constrain narrow layouts');
  pass('static-dom-contracts');
}

function isLoopbackUrl(value) {
  try {
    const parsed = new URL(value);
    return parsed.protocol === 'http:' && ['127.0.0.1', 'localhost', '[::1]'].includes(parsed.hostname);
  } catch (error) {
    return false;
  }
}

function isWithin(root, target) {
  const relative = path.relative(root, target);
  return relative === '' || (relative !== '..' && !relative.startsWith('..' + path.sep) && !path.isAbsolute(relative));
}

function candidate(overrides) {
  const sourceKind = overrides.source_kind || 'primary';
  return {
    candidate_id: overrides.candidate_id,
    lecture_date: overrides.lecture_date || '2026-09-18',
    room: overrides.room || '8-309',
    period: overrides.period || [3, 4],
    course_title: overrides.course_title || '数据结构',
    teacher_name: overrides.teacher_name || '张老师',
    teacher_college: '计算机学院',
    student_grade_class: '2024级计算机1班',
    source_kind: sourceKind,
    source_label: sourceKind === 'backup' ? '备用课表线索 · 需核对' : '当前权威课表',
    source_batch_id: sourceKind === 'backup' ? 'backup-fixture-1' : 'primary-fixture-1',
    conflicts: overrides.conflicts || [],
    needs_confirmation: overrides.needs_confirmation === true || Boolean(overrides.conflicts?.length),
  };
}

const PRIMARY_MULTI = [
  candidate({candidate_id: 'primary-fixture-1', course_title: '数据结构'}),
  candidate({candidate_id: 'primary-fixture-2', course_title: '算法设计', period: [5, 6]}),
];
const PRIMARY_SINGLE = [candidate({candidate_id: 'primary-single-fixture', room: '9-101'})];
const CONFLICT = [candidate({
  candidate_id: 'primary-conflict-fixture',
  conflicts: ['date_needs_confirmation', 'period_mismatch'],
})];
const BACKUP = [candidate({
  candidate_id: 'backup-fixture-1',
  room: '9-101',
  course_title: '备用线索课程',
  source_kind: 'backup',
  needs_confirmation: true,
})];

function memoryQuestion() {
  return {
    kind: 'memory',
    prompt: '你还记得哪类信息？',
    options: [
      {code: 'A', label: '听课日期', value: 'date', candidate_count: 0},
      {code: 'B', label: '授课教师', value: 'teacher', candidate_count: 0},
      {code: 'C', label: '教室', value: 'room', candidate_count: 0},
    ],
    allow_custom: true,
  };
}

function fieldQuestion(kind) {
  const values = {
    date: [['2026-09-18', '2026-09-18'], ['2026-09-19', '2026-09-19']],
    teacher: [['张老师', '张老师'], ['王老师', '王老师']],
    room: [['8-309', '8-309'], ['9-101', '9-101']],
    period: [['第3-4节', '第3-4节'], ['第5-6节', '第5-6节']],
  };
  const options = (values[kind] || values.teacher).map(([label, value], index) => ({
    code: String.fromCharCode(65 + index), label, value, candidate_count: 1,
  }));
  return {kind, prompt: `请选择你记得的${kind}：`, options, allow_custom: true};
}

function candidateQuestion(items) {
  return {
    kind: 'teacher',
    prompt: '请选择最符合的课程候选：',
    options: items.slice(0, 3).map((item, index) => ({
      code: String.fromCharCode(65 + index),
      label: `${item.course_title} · ${item.teacher_name} · ${item.room}`,
      value: item.candidate_id,
      candidate_count: 1,
    })),
    allow_custom: true,
  };
}

function stateFor(facts, count, stage, candidates) {
  return {
    known_facts: {...facts},
    candidate_ids: candidates.map((item) => item.candidate_id),
    asked_question_kinds: [],
    question_count: count,
    stage,
  };
}

function candidatesFor(facts) {
  if (facts.teacher === 'Conflict' && facts.date) return CONFLICT;
  if (facts.room === '9-101') return PRIMARY_SINGLE;
  if (facts.date && (facts.teacher || facts.room)) return PRIMARY_MULTI;
  return [];
}

function nextQuestion(facts) {
  if (!facts.date) return fieldQuestion('date');
  if (!facts.teacher) return fieldQuestion('teacher');
  if (!facts.room) return fieldQuestion('room');
  return fieldQuestion('period');
}

function guideResult(facts, count) {
  if (count === 0 && Object.keys(facts).length === 0) return {
    state: stateFor(facts, count, 'question', []),
    question: memoryQuestion(), candidates: [], needs_confirmation: false,
  };
  const items = candidatesFor(facts);
  if (items.length === 1 && !items[0].conflicts.length) return {
    state: stateFor(facts, count, 'confirm', items),
    question: null, candidates: items, needs_confirmation: true,
  };
  if (items.length) return {
    state: stateFor(facts, count, 'candidate', items),
    question: candidateQuestion(items),
    candidates: items,
    needs_confirmation: true,
  };
  if (count >= 4) return {
    state: stateFor(facts, count, 'manual', []),
    question: null, candidates: [], needs_confirmation: false,
  };
  return {
    state: stateFor(facts, count, 'question', []),
    question: nextQuestion(facts),
    candidates: [], needs_confirmation: false,
  };
}

async function fulfillJson(route, payload, status = 200) {
  await route.fulfill({status, contentType: 'application/json', body: JSON.stringify(payload)});
}

async function rejectFixture(route, error) {
  await fulfillJson(route, {
    success: false, data: {}, message: 'browser fixture contract: ' + error.message,
  }, 400);
}

function parseRequestBody(route) { return JSON.parse(route.request().postData() || '{}'); }

async function installApiFixtures(page) {
  await page.route('**/user/api/listening-assistant/guide/start', async (route) => {
    try {
      const request = parseRequestBody(route);
      assert(route.request().method() === 'POST', 'guide start must use POST');
      assert(request.semester === '2026-2027-1', 'guide start semester is missing');
      assert(request.known_facts && typeof request.known_facts === 'object', 'guide start facts are missing');
      const result = guideResult(request.known_facts, 0);
      await fulfillJson(route, {
        success: true,
        data: {...result, backup_source_batch_id: 'backup-fixture-1', backup_rescue_available: true},
        message: '',
      });
    } catch (error) { await rejectFixture(route, error); }
  });

  await page.route('**/user/api/listening-assistant/guide/answer', async (route) => {
    try {
      const request = parseRequestBody(route);
      assert(route.request().method() === 'POST', 'guide answer must use POST');
      assert(request.semester === '2026-2027-1', 'guide answer semester is missing');
      assert(['memory', 'date', 'teacher', 'room', 'period'].includes(request.question_kind), 'guide question kind is invalid');
      assert(request.state && Number.isInteger(request.state.question_count), 'guide answer state is missing');
      assert(Boolean(request.option_code) !== Boolean(request.custom_value), 'option/custom answer must be exclusive');
      const facts = {...(request.state.known_facts || {})};
      const count = request.state.question_count + 1;
      if (request.state.stage === 'candidate') {
        const items = candidatesFor(facts);
        const index = Math.max(0, Number(request.option_code?.charCodeAt(0) || 65) - 65);
        const selected = items[index] || items[0];
        await fulfillJson(route, {
          success: true,
          data: {
            state: stateFor(facts, count, 'confirm', selected ? [selected] : []),
            question: null, candidates: items, needs_confirmation: true,
            backup_source_batch_id: 'backup-fixture-1', backup_rescue_available: true,
          },
          message: '',
        });
        return;
      }
      if (request.question_kind === 'memory' && request.option_code) {
        const nextKind = {A: 'date', B: 'teacher', C: 'room'}[request.option_code];
        assert(nextKind, 'memory option is invalid');
        const result = guideResult(facts, count);
        result.question = fieldQuestion(nextKind);
        result.state = stateFor(facts, count, 'question', []);
        await fulfillJson(route, {
          success: true,
          data: {...result, backup_source_batch_id: 'backup-fixture-1', backup_rescue_available: true},
          message: '',
        });
        return;
      }
      if (request.custom_value === '不确定') {
        await fulfillJson(route, {
          success: true,
          data: {
            state: stateFor(facts, count, 'manual', []),
            question: null, candidates: [], needs_confirmation: false,
            backup_source_batch_id: 'backup-fixture-1', backup_rescue_available: true,
          },
          message: '',
        });
        return;
      }
      if (request.custom_value != null) {
        facts[request.question_kind] = request.custom_value;
      } else if (request.option_code) {
        const valueMap = {
          date: {A: '2026-09-18', B: '2026-09-19'},
          teacher: {A: '张老师', B: '王老师'},
          room: {A: '8-309', B: '9-101'},
          period: {A: '第3-4节', B: '第5-6节'},
        };
        facts[request.question_kind] = valueMap[request.question_kind]?.[request.option_code] || request.option_code;
      }
      const result = guideResult(facts, count);
      await fulfillJson(route, {
        success: true,
        data: {...result, backup_source_batch_id: 'backup-fixture-1', backup_rescue_available: true},
        message: '',
      });
    } catch (error) { await rejectFixture(route, error); }
  });

  await page.route('**/user/api/listening-assistant/fallback', async (route) => {
    try {
      const request = parseRequestBody(route);
      assert(route.request().method() === 'POST', 'fallback must use POST');
      assert(request.date === '2026-09-18', 'fallback date is missing');
      assert(request.teacher === '张老师', 'fallback teacher is missing');
      assert(request.semester === '2026-2027-1', 'fallback semester is missing');
      assert(request.source_batch_id === 'backup-fixture-1', 'fallback batch is missing');
      assert(request.explicit_fallback === true, 'fallback must be explicit');
      await fulfillJson(route, {
        success: true,
        data: {
          candidates: BACKUP,
          source_batch_id: 'backup-fixture-1',
          fallback_reason: request.reason,
          needs_confirmation: true,
        },
        message: '',
      });
    } catch (error) { await rejectFixture(route, error); }
  });

  await page.route('**/user/api/listening-assistant/confirm', async (route) => {
    try {
      const request = parseRequestBody(route);
      assert(route.request().method() === 'POST', 'confirm must use POST');
      assert(request.stage === 'confirmed', 'confirm stage is missing');
      assert(request.query?.lecture_date === '2026-09-18', 'confirm date is missing');
      assert(request.query?.semester === '2026-2027-1', 'confirm semester is missing');
      assert(request.template_version === 'task4-v1', 'confirm template version is invalid');
      const selected = request.source_kind === 'backup'
        ? BACKUP[0]
        : [...PRIMARY_MULTI, ...PRIMARY_SINGLE, ...CONFLICT].find((item) => item.candidate_id === request.candidate_id);
      assert(selected, 'confirm candidate is not from fixture');
      if (request.source_kind === 'backup') {
        assert(request.acknowledged_source === true, 'backup acknowledgement is missing');
        assert(request.explicit_fallback === true, 'backup fallback flag is missing');
        assert(request.source_batch_id === 'backup-fixture-1', 'backup source batch is missing');
        assert(request.overrides && (request.overrides.room || request.overrides.lecture_location), 'backup room choice is missing');
      } else {
        assert(!request.source_batch_id || request.source_batch_id === 'primary-fixture-1', 'primary source batch is invalid');
      }
      if (selected.conflicts.includes('date_needs_confirmation')) {
        assert(request.overrides?.lecture_date, 'date override is missing');
        assert(request.overrides?.period, 'period override is missing');
      }
      const location = request.overrides?.lecture_location || request.overrides?.room || selected.room;
      await fulfillJson(route, {
        success: true,
        data: {
          candidate: selected, query: request.query, overrides: request.overrides || {},
          field_snapshot: {
            lecture_date: request.overrides?.lecture_date || selected.lecture_date,
            lecture_location: location, teacher_name: selected.teacher_name,
            teacher_college: selected.teacher_college, course_title: selected.course_title,
            student_grade_class: selected.student_grade_class, class_period: '第3-4节',
          },
          template_version: 'task4-v1',
          confirmation: {
            confirmed: true, candidate_id: selected.candidate_id,
            source_kind: selected.source_kind, source_batch_id: selected.source_batch_id,
            semester: '2026-2027-1',
            acknowledged_source: request.acknowledged_source === true,
            explicit_fallback: request.explicit_fallback === true,
            fallback_reason: request.fallback_reason || null,
          },
        },
        message: '',
      });
    } catch (error) { await rejectFixture(route, error); }
  });
}

function findPlaywrightModule() {
  const candidates = [];
  if (process.env.LISTENING_ASSISTANT_PLAYWRIGHT_MODULE) {
    const override = path.resolve(process.env.LISTENING_ASSISTANT_PLAYWRIGHT_MODULE);
    const bundledRoots = [
      path.join(REPO_ROOT, 'node_modules'),
      path.resolve(REPO_ROOT, '..', 'SWU_TIC-main', 'tmp', 'e2e_node'),
    ];
    if (!bundledRoots.some((root) => isWithin(root, override))) {
      runtimeSelectionError = 'LISTENING_ASSISTANT_PLAYWRIGHT_MODULE must be inside a bundled runtime directory';
      return null;
    }
    candidates.push(override);
  }
  candidates.push(path.resolve(REPO_ROOT, '..', 'SWU_TIC-main', 'tmp', 'e2e_node', 'node_modules', 'playwright'));
  for (const modulePath of candidates) {
    try { require.resolve(modulePath); return modulePath; } catch (error) { /* next */ }
  }
  return null;
}

async function openForm(page, baseUrl) {
  await page.goto(baseUrl.replace(/\/$/, '') + '/user/submit_form', {
    waitUntil: 'domcontentloaded', timeout: TIMEOUT_MS,
  });
  await page.locator('[data-listening-assistant]').waitFor({state: 'visible', timeout: TIMEOUT_MS});
}

async function fillSemester(page) { await page.locator('#assistantSemester').fill('2026-2027-1'); }

async function waitForText(page, selector, value) {
  await page.waitForFunction(({selector: query, value: expected}) => {
    return document.querySelector(query)?.textContent?.includes(expected);
  }, {selector, value}, {timeout: TIMEOUT_MS});
}

async function runScenario(name, callback) {
  try { await callback(); pass(name); }
  catch (error) { failed(name, error?.message || String(error)); }
}

async function login(page, baseUrl, username, password) {
  await page.goto(baseUrl.replace(/\/$/, '') + '/auth/login', {
    waitUntil: 'domcontentloaded', timeout: TIMEOUT_MS,
  });
  await page.locator('#student_id').fill(username);
  await page.locator('#password').fill(password);
  await page.locator('form button[type="submit"]').first().click();
  await page.waitForURL((url) => !url.toString().includes('/auth/login'), {timeout: TIMEOUT_MS});
  assert(!page.url().includes('/auth/login'), 'explicit browser credentials did not authenticate');
}

async function runBrowserScenarios() {
  const baseUrl = String(process.env.LISTENING_ASSISTANT_BROWSER_BASE_URL || '').trim();
  const username = String(process.env.LISTENING_ASSISTANT_BROWSER_USER || '').trim();
  const password = String(process.env.LISTENING_ASSISTANT_BROWSER_PASSWORD || '');
  if (!baseUrl || !username || !password) {
    SCENARIO_NAMES.forEach((name) => notRun(name, 'requires an already-running isolated loopback server and explicit browser credentials'));
    return;
  }
  if (!isLoopbackUrl(baseUrl)) {
    SCENARIO_NAMES.forEach((name) => blocked(name, 'browser base URL must be an http loopback address'));
    return;
  }
  runtimePath = findPlaywrightModule();
  if (!runtimePath) {
    const reason = runtimeSelectionError || 'explicit browser inputs were supplied but the bundled Playwright runtime was not found';
    SCENARIO_NAMES.forEach((name) => blocked(name, reason));
    return;
  }
  let playwright;
  try { playwright = require(runtimePath); }
  catch (error) {
    SCENARIO_NAMES.forEach((name) => blocked(name, 'Playwright runtime could not be loaded: ' + error.message));
    return;
  }
  let browser;
  try {
    browser = await playwright.chromium.launch({headless: true, timeout: TIMEOUT_MS});
    browserStarted = true;
    const page = await browser.newPage({viewport: {width: 1440, height: 900}});
    page.on('console', (message) => { if (message.type() === 'error') consoleErrors.push(message.text()); });
    page.on('pageerror', (error) => consoleErrors.push(error.message));
    await installApiFixtures(page);
    await login(page, baseUrl, username, password);

    await runScenario('initial-memory-question-a-b-c-d', async () => {
      await openForm(page, baseUrl); await fillSemester(page);
      assert(await page.locator('[data-assistant-option]').count() === 4, 'initial question must show A/B/C/D');
      await waitForText(page, '[data-assistant-question]', '你还记得哪类信息？');
    });

    await runScenario('date-and-teacher-answers-stay-single-question', async () => {
      await openForm(page, baseUrl); await fillSemester(page);
      await page.locator('[data-assistant-option="A"]').click();
      await waitForText(page, '[data-assistant-question]', '日期');
      await openForm(page, baseUrl); await fillSemester(page);
      await page.locator('[data-assistant-option="B"]').click();
      await waitForText(page, '[data-assistant-question]', '教师');
      await openForm(page, baseUrl); await fillSemester(page);
      await page.locator('[data-assistant-option="C"]').click();
      await waitForText(page, '[data-assistant-question]', '教室');
    });

    await runScenario('custom-answer-renders-as-safe-text', async () => {
      await openForm(page, baseUrl); await fillSemester(page);
      await page.locator('[data-assistant-option="B"]').click();
      await page.locator('[data-assistant-option="D"]').click();
      await page.locator('[data-assistant-custom-input]').fill('<img src=x onerror=alert(1)>');
      assert((await page.locator('[data-assistant-custom-input]').inputValue()).includes('<img'), 'custom text was altered');
      assert(await page.locator('img').count() === 0, 'custom text created an image element');
    });

    await runScenario('three-round-candidate-to-confirm', async () => {
      await openForm(page, baseUrl); await fillSemester(page);
      await page.locator('[data-assistant-option="B"]').click();
      await page.locator('[data-assistant-option="A"]').click();
      await waitForText(page, '[data-assistant-question]', '日期');
      await page.locator('[data-assistant-option="A"]').click();
      await waitForText(page, '[data-assistant-question]', '候选');
      await page.locator('[data-assistant-option="A"]').click();
      await page.locator('[data-assistant-candidate-confirm]').waitFor({state: 'visible'});
    });

    await runScenario('four-round-uncertain-path-goes-manual', async () => {
      await openForm(page, baseUrl); await fillSemester(page);
      await page.locator('[data-assistant-option="A"]').click();
      await page.locator('[data-assistant-option="D"]').click();
      await page.locator('[data-assistant-custom-input]').fill('不确定');
      await page.locator('[data-assistant-custom-submit]').click();
      await page.locator('[data-assistant-manual-view]').waitFor({state: 'visible'});
    });

    await runScenario('singleton-still-offers-none', async () => {
      await openForm(page, baseUrl); await fillSemester(page);
      await page.locator('#lecture_date').fill('2026-09-18');
      await page.locator('#lecture_location').fill('9-101');
      await page.locator('[data-assistant-option="A"]').click();
      await page.locator('[data-assistant-candidate-confirm]').waitFor({state: 'visible'});
      assert(await page.locator('[data-assistant-none]').isVisible(), 'singleton must still expose none/manual');
    });

    await runScenario('back-clears-later-guide-state', async () => {
      await openForm(page, baseUrl); await fillSemester(page);
      await page.locator('[data-assistant-option="B"]').click();
      await page.locator('[data-assistant-back]').click();
      await waitForText(page, '[data-assistant-question]', '你还记得哪类信息？');
    });

    await runScenario('backup-ack-and-room-choice-gate-confirm', async () => {
      await openForm(page, baseUrl); await fillSemester(page);
      await page.locator('#lecture_date').fill('2026-09-18');
      await page.locator('#lecture_location').fill('8-309');
      await page.locator('[data-assistant-option="A"]').click();
      await page.locator('[data-assistant-fallback]').click();
      await page.locator('[data-assistant-fallback-date]').fill('2026-09-18');
      await page.locator('[data-assistant-fallback-teacher]').fill('张老师');
      await page.locator('[data-assistant-fallback-submit]').click();
      await page.locator('[data-assistant-option="A"]').click();
      const confirm = page.locator('[data-assistant-confirm]');
      assert(await page.locator('[data-assistant-room-choice]').count() === 2, 'backup room choice is missing');
      assert(await confirm.isDisabled(), 'backup acknowledgement/room choice must gate confirm');
      await page.locator('[data-assistant-room-choice="candidate"]').check();
      await page.locator('[data-assistant-backup-ack]').check();
      assert(!(await confirm.isDisabled()), 'backup confirm did not unlock after acknowledgement and room choice');
      await confirm.click();
      await waitForText(page, '[data-assistant-status]', '已完成');
      const payload = JSON.parse(await page.locator('#assistant_payload').inputValue());
      assert(payload.guide_state && Array.isArray(payload.history), 'guided provenance was not persisted');
    });

    await runScenario('date-and-period-conflict-overrides-gate-confirm', async () => {
      await openForm(page, baseUrl); await fillSemester(page);
      await page.locator('[data-assistant-option="B"]').click();
      await page.locator('[data-assistant-option="D"]').click();
      await page.locator('[data-assistant-custom-input]').fill('Conflict');
      await page.locator('[data-assistant-custom-submit]').click();
      await page.locator('[data-assistant-option="D"]').click();
      await page.locator('[data-assistant-custom-input]').fill('2026-09-18');
      await page.locator('[data-assistant-custom-submit]').click();
      await page.locator('[data-assistant-option="A"]').click();
      const confirm = page.locator('[data-assistant-confirm]');
      assert(await confirm.isDisabled(), 'conflict overrides must gate confirm');
      await page.locator('[data-assistant-date-override]').fill('2026-09-18');
      await page.locator('[data-assistant-period-override]').fill('第3-4节');
      assert(!(await confirm.isDisabled()), 'conflict overrides did not unlock confirm');
    });

    await runScenario('keyboard-focus-320px-and-console-clean', async () => {
      await openForm(page, baseUrl); await fillSemester(page);
      await page.locator('[data-assistant-option="A"]').focus();
      await page.keyboard.press('Enter');
      await page.locator('[data-assistant-question]').waitFor({state: 'visible'});
      await page.setViewportSize({width: 320, height: 720});
      const dimensions = await page.evaluate(() => ({
        scrollWidth: document.documentElement.scrollWidth,
        clientWidth: document.documentElement.clientWidth,
      }));
      horizontalOverflow = dimensions.scrollWidth !== dimensions.clientWidth;
      assert(!horizontalOverflow, `horizontal overflow: ${dimensions.scrollWidth} > ${dimensions.clientWidth}`);
    });
    if (consoleErrors.length === 0) pass('browser-console-errors-zero', {consoleErrors: 0});
    else failed('browser-console-errors-zero', `browser emitted ${consoleErrors.length} console/page errors`);
  } catch (error) {
    const reason = error?.message || String(error);
    SCENARIO_NAMES.forEach((name) => { if (!hasScenario(name)) blocked(name, 'browser setup failed: ' + reason); });
  } finally {
    if (browser) await browser.close();
  }
}

async function main() {
  try { staticContractChecks(); } catch (error) { failed('static-dom-contracts', error.message); }
  await runBrowserScenarios();
  const hasBlocked = scenarios.some((item) => item.status === 'BLOCKED');
  const hasFailure = scenarios.some((item) => item.status === 'FAIL');
  const hasNotRun = scenarios.some((item) => item.status === 'NOT_RUN');
  const status = hasFailure ? 'FAIL' : (hasBlocked ? 'BLOCKED' : (hasNotRun ? 'NOT_RUN' : 'PASS'));
  const allowNotRun = process.env.LISTENING_ASSISTANT_ALLOW_NOT_RUN === '1';
  process.stdout.write(JSON.stringify({
    schemaVersion: 3,
    status,
    repository: REPO_ROOT,
    runtime: runtimePath,
    mode: 'adaptive guide static contract plus optional loopback browser fixtures',
    scenarios,
    consoleErrors: browserStarted ? consoleErrors.length : null,
    horizontalOverflow,
  }, null, 2) + '\n');
  process.exitCode = hasFailure || hasBlocked ? 2 : (hasNotRun && !allowNotRun ? 2 : 0);
}

main().catch((error) => {
  process.stdout.write(JSON.stringify({
    schemaVersion: 3,
    status: 'BLOCKED',
    repository: REPO_ROOT,
    scenarios: [{name: 'script-runtime', status: 'BLOCKED', reason: error.message}],
    consoleErrors: null,
    horizontalOverflow: null,
  }, null, 2) + '\n');
  process.exitCode = 2;
});
