# SWU TIC Automated Form Review System Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a durable, extensible review-assistance layer that checks text, schedules, historical patterns, and DeepSeek V4 Flash output, then only classifies and marks forms for human review without ever approving or rejecting a form automatically.

**Architecture:** Keep the existing Flask application as a modular monolith. Add a `review_automation` package with versioned schedule/rule data, immutable assessments/findings, a deterministic rule registry, a validated DeepSeek adapter, and Redis-backed Celery tasks. Preserve existing routes and human-review semantics through thin compatibility adapters; expose the new evidence in the current settings and review-queue UI.

**Tech Stack:** Python 3.10, Flask 2.3, Flask-SQLAlchemy 3, SQLite/PostgreSQL-compatible SQLAlchemy models, pandas/openpyxl, Celery 5, Redis, OpenAI Python SDK against DeepSeek's OpenAI-compatible Chat Completions endpoint, Pydantic 2, Bootstrap 5, vanilla JavaScript, `unittest`.

**Approved design:** `docs/superpowers/specs/2026-08-10-automated-form-review-system-design.md`

---

## File map

Create these production files:

- `app/review_automation/__init__.py` — Flask/Celery/CLI registration and model import boundary.
- `app/review_automation/contracts.py` — enums and typed rule/assessment contracts.
- `app/review_automation/models.py` — schedule datasets, rule revisions, batches, assessments, findings, and audit logs.
- `app/review_automation/fingerprints.py` — normalized form and dependency fingerprinting.
- `app/review_automation/classification.py` — four-category precedence rules.
- `app/review_automation/service.py` — one-form assessment orchestration.
- `app/review_automation/permissions.py` — shared super-admin and center-review permission guards.
- `app/review_automation/routes.py` — new automation JSON API; starts as a health endpoint and grows behind stable URLs.
- `app/review_automation/cli.py` — idempotent schema/rule initialization and synthetic compatibility checks.
- `app/review_automation/celery_app.py` — Flask-aware Celery configuration.
- `app/review_automation/schedules/{__init__,aliases,normalization,importer,repository}.py` — versioned schedule import and lookup.
- `app/review_automation/rules/{__init__,registry,text,schedule,history}.py` — extensible rule handlers.
- `app/review_automation/llm/{__init__,client,prompts,schemas}.py` — DeepSeek structured review adapter.
- `app/review_automation/tasks/{__init__,review}.py` — per-form tasks, chord aggregation, retry, and cancellation.
- `celery_worker.py` — worker entrypoint.
- `app/static/js/automation-center.js` — settings uploads, rule editor, health, and batch progress.

Modify these production files:

- `requirements.txt`, `Pipfile`, `.env.example` — runtime dependencies and safe configuration.
- `app/app.py` — register automation package and Celery after `db.init_app`.
- `app/blueprints/admin.py` — compatibility adapters and assessment summaries in existing review APIs.
- `app/templates/admin/_settings_automation.html` — replace legacy Excel-run panel with data/rule/service management.
- `app/templates/admin/system_management.html` — load `automation-center.js`.
- `app/templates/admin/review_forms.html` — risk filter/badges, evidence drawer, external-transfer confirmation, and progress UI.
- `app/templates/admin/auto_review_results.html` — render batch categories/evidence instead of legacy pass/fail.
- `app/static/css/style.css` — risk badge, evidence drawer, coverage, progress, and responsive styles.
- `README.md` — local Redis/worker/API configuration and operating procedure.

Create these tests and fixtures:

- `tests/fixtures/automation/` — synthetic/de-identified workbooks only.
- `tests/automation_test_utils.py`
- `tests/test_automation_contracts.py`
- `tests/test_automation_models.py`
- `tests/test_schedule_normalization.py`
- `tests/test_schedule_importer.py`
- `tests/test_automation_text_rules.py`
- `tests/test_automation_business_rules.py`
- `tests/test_automation_classification.py`
- `tests/test_deepseek_review_client.py`
- `tests/test_automation_service.py`
- `tests/test_automation_tasks.py`
- `tests/test_automation_routes.py`
- `tests/test_automation_templates.py`
- `tests/test_automation_integration.py`

Never copy any file from the outer `demo/` directory into Git. Tests must build synthetic rows containing invented names, numbers, phones, courses, and classes.

## Execution-session protocol

All implementation work is execution work and must be performed in visible Codex tasks created by the main task with model `gpt-5.6-luna` and reasoning effort `max`. The main task owns this plan, creates/monitors each task, reviews its diff and evidence, and integrates only accepted work.

Use these dependency-ordered execution packages:

1. `automation_foundation` — Tasks 1–3.
2. `automation_schedules` — Tasks 4–5; starts only after package 1 is accepted.
3. `automation_rules` — Tasks 6–8; starts after packages 1–2 are accepted.
4. `automation_deepseek_celery` — Tasks 9–11; starts after package 3 is accepted.
5. `automation_web_ui` — Tasks 12–14; starts after package 4 is accepted.
6. `automation_final_qa` — Task 15; starts after all implementation packages are accepted.

For each package, the worker must:

- commit only its scoped files in small task commits;
- include the exact failing RED command/output summary before implementation and passing GREEN command/output summary after implementation;
- run `git diff --check` and report `git status --short`;
- never read or print real PII from `demo/`;
- never push any remote branch;
- stop and report if a required change falls outside that package.

The main task rejects a package if it changes `LectureForm.status`, `reviewer_id`, `review_time`, or `review_comment` outside the existing human-review endpoints; stores an API key or raw full-model payload in logs; removes a compatibility route; or lacks genuine RED evidence.

---

### Task 1: Add runtime configuration and package bootstrap

**Files:**

