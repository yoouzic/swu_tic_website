'use strict';

/*
 * Browser acceptance check for the universal listening assistant.
 *
 * The script never starts a server, imports a workbook, guesses credentials,
 * or downloads a browser. Real browser scenarios run only when an already
 * running loopback server, explicit test credentials, and an existing
 * Playwright runtime are supplied. API responses used by those scenarios are
 * local route fixtures, so the browser exercises the UI state machine without
 * using production schedule data.
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
  'desktop-date-room-primary-review',
  'singleton-and-multi-candidate-show-none',
  'reject-then-teacher-fallback-and-backup-badge',
  'backup-confirmation-requires-acknowledgement',
  'room-conflict-requires-explicit-choice',
  'date-room-change-clears-stale-candidates',
  'manual-period-range-and-safe-user-text',
  'keyboard-focus-reaches-assistant-controls',
  '320px-no-horizontal-overflow',
  'browser-console-errors-zero',
];

const scenarios = [];
const consoleErrors = [];
let horizontalOverflow = null;
let runtimePath = null;
let browserStarted = false;
let runtimeSelectionError = null;

function readUtf8(filePath) {
  return fs.readFileSync(filePath, 'utf8');
}

function assert(condition, message) {
  if (!condition) {
    throw new Error(message);
  }
}

function pass(name, details) {
  scenarios.push({name, status: 'PASS', ...(details || {})});
}

function notRun(name, reason) {
  scenarios.push({name, status: 'NOT_RUN', reason});
}

function blocked(name, reason) {
  scenarios.push({name, status: 'BLOCKED', reason});
}

function failed(name, reason) {
  scenarios.push({name, status: 'FAIL', reason});
}

function hasScenario(name) {
  return scenarios.some((item) => item.name === name);
}

function staticContractChecks() {
  const template = readUtf8(TEMPLATE);
  const assistantScript = readUtf8(SCRIPT);
  const style = readUtf8(STYLE);
  const templateHooks = [
    'data-listening-assistant',
    'id="assistantDate"',
    'id="assistantRoom"',
    'id="assistantTeacher"',
    'id="assistantPeriod"',
    'id="assistantSemester"',
    'data-assistant-card',
    'data-assistant-none',
    'data-assistant-confirm',
    'data-assistant-backup-ack',
    'data-assistant-manual',
    'aria-live',
    'listening-assistant.js',
    'listening-assistant.css',
  ];
  templateHooks.forEach((hook) => assert(template.includes(hook), 'template is missing ' + hook));
  [
    '/user/api/listening-assistant/candidates',
    '/user/api/listening-assistant/fallback',
    '/user/api/listening-assistant/confirm',
  ].forEach((endpoint) => {
    assert(assistantScript.includes(endpoint), 'assistant script is missing ' + endpoint);
  });
  assert(assistantScript.includes('const STATES = Object.freeze({'), 'state machine is missing');
  ['FIND:', 'RESCUE:', 'REVIEW:', 'MANUAL:', 'DONE:'].forEach((state) => {
    assert(assistantScript.includes(state), 'state machine is missing ' + state);
  });
  assert(!/window\.(alert|confirm)\s*\(/.test(assistantScript), 'assistant must not use window.alert/confirm');
  assert(!/candidates\s*\[\s*0\s*\]/.test(assistantScript), 'assistant must not auto-select first candidate');
  assert(assistantScript.includes('state.rejectedIds = [];'), 'query changes must clear rejected candidates');
  assert(assistantScript.includes('data-assistant-room-choice'), 'room conflict choice hook is missing');
  assert(assistantScript.includes('textContent'), 'user text should be rendered as text');
  assert(style.includes('min-width: 0'), 'assistant style should constrain narrow layouts');
  assert(style.includes('@media'), 'assistant style should include responsive rules');
  pass('static-dom-contracts', {checks: templateHooks.length + 15});
}

function isLoopbackUrl(value) {
  try {
    const parsed = new URL(value);
    return parsed.protocol === 'http:' &&
      ['127.0.0.1', 'localhost', '[::1]'].includes(parsed.hostname);
  } catch (error) {
    return false;
  }
}

function isWithin(root, target) {
  const relative = path.relative(root, target);
  return relative === '' || (
    relative !== '..' &&
    !relative.startsWith('..' + path.sep) &&
    !path.isAbsolute(relative)
  );
}

function candidate(overrides) {
  const sourceKind = overrides.source_kind || 'primary';
  return {
    candidate_id: overrides.candidate_id,
    lecture_date: '2026-09-18',
    room: overrides.room || '8-309',
    period: overrides.period || [3, 4],
    course_title: overrides.course_title || '数据结构',
    teacher_name: overrides.teacher_name || '张老师',
    teacher_college: '计算机学院',
    student_grade_class: '2024级计算机1班',
    course_code: overrides.course_code || 'C101',
    selection_code: overrides.selection_code || 'S101',
    source_kind: sourceKind,
    source_label: sourceKind === 'backup' ? '备用课表线索 · 需核对' : '当前权威课表',
    source_batch_id: sourceKind === 'backup' ? 'backup-fixture-1' : 'primary-fixture-1',
    conflicts: overrides.conflicts || [],
    needs_confirmation: overrides.needs_confirmation === true,
  };
}

const PRIMARY_MULTI = [
  candidate({candidate_id: 'primary-fixture-1', course_title: '数据结构'}),
  candidate({candidate_id: 'primary-fixture-2', course_title: '算法设计', selection_code: 'S102'}),
];
const PRIMARY_SINGLETON = [
  candidate({
    candidate_id: 'primary-xss-fixture',
    room: '9-101',
    course_title: '<img src=x onerror=alert(1)>安全文本课程',
  }),
];
const BACKUP = [
  candidate({
    candidate_id: 'backup-fixture-1',
    room: '9-101',
    course_title: '备用线索课程',
    source_kind: 'backup',
    needs_confirmation: true,
  }),
];

async function fulfillJson(route, payload, status) {
  await route.fulfill({
    status: status || 200,
    contentType: 'application/json',
    body: JSON.stringify(payload),
  });
}

async function rejectFixture(route, error) {
  await fulfillJson(route, {
    success: false,
    data: {},
    message: 'browser fixture contract: ' + error.message,
  }, 400);
}

function parseRequestBody(route) {
  return JSON.parse(route.request().postData() || '{}');
}

async function installApiFixtures(page) {
  await page.route('**/user/api/listening-assistant/candidates*', async (route) => {
    const query = new URL(route.request().url()).searchParams;
    try {
      assert(route.request().method() === 'GET', 'candidate lookup must use GET');
      assert(query.get('date') === '2026-09-18', 'candidate lookup date is missing or invalid');
      assert(query.get('room') || query.get('teacher'), 'candidate lookup anchor is missing');
    } catch (error) {
      await rejectFixture(route, error);
      return;
    }
    const candidates = query.get('room') === '9-101' ? PRIMARY_SINGLETON : PRIMARY_MULTI;
    await fulfillJson(route, {
      success: true,
      data: {
        candidates,
        always_show_none: true,
        backup_source_batch_id: 'backup-fixture-1',
        backup_rescue_available: true,
        skipped_invalid_rows: 0,
      },
      message: '',
    });
  });

  await page.route('**/user/api/listening-assistant/fallback*', async (route) => {
    let request;
    try {
      request = parseRequestBody(route);
      assert(route.request().method() === 'POST', 'fallback lookup must use POST');
      assert(request.date === '2026-09-18', 'fallback date is missing or invalid');
      assert(request.teacher === '张老师', 'fallback teacher is missing or invalid');
      assert(request.explicit_fallback === true, 'fallback must be explicitly requested');
      assert(request.source_batch_id === 'backup-fixture-1', 'fallback batch provenance is missing');
      assert(['no_result', 'rejected_candidates'].includes(request.reason), 'fallback reason is invalid');
    } catch (error) {
      await rejectFixture(route, error);
      return;
    }
    await fulfillJson(route, {
      success: true,
      data: {
        candidates: BACKUP,
        source_batch_id: 'backup-fixture-1',
        fallback_reason: 'rejected_candidates',
        needs_confirmation: true,
      },
      message: '',
    });
  });

  await page.route('**/user/api/listening-assistant/confirm*', async (route) => {
    try {
      const request = parseRequestBody(route);
      assert(route.request().method() === 'POST', 'confirmation must use POST');
      assert(request.stage === 'confirmed', 'confirmation stage is missing');
      assert(request.query && request.query.lecture_date === '2026-09-18', 'confirmation query is missing date');
      assert(request.candidate_id, 'confirmation candidate ID is missing');
      const allowedIds = new Set(['primary-fixture-1', 'primary-fixture-2', 'backup-fixture-1']);
      assert(allowedIds.has(request.candidate_id), 'confirmation candidate ID is not from the fixture');
      assert(request.source_kind === 'primary' || request.source_kind === 'backup', 'confirmation source kind is invalid');
      assert(
        request.source_kind === 'backup'
          ? request.candidate_id === 'backup-fixture-1'
          : request.candidate_id !== 'backup-fixture-1',
        'confirmation source kind and candidate ID do not match',
      );
      const selected = request.source_kind === 'backup' ? BACKUP[0] : PRIMARY_MULTI.find(
        (item) => item.candidate_id === request.candidate_id,
      );
      assert(selected, 'confirmation candidate/source mismatch');
      const location = request.overrides && (
        request.overrides.lecture_location || request.overrides.room
      ) || selected.room;
      await fulfillJson(route, {
        success: true,
        data: {
          candidate: selected,
          query: request.query || {},
          field_snapshot: {
            lecture_date: selected.lecture_date,
            lecture_location: location,
            teacher_name: selected.teacher_name,
            teacher_college: selected.teacher_college,
            course_title: selected.course_title,
            student_grade_class: selected.student_grade_class,
            class_period: '第3-4节',
          },
          confirmation: {
            confirmed: true,
            source_kind: selected.source_kind,
            acknowledged_source: request.acknowledged_source === true,
            explicit_fallback: request.explicit_fallback === true,
          },
        },
        message: '',
      });
    } catch (error) {
      await rejectFixture(route, error);
    }
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
      runtimeSelectionError = 'LISTENING_ASSISTANT_PLAYWRIGHT_MODULE must be inside a repository bundled runtime directory';
      return null;
    }
    candidates.push(override);
  }
  candidates.push(
    path.resolve(REPO_ROOT, '..', 'SWU_TIC-main', 'tmp', 'e2e_node', 'node_modules', 'playwright'),
  );
  for (const modulePath of candidates) {
    try {
      require.resolve(modulePath);
      return modulePath;
    } catch (error) {
      // Try the next existing runtime.
    }
  }
  return null;
}

