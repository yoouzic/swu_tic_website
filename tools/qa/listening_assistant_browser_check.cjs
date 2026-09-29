'use strict';

/**
 * Bounded browser/contract check for the universal listening assistant.
 *
 * The repository deliberately has no top-level npm manifest.  A real browser
 * run therefore requires an already-running isolated local-debug server and
 * explicit test credentials.  This script never starts a server, imports a
 * workbook, or guesses credentials.  Without those explicit inputs it still
 * runs the reviewable static/DOM contract checks and reports browser scenarios
 * as NOT_RUN rather than manufacturing PASS results.
 */

const fs = require('node:fs');
const path = require('node:path');

const REPO_ROOT = path.resolve(__dirname, '..', '..');
const TEMPLATE = path.join(REPO_ROOT, 'app', 'templates', 'user', 'lecture_form.html');
const SCRIPT = path.join(REPO_ROOT, 'app', 'static', 'js', 'listening-assistant.js');
const STYLE = path.join(REPO_ROOT, 'app', 'static', 'css', 'listening-assistant.css');
const BROWSER_TIMEOUT_MS = 15_000;

const scenarioResults = [];
const consoleErrors = [];
let horizontalOverflow = null;

function readUtf8(filePath) {
  return fs.readFileSync(filePath, 'utf8');
}

function assert(condition, message) {
  if (!condition) {
    throw new Error(message);
  }
}

function pass(name, details = {}) {
  scenarioResults.push({name, status: 'PASS', ...details});
}

function notRun(name, reason) {
  scenarioResults.push({name, status: 'NOT_RUN', reason});
}

function blocked(name, reason) {
  scenarioResults.push({name, status: 'BLOCKED', reason});
}