- Create: `app/review_automation/__init__.py`
- Create: `app/review_automation/celery_app.py`
- Create: `app/review_automation/routes.py` (minimal health endpoint; expanded in Task 12)
- Create: `celery_worker.py`
- Modify: `app/app.py`
- Modify: `requirements.txt`
- Modify: `Pipfile`
- Modify: `.env.example`
- Test: `tests/test_automation_contracts.py`

- [ ] **Step 1: Write a failing bootstrap/configuration test**

```python
# tests/test_automation_contracts.py
import unittest

from app.app import app


class AutomationBootstrapTest(unittest.TestCase):
    def test_default_configuration_is_safe_and_explicit(self):
        self.assertEqual(app.config['DEEPSEEK_BASE_URL'], 'https://api.deepseek.com')
        self.assertEqual(app.config['DEEPSEEK_MODEL'], 'deepseek-v4-flash')
        self.assertTrue(app.config['DEEPSEEK_THINKING_ENABLED'])
        self.assertFalse(app.config['CELERY_TASK_ALWAYS_EAGER'])
        self.assertNotIn('DEEPSEEK_API_KEY', app.config.get('AUTOMATION_PUBLIC_CONFIG', {}))

    def test_automation_blueprint_is_registered(self):
        self.assertIn('review_automation.health', {rule.endpoint for rule in app.url_map.iter_rules()})


if __name__ == '__main__':
    unittest.main()
```

- [ ] **Step 2: Run RED and confirm the missing config/endpoint failure**

```powershell
$py = 'D:\Users\yoouzico\Desktop\worksoace_codex\students_work\bumen\swu_tic_website\SWU_TIC-main\.venv\Scripts\python.exe'
& $py -m unittest tests.test_automation_contracts -v
```

Expected: failure for missing `DEEPSEEK_BASE_URL` and/or missing `review_automation.health`.

- [ ] **Step 3: Add dependencies and environment keys**

Append equivalent entries to both dependency files:

```text
celery==5.5.3
redis==6.4.0
openai>=1.109.0,<3
pydantic>=2.11.0,<3
```

Add to `.env.example` without a real key:

```dotenv
# Automated review worker
CELERY_BROKER_URL=redis://127.0.0.1:6379/0
CELERY_RESULT_BACKEND=redis://127.0.0.1:6379/1
CELERY_TASK_ALWAYS_EAGER=0
AUTOMATION_UPLOAD_DIR=data/storage/uploads/automation

# DeepSeek: complete forms are sent externally only after an administrator confirms a batch
DEEPSEEK_API_KEY=
DEEPSEEK_BASE_URL=https://api.deepseek.com
DEEPSEEK_MODEL=deepseek-v4-flash
DEEPSEEK_THINKING_ENABLED=1
DEEPSEEK_REASONING_EFFORT=high
DEEPSEEK_TIMEOUT_SECONDS=60
DEEPSEEK_MAX_RETRIES=3
DEEPSEEK_PROMPT_VERSION=2026-08-10-v1
```

- [ ] **Step 4: Implement Flask-aware Celery and registration**

`create_celery(flask_app)` must use a custom `Task` that enters `flask_app.app_context()`, JSON serializers only, `task_track_started=True`, `task_acks_late=True`, `worker_prefetch_multiplier=1`, and config sourced from Flask. `celery_worker.py` must expose `celery_app` without starting Flask's development server.

`init_review_automation(app)` must import models, register the blueprint and CLI, create the upload directory, initialize Celery, and return the Celery object. Add configuration in `app/app.py` with existing `env_value`, `env_bool`, and `env_int` helpers. Never include the API key in `AUTOMATION_PUBLIC_CONFIG`.

The initial `/admin/api/automation/health` response may be a minimal authenticated placeholder returning:

```json
{"success": true, "services": {"redis": "unchecked", "worker": "unchecked", "deepseek": "configured|missing"}}
```

- [ ] **Step 5: Run GREEN and existing route smoke tests**

```powershell
& $py -m unittest tests.test_automation_contracts tests.test_admin_auto_review_routes -v
```

Expected: all tests pass and old automatic-review endpoints remain registered.

- [ ] **Step 6: Commit**

```powershell
git add app/review_automation app/app.py celery_worker.py requirements.txt Pipfile .env.example tests/test_automation_contracts.py
git commit -m "feat: bootstrap automated review runtime"
```

### Task 2: Define typed contracts and immutable automation tables

**Files:**

- Create: `app/review_automation/contracts.py`
- Create: `app/review_automation/models.py`
- Create: `tests/automation_test_utils.py`
- Create: `tests/test_automation_models.py`
- Modify: `app/review_automation/__init__.py`

- [ ] **Step 1: Write failing model and enum tests**

Cover exact enum values:

```python
ReviewCategory.CLEAR.value == '无明显风险'
ReviewCategory.REVIEW.value == '建议复核'
ReviewCategory.HIGH_RISK.value == '高风险疑似假表'
ReviewCategory.UNKNOWN.value == '系统无法判断'
EvidenceStrength.APPROXIMATE.value == 'approximate'
EvidenceStrength.EXACT.value == 'exact'
```

In a temporary SQLite database, call `db.create_all()` and assert that these tables exist:

```text
automation_schedule_datasets
automation_school_schedule_entries
automation_listener_class_mappings
automation_personal_schedule_slots
automation_schedule_import_issues
automation_rule_revisions
automation_review_batches
automation_review_assessments
automation_review_findings
automation_audit_logs
```

Also assert that two assessments cannot share a fingerprint and that saving an assessment does not change the linked `LectureForm.status`.

- [ ] **Step 2: Run RED**

```powershell
& $py -m unittest tests.test_automation_models -v
```

Expected: import/table failures because contracts and models do not yet exist.

- [ ] **Step 3: Implement contracts**