async function waitForCards(page, count) {
  await page.waitForFunction(
    (expected) => document.querySelectorAll('[data-assistant-card]').length === expected,
    count,
    {timeout: TIMEOUT_MS},
  );
}

async function waitForState(page, state) {
  await page.waitForFunction(
    (expected) => document.querySelector('[data-listening-assistant]')?.dataset.assistantState === expected,
    state,
    {timeout: TIMEOUT_MS},
  );
}

async function openForm(page, baseUrl) {
  await page.goto(baseUrl.replace(/\/$/, '') + '/user/submit_form', {
    waitUntil: 'domcontentloaded',
    timeout: TIMEOUT_MS,
  });
  await page.locator('[data-listening-assistant]').waitFor({
    state: 'visible',
    timeout: TIMEOUT_MS,
  });
}

async function fillQuery(page, values) {
  await page.locator('#assistantDate').fill(values.date || '2026-09-18');
  await page.locator('#assistantRoom').fill(values.room || '8-309');
  await page.locator('#assistantTeacher').fill(values.teacher || '张老师');
  await page.locator('#assistantSemester').fill(values.semester || '2026-2027-1');
}

async function search(page, expectedCount) {
  await page.locator('[data-assistant-search]').click();
  await waitForCards(page, expectedCount);
}