function staticContractChecks() {
  const template = readUtf8(TEMPLATE);
  const script = readUtf8(SCRIPT);
  const style = readUtf8(STYLE);

  const requiredTemplateHooks = [
    'data-listening-assistant',
    'assistantDate',
    'assistantRoom',
    'assistantTeacher',
    'assistantPeriod',
    'assistantSemester',
    'data-assistant-card',
    'data-assistant-none',
    'data-assistant-confirm',
    'data-assistant-backup-ack',
    'data-assistant-manual',
    'aria-live',
    'listening-assistant.js',
    'listening-assistant.css',
  ];
  for (const hook of requiredTemplateHooks) {
    assert(template.includes(hook), `template is missing contract hook: ${hook}`);
  }

  for (const endpoint of [
    '/user/api/listening-assistant/candidates',
    '/user/api/listening-assistant/fallback',
    '/user/api/listening-assistant/confirm',
  ]) {
    assert(script.includes(endpoint), `assistant script is missing endpoint: ${endpoint}`);
  }
  assert(script.includes("const STATES = Object.freeze({"), 'state machine is missing');
  for (const state of ['FIND', 'RESCUE', 'REVIEW', 'MANUAL', 'DONE']) {
    assert(script.includes(`${state}:`), `state machine is missing ${state}`);
  }
  assert(!/window\.(alert|confirm)\s*\(/.test(script), 'assistant must not use window.alert/confirm');
  assert(!/candidates\s*\[\s*0\s*\]/.test(script), 'assistant must not auto-select the first candidate');
  assert(script.includes('state.rejectedIds = [];'), 'query changes must clear rejected candidates');
  assert(script.includes('data-assistant-room-choice'), 'room conflict choice hook is missing');
  assert(script.includes('input.textContent') || script.includes('textContent'), 'user text must use textContent');
  assert(style.includes('@media'), 'assistant stylesheet must contain responsive rules');
  assert(style.includes('320px') || style.includes('min-width: 0'), 'assistant stylesheet must constrain narrow layouts');

  pass('static-dom-contracts', {
    checks: requiredTemplateHooks.length + 3 + 1 + 5 + 7,
    files: [
      path.relative(REPO_ROOT, TEMPLATE),
      path.relative(REPO_ROOT, SCRIPT),
      path.relative(REPO_ROOT, STYLE),
    ],
  });
}

function bundledPlaywrightPath() {
  const siblingRuntime = path.resolve(
    REPO_ROOT,
    '..',
    'SWU_TIC-main',
    'tmp',
    'e2e_node',
    'node_modules',
    'playwright',
  );
  return fs.existsSync(siblingRuntime) ? siblingRuntime : null;
}

function localBrowserBaseUrl(value) {
  let parsed;
  try {
    parsed = new URL(value);
  } catch {
    return null;
  }
  const localHosts = new Set(['localhost', '127.0.0.1', '[::1]', '::1']);
  if (!['http:', 'https:'].includes(parsed.protocol) || !localHosts.has(parsed.hostname)) {
    return null;
  }
  return parsed.toString().replace(/\/$/, '');
}

async function runBoundedBrowserPreflight() {
  const baseUrl = String(process.env.LISTENING_ASSISTANT_BROWSER_BASE_URL || '').trim();
  const username = String(process.env.LISTENING_ASSISTANT_BROWSER_USER || '').trim();
  const password = String(process.env.LISTENING_ASSISTANT_BROWSER_PASSWORD || '');
  const runtime = bundledPlaywrightPath();

  const browserScenarioNames = [
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

  if (!runtime) {
    for (const name of browserScenarioNames) {
      notRun(name, 'bundled Playwright runtime was not found');
    }
    return;
  }
  if (!baseUrl || !username || !password) {
    for (const name of browserScenarioNames) {
      notRun(
        name,
        'requires an already-running isolated local-debug server plus LISTENING_ASSISTANT_BROWSER_USER/PASSWORD; no credentials were guessed',
      );
    }
    return;
  }
  const localBaseUrl = localBrowserBaseUrl(baseUrl);
  if (!localBaseUrl) {
    for (const name of browserScenarioNames) {
      notRun(name, 'LISTENING_ASSISTANT_BROWSER_BASE_URL must be an explicit loopback http(s) URL');
    }
    return;
  }

  let chromium;
  try {
    ({chromium} = require(runtime));
  } catch (error) {
    for (const name of browserScenarioNames) {
      blocked(name, `bundled Playwright could not be loaded: ${error.message}`);
    }
    return;
  }

  let browser;
  try {
    browser = await chromium.launch({headless: true, timeout: BROWSER_TIMEOUT_MS});
    const page = await browser.newPage({viewport: {width: 1440, height: 900}});
    page.on('console', (message) => {
      if (message.type() === 'error') {
        consoleErrors.push(message.text());
      }
    });
    page.on('pageerror', (error) => consoleErrors.push(error.message));

    await page.goto(`${localBaseUrl}/auth/login`, {
      waitUntil: 'domcontentloaded',
      timeout: BROWSER_TIMEOUT_MS,
    });
    await page.locator('#student_id').fill(username);
    await page.locator('#password').fill(password);
    await Promise.all([
      page.waitForLoadState('domcontentloaded', {timeout: BROWSER_TIMEOUT_MS}),
      page.locator('form button[type="submit"]').click(),
    ]);
    assert(!page.url().includes('/auth/login'), 'explicit browser credentials did not authenticate');

    await page.goto(`${localBaseUrl}/user/submit_form`, {
      waitUntil: 'domcontentloaded',
      timeout: BROWSER_TIMEOUT_MS,
    });
    await page.locator('[data-listening-assistant]').waitFor({state: 'visible', timeout: BROWSER_TIMEOUT_MS});
    const desktopContract = await page.evaluate(() => ({
      hasDate: Boolean(document.querySelector('#assistantDate')),
      hasRoom: Boolean(document.querySelector('#assistantRoom')),
      hasNone: Boolean(document.querySelector('[data-assistant-none]')),
      hasConfirm: Boolean(document.querySelector('[data-assistant-confirm]')),
      scrollWidth: document.documentElement.scrollWidth,
      clientWidth: document.documentElement.clientWidth,
    }));
    assert(desktopContract.hasDate && desktopContract.hasRoom, 'browser DOM is missing date/room controls');
    assert(desktopContract.hasNone && desktopContract.hasConfirm, 'browser DOM is missing correction/confirm controls');
    pass('browser-authenticated-dom-preflight', {authenticated: true});

    await page.setViewportSize({width: 320, height: 720});
    horizontalOverflow = await page.evaluate(
      () => document.documentElement.scrollWidth !== document.documentElement.clientWidth,
    );
    if (!horizontalOverflow) {
      pass('320px-no-horizontal-overflow');
    } else {
      blocked('320px-no-horizontal-overflow', 'authenticated page has horizontal overflow');
    }

    if (consoleErrors.length === 0) {
      pass('browser-console-errors-zero', {consoleErrors: 0});
    } else {
      blocked('browser-console-errors-zero', 'page emitted console errors', {errors: consoleErrors});
    }

    for (const name of browserScenarioNames) {
      if (!scenarioResults.some((item) => item.name === name)) {
        notRun(name, 'full candidate data-flow scenario requires a seeded isolated schedule fixture');
      }
    }
  } catch (error) {
    blocked('browser-authenticated-dom-preflight', error.message);
    for (const name of browserScenarioNames) {
      if (!scenarioResults.some((item) => item.name === name)) {
        blocked(name, `browser preflight stopped: ${error.message}`);
      }
    }
  } finally {
    if (browser) {
      await browser.close().catch(() => {});
    }
  }
}

async function main() {
  let setupError = null;
  try {
    staticContractChecks();
  } catch (error) {
    setupError = error;
    blocked('static-dom-contracts', error.message);
  }

  await runBoundedBrowserPreflight();

  const hasBlocked = scenarioResults.some((item) => item.status === 'BLOCKED');
  const hasNotRun = scenarioResults.some((item) => item.status === 'NOT_RUN');
  const summary = {
    schemaVersion: 1,
    status: hasBlocked ? 'BLOCKED' : (hasNotRun ? 'NOT_RUN' : 'PASS'),
    repository: REPO_ROOT,
    mode: 'read-only contract check; browser requires explicit isolated local-debug inputs',
    scenarios: scenarioResults,
    consoleErrors: horizontalOverflow === null ? null : consoleErrors.length,
    horizontalOverflow,
  };
  if (setupError) {
    summary.setupError = setupError.message;
  }
  process.stdout.write(`${JSON.stringify(summary, null, 2)}\n`);
  process.exitCode = hasBlocked ? 2 : 0;
}

main().catch((error) => {
  process.stdout.write(`${JSON.stringify({
    schemaVersion: 1,
    status: 'BLOCKED',
    repository: REPO_ROOT,
    scenarios: [{name: 'script-runtime', status: 'BLOCKED', reason: error.message}],
    consoleErrors: null,
    horizontalOverflow: null,
  }, null, 2)}\n`);
  process.exitCode = 2;
});