Define `str, Enum` classes for `ReviewCategory`, `FindingSeverity` (`info`, `review`, `high`, `unknown`), `FindingSource` (`rule`, `schedule`, `history`, `llm`, `system`), `EvidenceStrength` (`weak`, `approximate`, `exact`), `ScheduleCoverage` (`none`, `basic`, `complete`), `BatchStatus` (`queued`, `running`, `completed`, `completed_with_errors`, `cancel_requested`, `cancelled`, `failed`), and `DatasetStatus` (`staged`, `active`, `retired`, `failed`).

Add frozen dataclasses `FindingDraft`, `NormalizedForm`, and `AssessmentDraft`. `FindingDraft` must carry `rule_key`, source, severity, title, message, objective flag, evidence strength, and a JSON-safe evidence dictionary.

- [ ] **Step 4: Implement new-table-only SQLAlchemy models**

Use the fields in the design plus these mandatory constraints:

- `ScheduleDataset`: UUID/string primary key, `kind`, `semester`, SHA-256, original safe filename, status, row/error counts, summary JSON text, creator and timestamps; unique `(kind, semester, sha256)`.
- `SchoolScheduleEntry`: dataset FK plus normalized teacher/college/course/class/week/day/period/location columns and indexes on `(dataset_id, weekday, start_period, end_period)` and `(dataset_id, teacher_name)`.
- `ListenerClassMapping`: dataset FK, listener number/student ID/admin class/match status; require at least one identity in service validation.
- `PersonalScheduleSlot`: dataset FK, identity, course, week JSON, weekday, start/end period, source row.
- `ScheduleImportIssue`: dataset FK, row, code, message, redacted value excerpt.
- `ReviewRuleRevision`: `rule_key`, integer version, handler, enabled, severity, parameters JSON, creator/time; unique `(rule_key, version)`.
- `ReviewBatch`: UUID/string primary key, Celery root ID, status, requester, counts, snapshot JSON, cancel flag, timestamps.
- `ReviewAssessment`: form FK, batch FK, classification, coverage, unique fingerprint, dependency IDs/versions, model/prompt identifiers, validated model JSON, editable suggestion draft, error code/message, timestamp.
- `ReviewFinding`: assessment FK, source/rule/severity/title/message/objective/evidence-strength/evidence JSON.
- `AutomationAuditLog`: actor, action, target type/ID, details JSON, timestamp.

Do not add or alter any column in `users` or `lecture_forms`.

- [ ] **Step 5: Run GREEN**

```powershell
& $py -m unittest tests.test_automation_models -v
```

Expected: all table, constraint, and status-immutability tests pass.

- [ ] **Step 6: Commit**

```powershell
git add app/review_automation/contracts.py app/review_automation/models.py app/review_automation/__init__.py tests/automation_test_utils.py tests/test_automation_models.py
git commit -m "feat: add automated review data model"
```

### Task 3: Add idempotent schema initialization and seeded rule revisions

**Files:**

- Create: `app/review_automation/cli.py`
- Modify: `app/review_automation/__init__.py`
- Modify: `app/app.py`
- Modify: `tests/test_automation_models.py`

- [ ] **Step 1: Write failing CLI idempotency tests**

Invoke `flask auto-review init-schema` twice against a temporary SQLite database. Both invocations must exit 0; exactly one version-1 row must exist for every seed key:

```text
feedback_required_prefix
feedback_min_length
common_word_confusion
personal_schedule_conflict
class_schedule_conflict
same_college_teacher
witness_reused_across_weeks
witness_phone_name_conflict
consecutive_teacher_weeks
same_listener_same_slot
school_schedule_mismatch
feedback_similarity
```

- [ ] **Step 2: Run RED, then implement `auto-review init-schema`**

The command must import automation models, call `db.create_all()` for new tables, and insert missing seed revisions without changing an existing revision. Seed parameters must be JSON objects; use `handler` values registered later rather than executable text.

Important seed values:

```json
{"required_prefix":"该老师"}
{"minimum_characters":50}
{"minimum_distinct_weeks":2,"high_risk_candidate_weeks":3,"identity":"phone_primary"}
{"maximum_week_gap":1}
```

Seed `common_word_confusion` with configurable suspicious regex/message pairs for obvious `的/地/得` contexts, but only emit suggestions and never rewrite form text.

- [ ] **Step 3: Make normal database initialization discover the tables**

Ensure `init_review_automation(app)` imports `models` before `init_database()` can call `db.create_all()`. Do not add a destructive migration or rebuild an existing table.

- [ ] **Step 4: Run the focused suite twice**

```powershell
& $py -m unittest tests.test_automation_models -v
& $py -m unittest tests.test_automation_models -v
```

Expected: both runs pass with no duplicate seed revisions.

- [ ] **Step 5: Commit**

```powershell
git add app/review_automation/cli.py app/review_automation/__init__.py app/app.py tests/test_automation_models.py
git commit -m "feat: initialize automated review schema"
```

### Task 4: Normalize dates, weeks, weekdays, periods, identities, and aliases

**Files:**

- Create: `app/review_automation/schedules/__init__.py`
- Create: `app/review_automation/schedules/aliases.py`
- Create: `app/review_automation/schedules/normalization.py`
- Create: `tests/test_schedule_normalization.py`

- [ ] **Step 1: Write a failing normalization matrix**

Cover at least:

- dates: Excel serial/date/datetime, `2026-04-13`, `2026/4/13`, `4月13日`, strings containing a weekday;
- weeks: `1-16周`, `1-16周(双)`, `1,3,5-9周`, Chinese punctuation, blank/invalid values;
- weekdays: `星期一`, `周一`, `一`, `1`;
- periods: `1-2节`, `第3、4节`, `0102`, integer cells;
- identity: preserve leading zeroes and normalize whitespace without converting an identifier to float text;
- college/class names: strip bracket variants, full-width punctuation and harmless suffix whitespace;
- historical/current schedule aliases for course, teacher, teacher college, class composition, major composition, weeks, weekday, periods, and location.