async function loadBackupReview(page, baseUrl) {
  await openForm(page, baseUrl);
  await fillQuery(page, {room: '8-309'});
  await search(page, 2);
  await page.locator('[data-assistant-none]').click();
  await waitForState(page, 'rescue');
  await page.locator('[data-assistant-rescue]').click();
  await waitForCards(page, 1);
  await page.locator('[data-assistant-select]').click();
  await waitForState(page, 'review');
}

async function runScenario(name, callback) {
  try {
    await callback();
    pass(name);
  } catch (error) {
    failed(name, error && error.message ? error.message : String(error));
  }
}

async function login(page, baseUrl, username, password) {
  await page.goto(baseUrl.replace(/\/$/, '') + '/auth/login', {
    waitUntil: 'domcontentloaded',
    timeout: TIMEOUT_MS,
  });
  await page.locator('#student_id').fill(username);
  await page.locator('#password').fill(password);
  await page.locator('form button[type="submit"]').first().click();
  try {
    await page.waitForURL((url) => !url.toString().includes('/auth/login'), {timeout: TIMEOUT_MS});
  } catch (error) {
    // The assertion below reports a concise authentication failure.
  }
  assert(!page.url().includes('/auth/login'), 'explicit browser credentials did not authenticate');
}

async function runBrowserScenarios() {
  const baseUrl = String(process.env.LISTENING_ASSISTANT_BROWSER_BASE_URL || '').trim();
  const username = String(process.env.LISTENING_ASSISTANT_BROWSER_USER || '').trim();
  const password = String(process.env.LISTENING_ASSISTANT_BROWSER_PASSWORD || '');

  if (!baseUrl || !username || !password) {
    SCENARIO_NAMES.forEach((name) => notRun(
      name,
      'requires an already-running isolated loopback server and explicit browser credentials',
    ));
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
  try {
    playwright = require(runtimePath);
  } catch (error) {
    SCENARIO_NAMES.forEach((name) => blocked(name, 'Playwright runtime could not be loaded: ' + error.message));
    return;
  }

  let browser;
  try {
    browser = await playwright.chromium.launch({headless: true, timeout: TIMEOUT_MS});
    browserStarted = true;
    const page = await browser.newPage({viewport: {width: 1440, height: 900}});
    page.on('console', (message) => {
      if (message.type() === 'error') {
        consoleErrors.push(message.text());
      }
    });
    page.on('pageerror', (error) => consoleErrors.push(error.message));
    await installApiFixtures(page);
    await login(page, baseUrl, username, password);

    await runScenario('desktop-date-room-primary-review', async () => {
      await openForm(page, baseUrl);
      await fillQuery(page, {});
      await search(page, 2);
      assert(await page.locator('[data-assistant-card]').count() === 2, 'expected two primary cards');
      assert(await page.locator('[data-source-kind="primary"]').count() === 2, 'primary source badges missing');
      await page.locator('[data-assistant-select]').first().click();
      await waitForState(page, 'review');
      assert(await page.locator('[data-assistant-review]').isVisible(), 'review view did not open');
    });

    await runScenario('singleton-and-multi-candidate-show-none', async () => {
      await openForm(page, baseUrl);
      await fillQuery(page, {room: '8-309'});
      await search(page, 2);
      assert(await page.locator('[data-assistant-none]').isVisible(), 'multi-candidate none action missing');
      await openForm(page, baseUrl);
      await fillQuery(page, {room: '9-101'});
      await search(page, 1);
      assert(await page.locator('[data-assistant-card]').count() === 1, 'expected singleton card');
      assert(await page.locator('[data-assistant-none]').isVisible(), 'singleton none action missing');
    });

    await runScenario('reject-then-teacher-fallback-and-backup-badge', async () => {
      await openForm(page, baseUrl);
      await fillQuery(page, {});
      await search(page, 2);
      await page.locator('[data-assistant-reject]').first().click();
      assert(await page.locator('[data-assistant-card]').count() === 1, 'reject did not remove one card');
      await page.locator('[data-assistant-none]').click();
      await waitForState(page, 'rescue');
      await page.locator('[data-assistant-rescue]').click();
      await waitForCards(page, 1);
      assert(await page.locator('[data-source-kind="backup"]').count() === 1, 'backup badge missing');
      assert((await page.locator('[data-assistant-candidates]').textContent()).includes('备用课表线索'), 'backup label missing');
    });

    await runScenario('backup-confirmation-requires-acknowledgement', async () => {
      await loadBackupReview(page, baseUrl);
      const confirm = page.locator('[data-assistant-confirm]');
      assert(await confirm.isDisabled(), 'backup confirm must be disabled before source acknowledgement');
      assert((await page.locator('[data-assistant-review-hint]').textContent()).includes('备用来源'), 'acknowledgement hint missing');
    });

    await runScenario('room-conflict-requires-explicit-choice', async () => {
      await loadBackupReview(page, baseUrl);
      const confirm = page.locator('[data-assistant-confirm]');
      assert(await page.locator('[data-assistant-room-choice]').count() === 2, 'room conflict choices missing');
      assert(await confirm.isDisabled(), 'room conflict should disable confirm');
      await page.locator('[data-assistant-room-choice="candidate"]').check();
      assert(await confirm.isDisabled(), 'acknowledgement should still be required');
      await page.locator('[data-assistant-backup-ack]').check();
      await page.waitForTimeout(50);
      assert(!(await confirm.isDisabled()), 'confirm did not enable after conflict and source choices');
      await confirm.click();
      await waitForState(page, 'done');
    });

    await runScenario('date-room-change-clears-stale-candidates', async () => {
      await openForm(page, baseUrl);
      await fillQuery(page, {});
      await search(page, 2);
      assert(await page.locator('[data-assistant-card]').count() === 2, 'fixture cards missing');
      await page.locator('#assistantRoom').fill('9-101');
      await waitForCards(page, 0);
      await waitForState(page, 'find');
      assert((await page.locator('[data-assistant-status]').textContent()).includes('查询条件已改变'), 'stale-query status missing');
    });

    await runScenario('manual-period-range-and-safe-user-text', async () => {
      await openForm(page, baseUrl);
      await fillQuery(page, {room: '9-101'});
      await search(page, 1);
      const card = page.locator('[data-assistant-card]').first();
      assert((await card.locator('.listening-assistant__candidate-title').textContent()).includes('<img'), 'fixture text was not preserved');
      assert(await card.locator('img').count() === 0, 'candidate text created an image element');
      await page.locator('[data-assistant-none]').click();
      await waitForState(page, 'rescue');
      await page.locator('[data-assistant-manual]').click();
      await waitForState(page, 'manual');
      const optionValues = await page.locator('#start_period option, #end_period option').evaluateAll(
        (options) => options.map((option) => option.value),
      );
      assert(optionValues.includes('1') && optionValues.includes('14'), 'manual period range is not 1-14');
      await page.locator('#start_period').selectOption('4');
      await page.locator('#end_period').selectOption('3');
      assert(await page.locator('#end_period').inputValue() === '', 'invalid period range was not cleared');
    });

    await runScenario('keyboard-focus-reaches-assistant-controls', async () => {
      await openForm(page, baseUrl);
      await fillQuery(page, {});
      await search(page, 2);
      await page.locator('#assistantDate').focus();
      await page.keyboard.press('Tab');
      assert(await page.evaluate(() => document.activeElement && document.activeElement.id === 'assistantRoom'), 'Tab did not reach room');
      await page.locator('[data-assistant-card]').first().focus();
      await page.keyboard.press('Enter');
      await waitForState(page, 'review');
      assert(await page.evaluate(() => document.activeElement && document.activeElement.matches('[data-assistant-confirm]')), 'card Enter did not focus confirm');
      await page.locator('[data-assistant-none]').focus();
      await page.keyboard.press('Enter');
      await waitForState(page, 'rescue');
    });

    await runScenario('320px-no-horizontal-overflow', async () => {
      await openForm(page, baseUrl);
      await fillQuery(page, {});
      await search(page, 2);
      await page.locator('[data-assistant-select]').first().click();
      await waitForState(page, 'review');
      await page.setViewportSize({width: 320, height: 720});
      const dimensions = await page.evaluate(() => ({
        scrollWidth: document.documentElement.scrollWidth,
        clientWidth: document.documentElement.clientWidth,
      }));
      horizontalOverflow = dimensions.scrollWidth !== dimensions.clientWidth;
      assert(!horizontalOverflow, 'horizontal overflow: ' + dimensions.scrollWidth + ' > ' + dimensions.clientWidth);
    });

    if (consoleErrors.length === 0) {
      pass('browser-console-errors-zero', {consoleErrors: 0});
    } else {
      failed('browser-console-errors-zero', 'browser emitted ' + consoleErrors.length + ' console/page errors');
    }
  } catch (error) {
    const reason = error && error.message ? error.message : String(error);
    SCENARIO_NAMES.forEach((name) => {
      if (!hasScenario(name)) {
        blocked(name, 'browser setup failed: ' + reason);
      }
    });
  } finally {
    if (browser) {
      try {
        await browser.close();
      } catch (error) {
        failed('browser-resource-close', 'browser close failed: ' + error.message);
      }
    }
  }
}

async function main() {
  try {
    staticContractChecks();
  } catch (error) {
    failed('static-dom-contracts', error.message);
  }
  await runBrowserScenarios();
  const hasBlocked = scenarios.some((item) => item.status === 'BLOCKED');
  const hasFailure = scenarios.some((item) => item.status === 'FAIL');
  const hasNotRun = scenarios.some((item) => item.status === 'NOT_RUN');
  const status = hasFailure ? 'FAIL' : (hasBlocked ? 'BLOCKED' : (hasNotRun ? 'NOT_RUN' : 'PASS'));
  const allowNotRun = process.env.LISTENING_ASSISTANT_ALLOW_NOT_RUN === '1';
  const summary = {
    schemaVersion: 2,
    status,
    repository: REPO_ROOT,
    runtime: runtimePath,
    mode: 'static contract plus optional isolated browser UI fixtures',
    scenarios,
    consoleErrors: browserStarted ? consoleErrors.length : null,
    horizontalOverflow,
  };
  process.stdout.write(JSON.stringify(summary, null, 2) + '\n');
  process.exitCode = hasFailure || hasBlocked ? 2 : (hasNotRun && !allowNotRun ? 2 : 0);
}

main().catch((error) => {
  process.stdout.write(JSON.stringify({
    schemaVersion: 2,
    status: 'BLOCKED',
    repository: REPO_ROOT,
    scenarios: [{name: 'script-runtime', status: 'BLOCKED', reason: error.message}],
    consoleErrors: null,
    horizontalOverflow: null,
  }, null, 2) + '\n');
  process.exitCode = 2;
});