Invalid input must return a structured issue or raise a domain-specific `NormalizationError` with field/code, not leak a raw pandas exception.

- [ ] **Step 2: Run RED**

```powershell
& $py -m unittest tests.test_schedule_normalization -v
```

- [ ] **Step 3: Implement pure normalization functions**

Required signatures:

```python
normalize_date(value, semester_monday=None) -> datetime.date
parse_weeks(value) -> frozenset[int]
normalize_weekday(value) -> int
parse_period_range(value) -> tuple[int, int]
normalize_identifier(value) -> str
normalize_name(value) -> str
normalize_college(value, aliases=None) -> str
normalize_class_name(value) -> str
resolve_columns(columns, alias_map, required) -> dict[str, str]
periods_overlap(a_start, a_end, b_start, b_end) -> bool
```

Reject impossible weekday/period/week values. Do not silently coerce missing required columns.

- [ ] **Step 4: Run GREEN and commit**

```powershell
& $py -m unittest tests.test_schedule_normalization -v
git add app/review_automation/schedules tests/test_schedule_normalization.py
git commit -m "feat: normalize schedule data"
```

### Task 5: Import and activate versioned school/class/personal schedule datasets

**Files:**

- Create: `app/review_automation/schedules/importer.py`
- Create: `app/review_automation/schedules/repository.py`
- Create: `tests/fixtures/automation/school_schedule_historical.xlsx`
- Create: `tests/fixtures/automation/school_schedule_current.xlsx`
- Create: `tests/fixtures/automation/listener_class_mapping.xlsx`
- Create: `tests/fixtures/automation/personal_schedule.xlsx`
- Create: `tests/test_schedule_importer.py`

- [ ] **Step 1: Generate synthetic fixture workbooks through test helpers**

Fixtures must include invented values and deliberately exercise:

- a 35-column historical header and a 23-column current header;
- formatting residue beyond the real data range;
- one invalid row mixed with valid rows;
- one administrative class mapping resolvable by listener number and one by student ID;
- one exact personal schedule and one duplicate slot;
- extra unrelated sheets.

Create the `.xlsx` files with a small repository script or test helper, review them, then commit only those synthetic fixtures.

- [ ] **Step 2: Write failing preview/activation tests**

Assert that:

- the importer chooses the sheet/header from recognizable required aliases rather than `max_column`;
- each invalid row becomes `ScheduleImportIssue` while other rows import;
- duplicate normalized rows collapse;
- preview creates a staged dataset and never changes the active dataset;
- activation retires the previous active dataset of the same kind/semester in one transaction;
- coverage is `basic` for a resolved class mapping and `complete` when an exact personal schedule exists;
- personal slots and class-derived slots are unioned and de-duplicated;
- filename/path traversal is impossible because only generated storage names are used.

- [ ] **Step 3: Run RED**

```powershell
& $py -m unittest tests.test_schedule_importer -v
```

- [ ] **Step 4: Implement import services and repository indexes**

Expose:

```python
preview_dataset(kind, semester, stream, filename, actor_id) -> ScheduleDataset
activate_dataset(dataset_id, actor_id) -> ScheduleDataset
get_active_dataset(kind, semester) -> ScheduleDataset | None
get_listener_schedule(listener_number, student_id, semester) -> ListenerSchedule
find_school_candidates(form, semester) -> list[SchoolScheduleMatch]
```

The repository may build request-local dictionaries keyed by normalized class, teacher/day, and listener identity, but the database remains authoritative. `activate_dataset` must write an `AutomationAuditLog`.

- [ ] **Step 5: Run GREEN and importer regression**

```powershell
& $py -m unittest tests.test_schedule_normalization tests.test_schedule_importer -v
```

- [ ] **Step 6: Commit**

```powershell
git add app/review_automation/schedules tests/fixtures/automation tests/test_schedule_importer.py
git commit -m "feat: import versioned review schedules"
```

### Task 6: Build the versioned rule registry and text rules

**Files:**

- Create: `app/review_automation/rules/__init__.py`
- Create: `app/review_automation/rules/registry.py`
- Create: `app/review_automation/rules/text.py`
- Create: `tests/test_automation_text_rules.py`

- [ ] **Step 1: Write failing registry and text-rule tests**

Test:

- newest revision per key wins;
- disabled rules do not execute;
- parameters are schema-validated per handler;
- unknown handler names fail closed with a system finding;
- feedback not starting with `该老师` produces `建议复核` evidence;
- `该老师` at position 0 passes the prefix rule and is not treated as template fraud;
- normalized feedback under 50 Chinese characters produces a review finding;
- suspicious configured `的/地/得` contexts report the exact span and suggestion without modifying source text;
- a new safe regex/phrase rule revision can be added without changing engine control flow;
- catastrophic/invalid regular expressions are rejected at save time by length/construct limits.

- [ ] **Step 2: Run RED**

```powershell
& $py -m unittest tests.test_automation_text_rules -v
```

- [ ] **Step 3: Implement registry with an explicit handler map**

Use a fixed Python map such as:

```python
HANDLERS = {
    'required_prefix': RequiredPrefixRule,
    'minimum_length': MinimumLengthRule,
    'confusion_patterns': ConfusionPatternRule,
    'safe_regex': SafeRegexRule,
    # schedule/history handlers are registered by their modules later
}
```

Administrators may select a known handler and edit JSON parameters/severity; they may not upload or evaluate Python. Persist every edit as a new `ReviewRuleRevision` and snapshot exact revision IDs into a batch.

- [ ] **Step 4: Run GREEN and commit**

```powershell
& $py -m unittest tests.test_automation_text_rules -v
git add app/review_automation/rules tests/test_automation_text_rules.py
git commit -m "feat: add extensible text review rules"
```

### Task 7: Implement schedule and confirmed business rules

**Files:**

- Create: `app/review_automation/rules/schedule.py`
- Create: `app/review_automation/rules/history.py`
- Create: `tests/test_automation_business_rules.py`

- [ ] **Step 1: Write failing schedule-rule tests**

Use synthetic forms/schedules to verify:

- exact personal week/day/period overlap -> objective high finding;
- class-derived overlap -> objective approximate review finding, never high by itself;
- no personal schedule but valid class mapping -> `basic` coverage;
- missing both -> `none` coverage but other rules still run;
- listener college equals normalized teacher college -> objective high finding;
- school course/teacher/college/time/location mismatch reports matched candidates and match confidence;
- ambiguous school candidates do not become high without another strong fact.

- [ ] **Step 2: Write failing history-rule tests**

Use latest logical versions only (`COALESCE(unique_id, id)`) and verify:

- same listener + same phone in two different teaching weeks -> review finding;
- same listener + same name and phone confirms identity;
- same name + different phone is weak evidence only;
- different name + same phone emits a separate identity-conflict anomaly;
- two different listeners sharing one witness does not trigger reuse;
- same listener using the witness in at least three distinct weeks produces a high-risk candidate, not unconditional high;
- same teacher in consecutive weeks -> review; nonconsecutive weeks -> no finding;
- same listener has two different logical forms in the same exact slot -> objective high;
- repeated exports and older versions do not inflate counts;
- highly similar feedback excludes the same logical form/version and respects the required `该老师` prefix.

- [ ] **Step 3: Run RED**

```powershell
& $py -m unittest tests.test_automation_business_rules -v
```

- [ ] **Step 4: Implement deterministic handlers**

All rule outputs must be `FindingDraft` objects. Include minimal, reviewable evidence—week, weekday, period, normalized match key, related logical form IDs—without storing unrelated users' full records in the finding.

History queries must operate within the current user's reviewable data when invoked interactively and within the batch's captured scope when invoked by a worker. Central automation batches may use all active information officers only because the route requires `审表_中心`.

- [ ] **Step 5: Run GREEN and commit**

```powershell
& $py -m unittest tests.test_automation_business_rules -v
git add app/review_automation/rules/schedule.py app/review_automation/rules/history.py tests/test_automation_business_rules.py
git commit -m "feat: add schedule and history review rules"
```

### Task 8: Implement fingerprints and four-category aggregation

**Files:**

- Create: `app/review_automation/fingerprints.py`
- Create: `app/review_automation/classification.py`
- Create: `tests/test_automation_classification.py`

- [ ] **Step 1: Write the full failing precedence matrix**

Required outcomes:

| Findings/dependencies | Result |
|---|---|
| no finding, all required checks complete | 无明显风险 |
| any review finding | 建议复核 |
| exact personal conflict | 高风险疑似假表 |
| same-college teacher | 高风险疑似假表 |
| same-listener same-slot distinct forms | 高风险疑似假表 |
| LLM high alone | 建议复核 |
| LLM high + objective review evidence | 高风险疑似假表 |
| LLM unavailable, deterministic strong high | 高风险疑似假表 plus system finding |
| LLM unavailable, no other finding | 系统无法判断 |
| schedules missing, text/history review finding exists | 建议复核 plus missing-data finding |
| critical parse failure and no stronger evidence | 系统无法判断 |

Fingerprint tests must prove reuse for identical form/dependencies and invalidation after form content, form version, schedule dataset, rule revision, prompt version, or model name changes.

- [ ] **Step 2: Run RED, implement canonical JSON SHA-256 fingerprinting and pure aggregation, then run GREEN**

The aggregator must be a pure function and must not receive a SQLAlchemy session. It returns category plus rationale keys. It must never return an instruction to pass/reject a form.

```powershell
& $py -m unittest tests.test_automation_classification -v
```

- [ ] **Step 3: Commit**

```powershell
git add app/review_automation/fingerprints.py app/review_automation/classification.py tests/test_automation_classification.py
git commit -m "feat: classify automated review evidence"
```

### Task 9: Add the validated DeepSeek V4 Flash adapter

**Files:**

- Create: `app/review_automation/llm/__init__.py`
- Create: `app/review_automation/llm/schemas.py`
- Create: `app/review_automation/llm/prompts.py`
- Create: `app/review_automation/llm/client.py`
- Create: `tests/test_deepseek_review_client.py`

- [ ] **Step 1: Write failing client tests using a fake OpenAI client**

Assert the request uses:

```python
model='deepseek-v4-flash'
response_format={'type': 'json_object'}
reasoning_effort='high'
extra_body={'thinking': {'type': 'enabled'}}
```

The prompt must explicitly say `json` and include the expected example object. Do not pass `temperature`, `top_p`, presence penalty, or frequency penalty in thinking mode.

Validate output fields:

```json
{
  "compliance": "compliant|needs_review|high_risk|unknown",
  "summary": "short Chinese summary",
  "findings": [
    {"code":"...","severity":"review|high","message":"...","evidence":"..."}
  ],
  "suggested_comment": "editable human-review draft"
}
```

Cover valid JSON, fenced JSON, empty content, truncated output, wrong enum, extra keys, timeout, 429, 401, server error, and exhausted retry classification. Assert exceptions/log messages never contain the API key or full form content.

- [ ] **Step 2: Run RED**

```powershell
& $py -m unittest tests.test_deepseek_review_client -v
```

- [ ] **Step 3: Implement schema, prompt, and client**

Use `OpenAI(api_key=..., base_url=..., timeout=...)`. Parse content with Pydantic using `extra='forbid'`. Store the validated final JSON but discard `reasoning_content`; never log it. Convert failures into typed `TransientLLMError`, `PermanentLLMError`, or `InvalidLLMResponse` with safe codes.

The prompt must state:

- `该老师` is a mandatory prefix and must not be called suspicious merely because it repeats;
- deterministic schedule/rule evidence is authoritative;
- the model supplies semantic concerns, not a final pass/reject decision;
- high-risk language without objective evidence is only a review suggestion;
- return JSON only.

- [ ] **Step 4: Run GREEN and commit**

```powershell
& $py -m unittest tests.test_deepseek_review_client -v
git add app/review_automation/llm tests/test_deepseek_review_client.py
git commit -m "feat: integrate DeepSeek review analysis"
```

### Task 10: Orchestrate one immutable assessment

**Files:**

- Create: `app/review_automation/service.py`
- Create: `tests/test_automation_service.py`

- [ ] **Step 1: Write failing service tests**

Verify this sequence: load latest logical form -> normalize -> capture active dependency versions -> fingerprint -> reuse cache or execute deterministic rules -> call DeepSeek if enabled -> aggregate -> persist assessment/findings -> return summary.

Required cases:

- identical fingerprint reuses an existing assessment and performs zero additional LLM calls;
- `force_refresh=True` creates a new fingerprint nonce/re-evaluation but preserves the old assessment;
- form/schedule/rule version change invalidates cache;
- LLM transient/permanent/invalid response adds a system finding and deterministic processing remains usable;
- no assessment path writes any human-review columns;
- full form data is present in the DeepSeek call, while worker/task arguments contain only IDs;
- returned suggestion is stored separately from `LectureForm.review_comment`.

- [ ] **Step 2: Run RED, implement `AssessmentService`, run GREEN**

```powershell
& $py -m unittest tests.test_automation_service -v
```

Commit only after directly comparing a before/after snapshot of the four protected human-review columns.

- [ ] **Step 3: Commit**

```powershell
git add app/review_automation/service.py tests/test_automation_service.py
git commit -m "feat: orchestrate immutable form assessments"
```

### Task 11: Replace background threads with durable Celery batches

**Files:**

- Create: `app/review_automation/tasks/__init__.py`
- Create: `app/review_automation/tasks/review.py`
- Create: `tests/test_automation_tasks.py`
- Modify: `app/review_automation/celery_app.py`

- [ ] **Step 1: Write failing eager-mode task tests**

Test batch creation, per-form isolation, progress counts, four category counts, cache counts, cancellation, one transient retry, permanent DeepSeek failure fallback, and final states. Inspect serialized task signatures and assert they contain only `batch_id`, `form_id`, and control booleans—never name, phone, feedback, or form JSON.

- [ ] **Step 2: Run RED**

```powershell
$env:CELERY_TASK_ALWAYS_EAGER='1'
& $py -m unittest tests.test_automation_tasks -v
Remove-Item Env:CELERY_TASK_ALWAYS_EAGER
```

- [ ] **Step 3: Implement a Celery chord with idempotent tasks**

Use one `assess_form_task(batch_id, form_id, force_refresh=False)` per form and one aggregation callback. Return only assessment ID/category/error code. Each header task checks `cancel_requested` before work, retries typed transient LLM failures with exponential backoff up to configured attempts, and persists a deterministic-only assessment when retries are exhausted. Aggregation must query assessments, not trust client-supplied counts.

If chord setup cannot be made reliable in eager and Redis modes, use a durable root task that iterates form IDs from `ReviewBatch.config_snapshot_json`; do not fall back to `threading.Thread`.

- [ ] **Step 4: Run GREEN and assert no background thread implementation remains in the new path**

```powershell
$env:CELERY_TASK_ALWAYS_EAGER='1'
& $py -m unittest tests.test_automation_tasks -v
Remove-Item Env:CELERY_TASK_ALWAYS_EAGER
rg -n "threading\.Thread|run_auto_review_task" app/review_automation
```

Expected: tests pass; `rg` returns no matches.

- [ ] **Step 5: Commit**

```powershell
git add app/review_automation/tasks app/review_automation/celery_app.py tests/test_automation_tasks.py
git commit -m "feat: run automated reviews with Celery"
```

### Task 12: Add permission-safe automation APIs and preserve old routes

**Files:**

- Create: `app/review_automation/permissions.py`
- Modify: `app/review_automation/routes.py`
- Create: `tests/test_automation_routes.py`
- Modify: `app/blueprints/admin.py`
- Modify: `tests/test_admin_auto_review_routes.py`

- [ ] **Step 1: Write failing permission and API tests**

Cover 401 unauthenticated, 403 ordinary information officer, center reviewer batch permissions, and super-admin configuration permissions.

Required endpoints:

```text
GET  /admin/api/automation/health
GET  /admin/api/automation/rules
POST /admin/api/automation/rules/<rule_key>/revisions
POST /admin/api/automation/datasets/<kind>/preview
POST /admin/api/automation/datasets/<dataset_id>/activate
GET  /admin/api/automation/datasets
GET  /admin/api/automation/forms/<form_id>/assessment
GET  /admin/api/automation/assessments/<assessment_id>
POST /admin/api/automation/batches/preview
POST /admin/api/automation/batches
GET  /admin/api/automation/batches/<batch_id>
POST /admin/api/automation/batches/<batch_id>/cancel
```

Batch tests must assert submitted IDs are intersected with `get_reviewable_users`, only latest logical form versions are processed, an external-transfer acknowledgement is required when LLM is enabled, and no HTTP path mutates the protected human-review columns.

- [ ] **Step 2: Run RED**

```powershell
& $py -m unittest tests.test_automation_routes tests.test_admin_auto_review_routes -v
```

- [ ] **Step 3: Implement routes and compatibility adapters**

Keep these endpoint names and URL rules exactly:

```text
admin.auto_review_page                /admin/auto_review
admin.auto_review_settings            /admin/api/auto_review/settings
admin.auto_review_upload              /admin/api/auto_review/upload
admin.auto_review_feedback_run        /admin/api/auto_review/feedback_run
admin.auto_review_download            /admin/auto_review/download
admin.batch_auto_check                /admin/api/review/batch_auto_check
admin.batch_auto_check_preview        /admin/api/review/batch_auto_check/preview
admin.get_auto_check_status           /admin/api/review/auto_check/status
```

Replace only the three database-batch implementations with delegation to the new service. Accept legacy `force_all` input for compatibility but ignore it and report `legacy_force_ignored: true`; never advance status. Remove `run_auto_review_task` and the `threading` import only after route tests prove parity.

Health checks must use short timeouts and return safe states. Do not echo the API key, broker password, raw exception containing secrets, or full external payload.

- [ ] **Step 4: Add assessment summaries to existing form APIs**

In `/admin/api/review/forms`, bulk-load the latest assessment for each returned latest form and add:

```json
"automation": {
  "assessment_id": "...",
  "category": "建议复核",
  "coverage": "basic",
  "finding_count": 2,
  "created_at": "..."
}
```

Avoid N+1 queries. In `/admin/api/review/form/<id>`, include the same summary plus an evidence URL; keep all existing response fields unchanged.

- [ ] **Step 5: Run GREEN and commit**

```powershell
& $py -m unittest tests.test_automation_routes tests.test_admin_auto_review_routes tests.test_review_queue_template -v
git add app/review_automation/permissions.py app/review_automation/routes.py app/blueprints/admin.py tests/test_automation_routes.py tests/test_admin_auto_review_routes.py
git commit -m "feat: expose automated review APIs"
```

### Task 13: Build the automation settings UI

**Files:**

- Modify: `app/templates/admin/_settings_automation.html`
- Modify: `app/templates/admin/system_management.html`
- Create: `app/static/js/automation-center.js`
- Modify: `app/static/css/style.css`
- Create: `tests/test_automation_templates.py`

- [ ] **Step 1: Write failing static/template contract tests**

Require accessible controls and `aria-live="polite"` feedback for:

- Redis/worker/DeepSeek service status;
- full school schedule preview and activation;
- information-officer-to-admin-class mapping upload;
- personal schedule template download/upload and coverage summary;
- dataset history;
- rule enabled/severity/parameter editor with revision display;
- explicit notice that complete form data and related evidence go to DeepSeek;
- API key shown only as configured/missing, never a value.

Assert the new JS contains no `alert(`, `confirm(`, `prompt(`, `window.open`, `_blank`, or `location.reload`.

- [ ] **Step 2: Run RED**

```powershell
& $py -m unittest tests.test_automation_templates -v
```

- [ ] **Step 3: Implement the Bootstrap settings panels**

Use the existing `activity-panel` visual language. Upload is two-stage: select/drop -> preview counts/issues -> explicit activate button. Render all server strings with `textContent`, not unsanitized `innerHTML`. Use a Bootstrap modal for activation/rule revision confirmation. Load `automation-center.js` after `settings-center.js` and initialize only when the automation tab emits `settings:shown`.

- [ ] **Step 4: Run GREEN, JavaScript syntax check, and commit**

```powershell
& $py -m unittest tests.test_automation_templates tests.test_unified_shell -v
node --check app/static/js/automation-center.js
git add app/templates/admin/_settings_automation.html app/templates/admin/system_management.html app/static/js/automation-center.js app/static/css/style.css tests/test_automation_templates.py
git commit -m "feat: manage automated review settings"
```

### Task 14: Integrate risk evidence and batch progress into the review queue

**Files:**

- Modify: `app/templates/admin/review_forms.html`
- Modify: `app/templates/admin/auto_review_results.html`
- Modify: `app/static/css/style.css`
- Modify: `tests/test_automation_templates.py`
- Modify: `tests/test_review_queue_template.py`

- [ ] **Step 1: Write failing review-queue contracts**

Require:

- four-category filter and badges;
- coverage badges `none/basic/complete` shown as `缺失/基础/完整`;
- button label `批量智能检查`, never `一键自动审核`;
- batch preview counts for processable/cache/basic/complete/missing;
- no normal/force auto-approval choices;
- explicit DeepSeek external-transfer confirmation before enqueue;
- local polling of batch status without full-page reload;
- evidence drawer sections for schedule, business rule, text, history, DeepSeek, system;
- editable suggested human comment that does not submit until the existing human-review action;
- existing `reviewQueueFeedback` aria-live region and confirmation modal remain.

Static tests must continue to reject browser dialogs/new tabs/reloads.

- [ ] **Step 2: Run RED**

```powershell
& $py -m unittest tests.test_automation_templates tests.test_review_queue_template -v
```

- [ ] **Step 3: Implement filter/badges/evidence drawer**

Add `risk` to the existing queue query string and client-side filter without changing status filtering. Populate risk cells from the new `automation` object. When viewing a form, fetch its assessment detail and render evidence into the existing preview on desktop and Bootstrap modal/offcanvas on narrow screens.

Use category colors consistently but never label a system classification as a final decision. Include the fixed sentence: `系统仅提供分类和证据，最终通过或驳回由人工审核人执行。`

- [ ] **Step 4: Replace batch decision flow**

The modal offers only `取消` and `确认开始检查`. It presents coverage/cache counts and a checked acknowledgement for DeepSeek external transfer. Enqueue via the new/compatible endpoint, poll by returned batch ID, update progress and row badges locally, and show per-form failures without reloading the page.

- [ ] **Step 5: Run GREEN, syntax/static checks, and commit**

```powershell
& $py -m unittest tests.test_automation_templates tests.test_review_queue_template tests.test_admin_auto_review_routes -v
node --check app/static/js/automation-center.js
rg -n "alert\(|confirm\(|prompt\(|window\.open|_blank|location\.reload|一键自动审核" app/templates/admin/review_forms.html app/templates/admin/auto_review_results.html app/static/js/automation-center.js
```

Expected: tests pass; `rg` returns no prohibited use or old label.

```powershell
git add app/templates/admin/review_forms.html app/templates/admin/auto_review_results.html app/static/css/style.css tests/test_automation_templates.py tests/test_review_queue_template.py
git commit -m "feat: show automated review evidence"
```

### Task 15: End-to-end compatibility, load, security, and browser verification

**Files:**

- Create: `tests/test_automation_integration.py`
- Modify: `README.md`
- Modify only if defects are found: files owned by Tasks 1–14

- [ ] **Step 1: Write a failing 200-form integration test**

Generate 200 synthetic `LectureForm` rows covering all four categories. Run Celery in eager mode with a fake DeepSeek client and assert:

- every form gets one immutable assessment or a recorded isolated failure;
- duplicate invocation reuses fingerprints;
- changing a rule revision invalidates only affected fingerprints;
- all protected `LectureForm` human-review columns are byte-for-byte unchanged;
- category and progress counts equal database truth;
- no query explosion beyond an agreed bound for list serialization;
- no output/log contains synthetic phone or feedback markers designated as secrets.

- [ ] **Step 2: Run RED, fix only demonstrated integration defects, then run the full suite**

```powershell
$env:CELERY_TASK_ALWAYS_EAGER='1'
& $py -m unittest discover -s tests -p 'test_*.py' -v
Remove-Item Env:CELERY_TASK_ALWAYS_EAGER
```

Expected: all current and new tests pass.

- [ ] **Step 3: Run static and compile gates**

```powershell
& $py -m compileall app tests celery_worker.py
node --check app/static/js/automation-center.js
git diff --check
rg -n "DEEPSEEK_API_KEY\s*=\s*[^\s]" . --glob '!*.example' --glob '!docs/**'
rg -n "threading\.Thread|run_auto_review_task" app/blueprints/admin.py app/review_automation
rg -n "LectureForm\.(status|reviewer_id|review_time|review_comment)\s*=" app/review_automation
```

Expected: compile/checks pass; secret/thread/protected-write scans return no unsafe match.

- [ ] **Step 4: Run a real Redis/Celery smoke test**

Start a disposable local Redis service/container available in the environment, start the worker with:

```powershell
& $py -m celery -A celery_worker.celery_app worker --loglevel=INFO --pool=solo
```

From a separate terminal, initialize a temporary database, enqueue a 3-form synthetic batch, restart the worker while one task is pending, and verify the batch resumes/idempotently completes. Do not use production data or production Redis.

- [ ] **Step 5: Run real browser QA against temporary data**

Use the established SWU TIC temporary database/storage pattern. Verify by clicks, not DOM inspection alone:

1. Super-admin opens System Settings -> Automation.
2. Upload/preview/activate the synthetic full school schedule.
3. Upload class mapping and see `基础` coverage.
4. Upload personal schedule and see coverage change to `完整`.
5. Disable/re-enable a rule and see its revision change.
6. Open the review queue, select forms, inspect preview counts, acknowledge external DeepSeek transfer, and start a batch.
7. Observe progress without page reload.
8. Filter all four categories and open the evidence drawer.
9. Edit the suggested review comment, then use the existing human action to pass or reject one form.
10. Confirm automation never performed the final decision itself.

Capture screenshots for desktop and narrow viewport. Record any untested case honestly.

- [ ] **Step 6: Document operations and rollback**

In `README.md`, document environment variables, schema initialization, Redis startup, worker startup, supported uploads, DeepSeek external-data warning, batch retry/cancel behavior, no-auto-decision guarantee, and rollback procedure (stop worker, disable automation UI/route registration if needed; existing form/review tables remain untouched).

- [ ] **Step 7: Final review gates**

Compare every requirement in the approved design against code/tests. Run:

```powershell
rg -n "TODO|TBD|FIXME|pass\s*(#.*)?$|NotImplementedError" app/review_automation tests/test_automation_*.py tests/test_schedule_*.py tests/test_deepseek_review_client.py
git status --short
git log --oneline --max-count=20
```

Expected: no placeholder implementation; only intended changes; package commits are reviewable.

- [ ] **Step 8: Commit final QA/docs**

```powershell
git add tests/test_automation_integration.py README.md
git commit -m "test: verify automated review workflow"
```

---

## Main-task acceptance checklist

The main task must independently confirm all of the following before declaring completion:

- [ ] Design rule matrix is fully represented by tests.
- [ ] Every implementation package has genuine RED and GREEN evidence.
- [ ] No real `demo/` workbook or derived PII fixture is tracked by Git.
- [ ] `LectureForm.status`, reviewer, time, and comment change only through existing human-review routes.
- [ ] Existing route contract tests and the full previous test suite pass.
- [ ] DeepSeek uses official `https://api.deepseek.com`, `deepseek-v4-flash`, JSON output, thinking enabled, and a configurable reasoning effort.
- [ ] Redis/Celery failures and DeepSeek failures are visible and do not create false `无明显风险` results.
- [ ] Batch payloads contain IDs only and assessment fingerprints are idempotent.
- [ ] UI has no browser dialogs, forced reloads, or new-tab flows in the high-frequency review path.
- [ ] A real browser click-through and a real Redis/Celery smoke test have been completed with temporary synthetic data.
