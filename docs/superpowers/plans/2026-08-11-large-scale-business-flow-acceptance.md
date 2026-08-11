# SWU TIC Large-Scale Business Flow Acceptance Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Execute a reproducible 1002-information-officer, 1500-form business-flow acceptance run that exercises rules-only, real-DeepSeek-only, combined automation, four administrator scopes, rejection/resubmission, final review, statistics, export, and screenshot evidence without performing attack testing.

**Architecture:** Extend the existing immutable assessment pipeline with an explicit three-value review mode and concurrency-safe per-form batch items, then build an ignored-runtime acceptance harness under `tools/business_acceptance/`. Generate and run all data against an isolated SQLite database and storage root; use existing Flask routes for bulk business actions and a real browser for representative human flows. Real DeepSeek calls are gated by a persistent request budget and the existing human-review fields are snapshotted before and after every automation batch.

**Tech Stack:** Python 3.10, Flask 2.3, Flask-SQLAlchemy 3, SQLite, Celery 5, Redis, OpenAI Python SDK against DeepSeek, Pydantic 2, openpyxl, PowerShell 5.1, Bootstrap/Jinja, vanilla JavaScript, `unittest`, and the Codex in-app browser.

**Approved design:** `docs/superpowers/specs/2026-08-11-large-scale-business-flow-acceptance-design.md`

---

## Execution ownership and hard gates

This plan is executed in one visible Codex task using model `gpt-5.6-luna` with reasoning effort `max`. The worker uses `superpowers:executing-plans`; the main task monitors checkpoints, reviews commits and evidence, and independently reruns the final verifiers.

Hard gates:

1. Never print, echo, screenshot, store, or commit `DEEPSEEK_API_KEY`. Only check whether it is non-empty.
2. Never write to the default business database. All commands must set an explicit acceptance `SQLITE_DB_PATH` below `data/instance/acceptance-2026-08-11/`.
3. Never copy original `demo` workbooks into the repository. Only generated, de-identified runtime artifacts may be written below ignored `data/storage/acceptance-2026-08-11/`.
4. Do not begin real DeepSeek calls until focused and full fake-client tests pass, the 60-form staged manifest closes exactly, and the HTTP ceiling is 1200.
5. Do not begin staged concurrency without a reachable Redis broker and result backend. Do not install WSL, Redis, Memurai, Docker, or another system service without user authorization. If Redis is absent, finish code/data preparation and report the gate to the main task.
6. Use `DEEPSEEK_MAX_RETRIES=0` for the acceptance run. The harness performs explicit, budgeted retries after the initial 1000 logical evaluations, so an individual SDK call cannot silently multiply HTTP attempts.
7. Automation must never change `LectureForm.status`, `reviewer_id`, `review_time`, or `review_comment`. Any mismatch is a stop condition.
8. Only the existing human-review and information-officer submission routes may change human status or create form versions.
9. Reuse one browser tab. Start Flask through the acceptance runtime with no browser-opening action; do not repeatedly open login pages or new tabs.
10. Do not fix unrelated business defects during the acceptance run. Record a defect with evidence and continue only if the defect does not invalidate later results.

## File map

Modify production files:

- `app/review_automation/contracts.py` — add `ReviewMode`.
- `app/review_automation/fingerprints.py` — include mode in the immutable fingerprint.
- `app/review_automation/classification.py` — allow rules-only results without a false `llm_unavailable` downgrade.
- `app/review_automation/service.py` — run rules and DeepSeek independently according to the selected mode; expose HTTP attempts.
- `app/review_automation/llm/client.py` — count provider attempts without logging payloads or credentials.
- `app/review_automation/models.py` — add concurrency-safe `ReviewBatchItem` rows.
- `app/review_automation/tasks/review.py` — persist batch items, configurable task retry count, group/chord execution, aggregation and cancellation.
- `app/review_automation/routes.py` — accept and return `review_mode`, preserve the old `llm_enabled` contract, and calculate live progress from batch items.
- `app/static/js/automation-center.js` — send/display explicit review modes and live item progress.
- `app/templates/admin/_settings_automation.html` — expose three batch modes with the existing visual language.
- `.env.example` — document acceptance-safe retry/concurrency controls without a key.

Create acceptance tooling:

- `tools/business_acceptance/__init__.py` — package marker.
- `tools/business_acceptance/config.py` — frozen counts, paths, seed, secret-safe validation and JSON state helpers.
- `tools/business_acceptance/corpus.py` — read only evaluation text from the external `demo` directory and de-identify text.
- `tools/business_acceptance/generator.py` — generate departments, groups, accounts, schedules, 1500 logical forms and anomaly oracle.
- `tools/business_acceptance/seed.py` — populate the isolated database using application models.
- `tools/business_acceptance/batches.py` — create/poll R, D and RD batches, run staged concurrency, enforce the HTTP budget, and verify cache reuse.
- `tools/business_acceptance/human_flow.py` — log in through existing routes and perform bulk group/department/center/super-admin business actions plus officer resubmission.
- `tools/business_acceptance/verify.py` — count closure, scope, protected-field, version-history, pagination and export checks.
- `tools/business_acceptance/report.py` — sanitized CSV/JSON/Markdown report writer.
- `tools/business_acceptance/cli.py` — `prepare`, `seed`, `run-batches`, `run-human-flow`, `verify`, and `report` commands.
- `tools/business_acceptance/runtime.ps1` — start/stop only recorded Celery worker PIDs and never open a browser.

Create tests:

- `tests/test_automation_review_modes.py`
- `tests/test_automation_concurrent_batches.py`
- `tests/test_business_acceptance_config.py`
- `tests/test_business_acceptance_generator.py`
- `tests/test_business_acceptance_batches.py`
- `tests/test_business_acceptance_human_flow.py`
- `tests/test_business_acceptance_verify.py`
- `tests/test_business_acceptance_runtime.py`

Runtime artifacts, all ignored by Git:

```text
data/instance/acceptance-2026-08-11/
  swu-tic-acceptance.db
  redis/                         only when a user-approved Redis runtime is provided
data/storage/acceptance-2026-08-11/
  manifest.json
  oracle.jsonl
  run-state.json
  generated/class-mapping.xlsx
  generated/personal-schedule.xlsx
  results/forms.csv
  results/batches.json
  results/deepseek-summary.json
  results/verification.json
  screenshots/*.png
  logs/flask.log
  logs/celery-*.log
  report/business-flow-acceptance.md
```

## Fixed acceptance matrix

```text
Information officers: 1002 across four departments (251, 251, 250, 250)
Logical forms:         1500 = 504 users x 1 + 498 users x 2
R batch:                500 rules_only
D batch:                500 llm_only
RD batch:               500 combined
Logical LLM reviews:   1000
Normal controls:        225 = 75 in each batch
Violation-intended:    1275 = 425 in each batch
HTTP attempt ceiling:  1200
Browser officers:      at least 12 = 3 per department
Group admins:             4 = 1 per department
Department admins:        4 = 1 per department
Center admins:            1
Super admins:             1
```

The provided school schedule is:

```text
D:\Users\yoouzico\Desktop\worksoace_codex\students_work\bumen\swu_tic_website\2025-2026-2全校课表260413.xlsx
```

The historical corpus directory is:

```text
D:\Users\yoouzico\Desktop\worksoace_codex\students_work\bumen\swu_tic_website\demo
```

## Spec coverage matrix

| 已确认业务要求 | 实施任务 |
| --- | --- |
| 不进行攻防测试，只排查业务流程缺陷 | 全程硬门；Tasks 9–12 |
| 1002 名信息员、4 个部门、1500 张表 | Tasks 4–5 |
| 上传并启用全校课表、班级映射、个人课表 | Task 5 |
| 信息员保存草稿、提交、查看、一人多张和跨周 | Tasks 5、7、11 |
| 500 规则、500 DeepSeek、500 联合审核 | Tasks 1–6、10 |
| DeepSeek 4 → 8 → 16 阶梯并发和 1200 次上限 | Tasks 2、6、10 |
| 小组管理员、部门管理员、中心管理员、超级管理员 | Tasks 5、7、11 |
| 小组和部门进入“部门已审核”，中心和超管进入“中心已审核” | Tasks 7、11 |
| 驳回、查看原因、修改并重新提交 | Tasks 7、11 |
| 旧版本和旧审核历史不得覆盖 | Tasks 7–8、12 |
| 批次、缓存、分类、证据、失败和人工字段隔离 | Tasks 1–3、6、8、10 |
| 页面、筛选、分页、统计、数据库和导出一致 | Tasks 8、11–12 |
| 关键截图和最终验收报告 | Tasks 11–12 |

---

### Task 1: Add explicit rules-only, DeepSeek-only, and combined review modes

**Files:**

- Modify: `app/review_automation/contracts.py`
- Modify: `app/review_automation/fingerprints.py`
- Modify: `app/review_automation/classification.py`
- Modify: `app/review_automation/service.py`
- Modify: `app/review_automation/tasks/review.py`
- Modify: `app/review_automation/routes.py`
- Test: `tests/test_automation_review_modes.py`
- Test: `tests/test_automation_service.py`
- Test: `tests/test_automation_routes.py`

- [ ] **Step 1: Write failing mode-isolation tests**

Create `tests/test_automation_review_modes.py` with a temporary automation database, one complete-coverage form, a counting deterministic runner and a counting fake LLM. Assert these exact contracts:

```python
def test_rules_only_never_calls_llm_and_can_return_clear():
    summary = service.assess_form(form.id, review_mode='rules_only')
    assert rule_calls == [form.id]
    assert llm.calls == []
    assert summary.category == '无明显风险'
    assert summary.error_code is None

def test_llm_only_never_runs_rules():
    summary = service.assess_form(form.id, review_mode='llm_only')
    assert rule_calls == []
    assert len(llm.calls) == 1
    sources = {row.source for row in assessment(summary).findings}
    assert sources <= {'llm', 'system'}

def test_combined_keeps_rule_and_llm_findings():
    summary = service.assess_form(form.id, review_mode='combined')
    assert rule_calls == [form.id]
    assert len(llm.calls) == 1
    assert {'rule', 'llm'} <= {row.source for row in assessment(summary).findings}

def test_modes_have_distinct_fingerprints_and_cache_within_mode():
    first = service.assess_form(form.id, review_mode='rules_only')
    second = service.assess_form(form.id, review_mode='rules_only')
    third = service.assess_form(form.id, review_mode='combined')
    assert second.cache_hit is True
    assert first.fingerprint == second.fingerprint
    assert third.fingerprint != first.fingerprint

def test_every_mode_preserves_human_fields():
    before = protected_snapshot(form)
    for mode in ('rules_only', 'llm_only', 'combined'):
        service.assess_form(form.id, review_mode=mode, force_refresh=True)
    assert protected_snapshot(form) == before
```

- [ ] **Step 2: Run RED**

```powershell
$py = 'D:\Users\yoouzico\Desktop\worksoace_codex\students_work\bumen\swu_tic_website\SWU_TIC-main\.venv\Scripts\python.exe'
& $py -m unittest tests.test_automation_review_modes -v
```

Expected: failures because `review_mode` and `ReviewMode` do not exist and the current service always runs deterministic rules.

- [ ] **Step 3: Add the typed mode and fingerprint input**

Add to `contracts.py`:

```python
class ReviewMode(str, Enum):
    RULES_ONLY = 'rules_only'
    LLM_ONLY = 'llm_only'
    COMBINED = 'combined'
```

Export `ReviewMode`. Add `review_mode: str | ReviewMode = ReviewMode.COMBINED` to `build_assessment_fingerprint()` and include `review_mode.value` in the canonical payload. Existing callers default to `combined`.

- [ ] **Step 4: Isolate service execution by mode**

Add `review_mode` to `AssessmentService.assess_form()`. Normalize with `ReviewMode(review_mode)`. Use these exact gates:

```python
run_rules = mode in {ReviewMode.RULES_ONLY, ReviewMode.COMBINED}
run_llm = mode in {ReviewMode.LLM_ONLY, ReviewMode.COMBINED}
deterministic = self._run_deterministic(form, context) if run_rules else ()
enabled = run_llm and (self.llm_enabled if llm_enabled is None else bool(llm_enabled))
```

Add `llm_required: bool = True` to `aggregate_classification()`. Only create `llm_unavailable`, add its rationale, or downgrade clear to unknown when `llm_required` is true. For `rules_only`, pass `llm_required=False`, do not set an LLM error, model ID, prompt version or system finding. For `llm_only`, pass no deterministic findings while retaining the normalized form and schedule context sent to the model.

- [ ] **Step 5: Preserve legacy route behavior while accepting `review_mode`**

Add a strict parser:

```python
def _review_mode(data):
    raw = data.get('review_mode')
    if raw is None:
        return ReviewMode.COMBINED if bool(data.get('llm_enabled', False)) else ReviewMode.RULES_ONLY
    try:
        return ReviewMode(str(raw))
    except ValueError:
        return None
```

New APIs return `review_mode`. Old payloads still map `llm_enabled=False` to `rules_only` and `llm_enabled=True` plus explicit transfer acknowledgement to `combined`. `llm_only` and `combined` both require the existing external-transfer acknowledgement.

- [ ] **Step 6: Run GREEN and regression tests**

```powershell
& $py -m unittest tests.test_automation_review_modes tests.test_automation_service tests.test_automation_routes tests.test_automation_classification -v
```

Expected: all pass; fake LLM only; no human field changes.

- [ ] **Step 7: Commit**

```powershell
git add app/review_automation/contracts.py app/review_automation/fingerprints.py app/review_automation/classification.py app/review_automation/service.py app/review_automation/tasks/review.py app/review_automation/routes.py tests/test_automation_review_modes.py tests/test_automation_service.py tests/test_automation_routes.py
git commit -m "feat: isolate automated review modes"
```

### Task 2: Make batch progress concurrency-safe and measurable

**Files:**

- Modify: `app/review_automation/models.py`
- Modify: `app/review_automation/llm/client.py`
- Modify: `app/review_automation/service.py`
- Modify: `app/review_automation/tasks/review.py`
- Modify: `app/review_automation/routes.py`
- Modify: `app/review_automation/cli.py`
- Modify: `.env.example`
- Test: `tests/test_automation_concurrent_batches.py`
- Test: `tests/test_automation_tasks.py`
- Test: `tests/test_deepseek_review_client.py`

- [ ] **Step 1: Write failing per-form item, attempt-count, and parallel aggregation tests**

Cover:

```python
def test_batch_creates_one_unique_item_per_latest_form():
    batch = create_review_batch(form_ids, enqueue=False, review_mode='combined')
    items = ReviewBatchItem.query.filter_by(batch_id=batch.id).all()
    assert len(items) == len(set(form_ids))
    assert {item.status for item in items} == {'queued'}

def test_progress_is_derived_from_items_without_lost_updates():
    run_items_concurrently(batch.id, form_ids, workers=8)
    payload = batch_payload(batch.id)
    assert payload['processed_count'] == len(form_ids)
    assert payload['target_form_count'] == len(form_ids)
    assert payload['processed_count'] == sum(payload[key] for key in (
        'clear_count', 'review_count', 'high_risk_count', 'unknown_count'
    ))

def test_acceptance_retry_zero_calls_provider_once():
    client = counting_deepseek_client(max_retries=0, response=TimeoutError())
    with self.assertRaises(TransientLLMError):
        client.review(SYNTHETIC_FORM)
    self.assertEqual(client.last_attempt_count, 1)

def test_batch_item_records_cache_and_http_attempts():
    item = completed_item(batch.id, form.id)
    assert item.http_attempts in {0, 1}
    assert item.cache_hit is (item.http_attempts == 0)
```

- [ ] **Step 2: Run RED**

```powershell
& $py -m unittest tests.test_automation_concurrent_batches tests.test_deepseek_review_client -v
```

Expected: missing `ReviewBatchItem` and attempt-count failures.

- [ ] **Step 3: Add `ReviewBatchItem`**

Add a new automation-only model without modifying `LectureForm`:

```python
class ReviewBatchItem(db.Model):
    __tablename__ = 'review_batch_items'
    __table_args__ = (
        db.UniqueConstraint('batch_id', 'form_id', name='uq_review_batch_item_form'),
        db.Index('ix_review_batch_item_status', 'batch_id', 'status'),
    )
    id = db.Column(db.Integer, primary_key=True)
    batch_id = db.Column(db.String(36), db.ForeignKey('review_batches.id'), nullable=False)
    form_id = db.Column(db.Integer, db.ForeignKey('lecture_forms.id'), nullable=False)
    ordinal = db.Column(db.Integer, nullable=False)
    status = db.Column(db.String(16), nullable=False, default='queued')
    assessment_id = db.Column(db.String(36), db.ForeignKey('review_assessments.id'), nullable=True)
    category = db.Column(db.String(32), nullable=True)
    error_code = db.Column(db.String(64), nullable=True)
    cache_hit = db.Column(db.Boolean, nullable=False, default=False)
    http_attempts = db.Column(db.Integer, nullable=False, default=0)
    started_at = db.Column(db.DateTime, nullable=True)
    finished_at = db.Column(db.DateTime, nullable=True)
    duration_ms = db.Column(db.Integer, nullable=True)
```

The idempotent `auto-review init-schema` command must create this table on an acceptance database and leave existing tables/data untouched.

- [ ] **Step 4: Count provider attempts without logging sensitive data**

`DeepSeekReviewClient.review()` sets `last_attempt_count = 0` before the loop and increments it immediately before each `chat.completions.create()` call. `AssessmentSummary` gains `http_attempts: int = 0`; cached, disabled and rules-only assessments report zero. Do not include request or response text in this metric.

`_assess_one()` accumulates HTTP attempts from every primary summary before any deterministic fallback. `_fallback()` receives that accumulated value and copies it into the `ReviewBatchItem` result, so a failed LLM attempt followed by a rules-only fallback still reports one external attempt rather than zero.

- [ ] **Step 5: Replace shared snapshot writes with per-item writes**

At batch creation, insert all `ReviewBatchItem` rows. Each form task updates only its own row from `queued` to `running` to `completed` or `failed`. `_batch_payload()` and `_aggregate_batch()` query item rows for processed/category/failure/cache/HTTP totals. The JSON snapshot remains a final compatibility summary but is not the live concurrency source.

Add `transient_retries` to the persisted batch config, defaulting to the existing `TASK_TRANSIENT_RETRIES`. The acceptance harness passes zero. Reject negative values and values over three.

Document the application concurrency setting in `.env.example` without adding a credential:

```dotenv
DEEPSEEK_MAX_CONCURRENCY=4
```

- [ ] **Step 6: Dispatch a Celery group/chord outside eager mode**

Keep eager tests deterministic. With `task_always_eager=True`, iterate per-form tasks and aggregate synchronously. Otherwise dispatch:

```python
header = group(
    assess_form_task.s(batch.id, item.form_id, force_refresh)
    for item in ordered_items
)
result = chord(header)(finalize_review_batch_task.s(batch.id))
batch.celery_root_id = result.id
db.session.commit()
```

`finalize_review_batch_task(results, batch_id)` ignores untrusted result bodies and recomputes totals from `ReviewBatchItem`. Cancellation marks not-started items `cancelled`; completed items remain intact.

- [ ] **Step 7: Run GREEN, concurrency regression and schema checks**

```powershell
& $py -m unittest tests.test_automation_concurrent_batches tests.test_automation_tasks tests.test_deepseek_review_client tests.test_automation_models tests.test_automation_routes -v
& $py -m compileall -q app tools tests
```

Expected: all pass; 8-worker synthetic run has no missing or duplicate items.

- [ ] **Step 8: Commit**

```powershell
git add app/review_automation/models.py app/review_automation/llm/client.py app/review_automation/service.py app/review_automation/tasks/review.py app/review_automation/routes.py app/review_automation/cli.py .env.example tests/test_automation_concurrent_batches.py tests/test_automation_tasks.py tests/test_deepseek_review_client.py
git commit -m "feat: persist concurrent review batch progress"
```

### Task 3: Expose the three review modes and concurrency-safe progress in the existing UI

**Files:**

- Modify: `app/templates/admin/_settings_automation.html`
- Modify: `app/static/js/automation-center.js`
- Test: `tests/test_automation_templates.py`
- Test: `tests/test_automation_routes.py`

- [ ] **Step 1: Write failing template and JavaScript contract tests**

Add assertions that the settings partial contains one named control with these exact values and labels:

```html
<input name="review_mode" value="rules_only">仅运行规则
<input name="review_mode" value="llm_only">仅运行 DeepSeek
<input name="review_mode" value="combined">规则与 DeepSeek 联合运行
```

The DOM may use radios or a select, but there must be exactly one selected value. Add static JavaScript assertions that the request includes `review_mode`, that `external_transfer_acknowledged` is true only after the existing confirmation for `llm_only` or `combined`, and that progress renders these fields:

```text
processed_count / target_form_count
clear_count
review_count
high_risk_count
unknown_count
failed_count
cache_count
http_attempts
```

Assert the changed source does not contain `alert(`, `confirm(`, `prompt(`, `window.open(` or an internal `_blank`.

- [ ] **Step 2: Run RED**

```powershell
& $py -m unittest tests.test_automation_templates tests.test_automation_routes -v
```

Expected: missing review-mode controls and/or payload/progress fields.

- [ ] **Step 3: Implement the existing-panel controls and payload**

Use the current Bootstrap checked-control style. Default to `rules_only`; selecting `llm_only` or `combined` reveals the existing external-transfer acknowledgement. Build requests with:

```javascript
const reviewMode = form.querySelector('[name="review_mode"]:checked')?.value
  || form.querySelector('[name="review_mode"]')?.value
  || 'rules_only';
const usesDeepSeek = reviewMode === 'llm_only' || reviewMode === 'combined';
const payload = {
  form_ids: selectedFormIds,
  review_mode: reviewMode,
  llm_enabled: usesDeepSeek,
  external_transfer_acknowledged: usesDeepSeek && transferCheckbox.checked,
};
```

Do not allow an LLM mode request unless the checkbox is checked. Keep `llm_enabled` for compatibility while making `review_mode` authoritative.

- [ ] **Step 4: Render live progress by updating the existing DOM**

Poll the batch status endpoint through the existing timer and update `aria-live` text plus the existing progress bar. Display category, failure, cache and HTTP-attempt counts. Stop polling only at `completed`, `completed_with_errors`, `failed` or `cancelled`. Do not reload the page or open a new tab.

- [ ] **Step 5: Run GREEN and commit**

```powershell
& $py -m unittest tests.test_automation_templates tests.test_automation_routes -v
node --check app/static/js/automation-center.js
git add app/templates/admin/_settings_automation.html app/static/js/automation-center.js tests/test_automation_templates.py tests/test_automation_routes.py
git commit -m "feat: expose automated review execution modes"
```

### Task 4: Build the deterministic, de-identified acceptance data generator

**Files:**

- Create: `tools/business_acceptance/__init__.py`
- Create: `tools/business_acceptance/config.py`
- Create: `tools/business_acceptance/corpus.py`
- Create: `tools/business_acceptance/generator.py`
- Create: `tools/business_acceptance/cli.py`
- Test: `tests/test_business_acceptance_config.py`
- Test: `tests/test_business_acceptance_generator.py`

- [ ] **Step 1: Write failing count, privacy and reproducibility tests**

The tests must use a temporary directory and synthetic in-memory corpus. Assert:

```python
manifest = generate_manifest(config, corpus=['该老师讲解具体，课堂组织清晰。'])
assert manifest.officer_count == 1002
assert manifest.logical_form_count == 1500
assert manifest.department_counts == [251, 251, 250, 250]
assert manifest.batch_counts == {'rules_only': 500, 'llm_only': 500, 'combined': 500}
assert manifest.llm_logical_count == 1000
assert manifest.normal_control_count == 225
assert manifest.violation_intended_count == 1275
assert len({form.synthetic_key for form in manifest.forms}) == 1500
assert sum(user.form_count for user in manifest.officers) == 1500
assert generate_manifest(config, corpus).sha256 == generate_manifest(config, corpus).sha256
assert_real_identity_patterns_absent(manifest.to_json())
```

Also assert 504 officers have one form and 498 have two, and all two-form users have different teaching weeks.

- [ ] **Step 2: Run RED**

```powershell
& $py -m unittest tests.test_business_acceptance_config tests.test_business_acceptance_generator -v
```

Expected: import failures for the missing tooling.

- [ ] **Step 3: Implement frozen configuration and stop conditions**

Use a frozen dataclass with these exact defaults:

```python
@dataclass(frozen=True)
class AcceptanceConfig:
    seed: int = 260811
    officer_count: int = 1002
    logical_form_count: int = 1500
    per_mode: int = 500
    normal_per_mode: int = 75
    http_attempt_ceiling: int = 1200
    staged_concurrency: tuple[int, ...] = (4, 8, 16)
    staged_size_each: int = 20
    semester: str = '2025-2026-2'
    semester_monday: str = '2026-03-02'
```

`validate()` raises before mutation unless all arithmetic contracts match. Paths must resolve under the specified acceptance runtime root except the read-only school schedule and demo inputs.

- [ ] **Step 4: Extract only evaluation prose and de-identify it**

Open workbooks with `openpyxl.load_workbook(path, read_only=True, data_only=True)`. Inspect the first ten rows for these normalized header aliases:

```python
FEEDBACK_HEADERS = {
    '课程反馈', '课程反馈优点', '评价内容', '听课评价', '课堂教学评价',
    '不足及建议', '建议', '优质案例推荐', '异常情况反映',
}
```

Collect non-empty strings of 8–600 Chinese characters only from matched columns. Do not collect listener, teacher, witness, phone, student ID, information-officer number, signature, QQ, dormitory or free-form identity columns. Apply explicit replacement patterns for 11-digit phones, long numeric identifiers and names found in identity headers. Return corpus statistics and SHA-256, not source rows, in tracked logs.

- [ ] **Step 5: Generate the organization, accounts, schedules and form oracle**

Use invented identifiers only:

```text
Departments: 验收部门一, 验收部门二, 验收部门三, 验收部门四
Groups:      each department has 验收A组 and 验收B组
Officer IDs: YA0001 ... YA1002
Student IDs: 260000000001 ...
Names:       验收信息员0001 ...
Teachers:    验收教师001 ...
Witnesses:   验收见证001 ...
Password:    generated once for the isolated run and written only to ignored manifest.json
```

Assign 75 normal controls per mode. Distribute the other 425 per mode across the approved anomaly types with overlaps so each violation-intended form has at least one oracle marker. Preserve real prose style by sampling the de-identified corpus, but synthesize all relational facts.

Generate `class-mapping.xlsx` and `personal-schedule.xlsx` with openpyxl. Create complete, basic and missing coverage cohorts. Use the provided school schedule unchanged as the upload source.

- [ ] **Step 6: Add a `prepare` command and run GREEN**

```powershell
& $py -m tools.business_acceptance.cli prepare `
  --runtime-root "$repo\data\storage\acceptance-2026-08-11" `
  --demo-dir "D:\Users\yoouzico\Desktop\worksoace_codex\students_work\bumen\swu_tic_website\demo" `
  --school-schedule "D:\Users\yoouzico\Desktop\worksoace_codex\students_work\bumen\swu_tic_website\2025-2026-2全校课表260413.xlsx"
& $py -m unittest tests.test_business_acceptance_config tests.test_business_acceptance_generator -v
```

Expected summary, with no raw evaluation text printed:

```text
PREPARE=PASS officers=1002 forms=1500 rules_only=500 llm_only=500 combined=500 normal=225 violation_intended=1275
```

- [ ] **Step 7: Commit**

```powershell
git add tools/business_acceptance tests/test_business_acceptance_config.py tests/test_business_acceptance_generator.py
git commit -m "test: generate large business acceptance dataset"
```

### Task 5: Seed the isolated database and activate schedule datasets

**Files:**

- Create: `tools/business_acceptance/seed.py`
- Modify: `tools/business_acceptance/cli.py`
- Test: `tests/test_business_acceptance_generator.py`
- Test: `tests/test_schedule_importer.py`

- [ ] **Step 1: Write a failing isolated seed integration test**

Assert on a temporary SQLite database:

```python
result = seed_acceptance_database(app, manifest)
assert result.information_officers == 1002
assert result.administrators == 10
assert result.departments == 4
assert result.groups == 8
assert result.logical_forms == 1500
assert result.physical_form_rows == 1500
assert latest_status_counts() == {'待审核': 1500}
assert get_user_review_permission(group_admin_id) == '审表_小组'
assert get_user_review_permission(department_admin_id) == '审表_部门'
assert get_user_review_permission(center_admin_id) == '审表_中心'
assert get_user_review_permission(super_admin_id) == '审表_中心'
```

For every group and department administrator, compare `get_reviewable_users()` to the manifest scope exactly.

- [ ] **Step 2: Run RED**

```powershell
& $py -m unittest tests.test_business_acceptance_generator.BusinessAcceptanceSeedTest -v
```

Expected: missing `seed_acceptance_database`.

- [ ] **Step 3: Implement idempotent isolated seeding**

Require `ACCEPTANCE_RUN=1` and reject any database path outside `data/instance/acceptance-2026-08-11`. Create only the isolated schema. Insert permissions `审表_小组`, `审表_部门`, `审表_中心`; assign per-user special roles through `RolePermission.role == f'特殊角色_{user.id}'` so the four levels coexist.

For this isolated SQLite concurrency run only, execute `PRAGMA journal_mode=WAL` and `PRAGMA busy_timeout=30000` after connecting. Assert the effective journal mode is `wal`; do not change the default business database configuration.

Hash passwords with Werkzeug. Insert forms in chunks of 100, flush, assign `unique_id = id`, and commit each chunk. Write the database IDs back to ignored `manifest.json`; never derive expected results from database-generated automation fields.

- [ ] **Step 4: Import and activate schedule data through existing repository services**

Use the same importer and activation functions called by `/admin/api/automation/datasets/...`. Import in this order:

1. provided school schedule;
2. generated class mapping;
3. generated personal schedule.

Assert one active dataset of each kind, preserve SHA-256 and import issues, and record coverage counts. The seed command must fail if `complete + basic + missing != 1002`.

- [ ] **Step 5: Run the real isolated seed and close counts**

```powershell
$env:ACCEPTANCE_RUN='1'
$env:INSTANCE_DIR="$repo\data\instance\acceptance-2026-08-11"
$env:SQLITE_DB_PATH="$env:INSTANCE_DIR\swu-tic-acceptance.db"
$env:DATABASE_URL=''
$env:AUTOMATION_UPLOAD_DIR="$repo\data\storage\acceptance-2026-08-11\uploads"
& $py -m tools.business_acceptance.cli seed --manifest "$repo\data\storage\acceptance-2026-08-11\manifest.json"
```

Expected:

```text
SEED=PASS officers=1002 admins=10 departments=4 groups=8 logical_forms=1500 physical_rows=1500 active_datasets=3
```

- [ ] **Step 6: Run GREEN and commit**

```powershell
& $py -m unittest tests.test_business_acceptance_generator tests.test_schedule_importer -v
git add tools/business_acceptance/seed.py tools/business_acceptance/cli.py tests/test_business_acceptance_generator.py
git commit -m "test: seed isolated business acceptance database"
```

### Task 6: Add batch orchestration, request budget, and cache verification

**Files:**

- Create: `tools/business_acceptance/batches.py`
- Modify: `tools/business_acceptance/cli.py`
- Create: `tests/test_business_acceptance_batches.py`

- [ ] **Step 1: Write failing budget and staged-run tests with fake clients**

Use fake batch adapters; no network. Assert:

```python
state = RunState(http_attempt_ceiling=1200)
state.reserve_http_attempts(1000)
state.reserve_http_attempts(200)
with self.assertRaises(RequestBudgetExceeded):
    state.reserve_http_attempts(1)

stages = build_stages(llm_form_ids, levels=(4, 8, 16), size_each=20)
self.assertEqual([(s.concurrency, len(s.form_ids)) for s in stages], [(4, 20), (8, 20), (16, 20)])
self.assertEqual(len(set(chain.from_iterable(s.form_ids for s in stages))), 60)

summary = close_batch(fake_completed_items(500))
self.assertEqual(summary.target, 500)
self.assertEqual(summary.processed, 500)
self.assertEqual(summary.processed, summary.classified)
```

Test that a 50-form unchanged rerun has 50 cache hits and zero HTTP attempts; a changed form version must have a new assessment.

- [ ] **Step 2: Run RED**

```powershell
& $py -m unittest tests.test_business_acceptance_batches -v
```

Expected: missing batch harness.

- [ ] **Step 3: Implement persistent run state and exact batch closure**

`run-state.json` is rewritten atomically using a temporary file in the same ignored directory. Persist:

```json
{
  "phase": "prepared|staged|main|cache|human|verified|reported",
  "batch_ids": {},
  "logical_llm_reviews": 0,
  "http_attempts": 0,
  "http_attempt_ceiling": 1200,
  "safe_concurrency": null,
  "last_completed_form_id": null
}
```

The CLI refuses to reserve more attempts than remain. With `DEEPSEEK_MAX_RETRIES=0` and task `transient_retries=0`, each uncached LLM item may record only 0 or 1 attempt. Any item over one is a stop condition.

- [ ] **Step 4: Implement three-mode and staged orchestration**

Create R as `rules_only`, D as `llm_only`, and RD as `combined`. First run 60 D/RD forms in three 20-form stages. Poll `/admin/api/automation/batches/<id>` or the model directly every two seconds and append sanitized timings; never poll by refreshing browser pages.

Choose safe concurrency as the highest stage where:

```text
rate_limited_ratio <= 0.05
failed_ratio <= 0.05
processed_count == target_form_count
progress_updated_during_run == true
```

If no stage passes, use 4 and report the observed failures. Complete the remaining D/RD forms at the selected concurrency. Run R without an API key and assert total HTTP attempts zero.

- [ ] **Step 5: Implement explicit retry and cache phases**

Retry only transient failed items, one attempt per item, never exceeding the remaining request budget. Permanent/provider-rejected/schema-invalid items remain failed with visible error codes. After main completion, rerun 50 successful unchanged items and assert 50 cache hits and no increase in HTTP attempts.

- [ ] **Step 6: Run GREEN and commit**

```powershell
& $py -m unittest tests.test_business_acceptance_batches tests.test_automation_tasks -v
git add tools/business_acceptance/batches.py tools/business_acceptance/cli.py tests/test_business_acceptance_batches.py
git commit -m "test: orchestrate budgeted review acceptance batches"
```

### Task 7: Add human business-flow automation and immutable history verification

**Files:**

- Create: `tools/business_acceptance/human_flow.py`
- Modify: `tools/business_acceptance/cli.py`
- Create: `tests/test_business_acceptance_human_flow.py`

- [ ] **Step 1: Write failing role/action/resubmission tests**

Use Flask test clients against a temporary database and real routes. Cover:

```python
assert login(group_admin).status_code in {200, 302}
assert set(list_review_forms(group_admin)) == manifest.group_scope(group_admin)
assert set(list_review_forms(department_admin)) == manifest.department_scope(department_admin)
assert set(list_review_forms(center_admin)) == manifest.global_scope(center_admin)
assert set(list_review_forms(super_admin)) == manifest.global_scope(super_admin)

approved = approve(group_admin, pending_form, '小组管理员验收通过')
assert approved.status == '部门已审核'
rejected = reject(department_admin, pending_form_2, '评价内容不符合填写要求，请修改后重交')
assert rejected.status == '已驳回'
resubmitted = resubmit(officer, rejected, compliant_payload)
assert resubmitted.status == '待审核'
assert resubmitted.id != rejected.id
assert resubmitted.unique_id == rejected.unique_id
assert version_ids(rejected.unique_id) == sorted(version_ids(rejected.unique_id))
```

Also test center and super-admin approval each produce `中心已审核`, using different forms.

- [ ] **Step 2: Run RED**

```powershell
& $py -m unittest tests.test_business_acceptance_human_flow -v
```

Expected: missing human-flow adapter.

- [ ] **Step 3: Implement route-backed actors**

`BusinessActor` owns one Flask test client and logs in through `POST /login` with the synthetic `student_id` and ignored runtime password. It must use these existing endpoints:

```text
GET  /admin/api/review/forms
GET  /admin/api/review/form/<form_id>
POST /admin/api/review/submit/<form_id>
POST /admin/api/review/reject/<form_id>
GET  /user/form/edit/<form_id>
POST /user/submit_form
```

Load the full current form payload from the detail endpoint before approving, and change only the intended business fields. Do not assign `LectureForm.status` directly.

- [ ] **Step 4: Generate deterministic human actions from actual classifications**

For each first-stage role, include all four classifications. Use this policy:

```python
def first_stage_action(category, ordinal):
    if category == '无明显风险':
        return 'approve'
    if category == '高风险疑似假表':
        return 'reject'
    return 'reject' if ordinal % 3 == 0 else 'approve'
```

Assign 75 forms per department to its group administrator and all other forms to its department administrator. Every rejected form is repaired with a valid “该老师” evaluation and, when necessary, a non-conflicting time/teacher relationship, resubmitted by its actual information-officer account, re-assessed in its original mode, then approved to `部门已审核`.

Split all department-reviewed logical forms evenly between center administrator and super administrator. At final review, reject every 25th form, confirm the opinion returns to the officer, repair and resubmit half of those final rejections through the complete department/final chain, and leave the other half rejected as an explicit final-state cohort.

- [ ] **Step 5: Verify automation and human fields remain separate**

Before every automation rerun, snapshot `(status, reviewer_id, review_time, review_comment)` for the latest version. After completion, assert exact equality. Before and after human actions, record which identity changed each field and verify `suggested_comment` was never copied into `review_comment` unless the human explicitly typed the same text in the browser sample.

- [ ] **Step 6: Run GREEN and commit**

```powershell
& $py -m unittest tests.test_business_acceptance_human_flow tests.test_review_form_draft tests.test_lecture_form_draft -v
git add tools/business_acceptance/human_flow.py tools/business_acceptance/cli.py tests/test_business_acceptance_human_flow.py
git commit -m "test: automate scoped human review acceptance flow"
```

### Task 8: Add independent verification and sanitized report generation

**Files:**

- Create: `tools/business_acceptance/verify.py`
- Create: `tools/business_acceptance/report.py`
- Modify: `tools/business_acceptance/cli.py`
- Create: `tests/test_business_acceptance_verify.py`

- [ ] **Step 1: Write failing verifier tests**

Create deliberately inconsistent temporary datasets and assert each defect is caught with a stable code:

```python
assert verify_form_counts(...).code == 'FORM_COUNT_MISMATCH'
assert verify_pagination(...duplicate_ids...).code == 'PAGINATION_DUPLICATE'
assert verify_scope(...foreign_department...).code == 'REVIEW_SCOPE_MISMATCH'
assert verify_combined_sources(...only_rule...).code == 'COMBINED_SOURCE_MISSING'
assert verify_protected_snapshot(...changed_comment...).code == 'AUTOMATION_CHANGED_HUMAN_FIELD'
assert verify_version_chain(...overwritten_old...).code == 'VERSION_HISTORY_LOST'
assert verify_batch_closure(...processed_499...).code == 'BATCH_COUNT_MISMATCH'
assert verify_export(...1499_rows...).code == 'EXPORT_COUNT_MISMATCH'
```

- [ ] **Step 2: Run RED**

```powershell
& $py -m unittest tests.test_business_acceptance_verify -v
```

Expected: missing verifier functions.

- [ ] **Step 3: Implement database-independent verification records**

Serialize only synthetic IDs, categories, statuses, counts, durations and error codes. For logical form counts, group by `unique_id` and choose latest by `(updated_at, id)`. Verify every page of the review API with a seen-ID set. Compare the same filters across page/API/database/export.

Required checks:

```text
ORG_COUNTS
LOGICAL_FORM_COUNTS
BATCH_MODE_ISOLATION
BATCH_COUNT_CLOSURE
HTTP_BUDGET
CACHE_REUSE
AUTOMATION_HUMAN_FIELD_IMMUTABILITY
GROUP_SCOPE
DEPARTMENT_SCOPE
CENTER_SCOPE
SUPERADMIN_SCOPE
STATUS_TRANSITIONS
REJECTION_RETURN
VERSION_HISTORY
PAGINATION_UNIQUENESS
EVIDENCE_CLASSIFICATION_ALIGNMENT
COMBINED_SOURCE_PRESERVATION
STATISTICS_ALIGNMENT
EXPORT_ALIGNMENT
```

For statistics and export, log in as the synthetic super administrator and use the real endpoints:

```text
GET  /admin/api/review/statistics
POST /admin/api/forms/export/check
GET  /admin/api/forms/export/download
```

Open the downloaded workbook with `openpyxl.load_workbook(..., read_only=True, data_only=True)`, count business rows from the known header, and compare the exported logical IDs/statuses to the same filter returned by the review API and database.

- [ ] **Step 4: Implement report generation**

The report includes scope, environment, dataset, three batches, concurrency stages, DeepSeek summary, four administrator scopes, resubmission/version chains, performance, verification matrix, and defects. Redact any field whose key contains `key`, `token`, `authorization`, `password`, `phone`, `student_id`, `prompt`, `response`, or `reasoning`.

Each conclusion is one of `PASS`, `FAIL`, or `BLOCKED`; do not collapse partial failures into PASS.

- [ ] **Step 5: Run GREEN and commit**

```powershell
& $py -m unittest tests.test_business_acceptance_verify -v
git add tools/business_acceptance/verify.py tools/business_acceptance/report.py tools/business_acceptance/cli.py tests/test_business_acceptance_verify.py
git commit -m "test: verify and report business acceptance evidence"
```

### Task 9: Pass the full fake-client gate before external calls

**Files:** No new files unless a failing test exposes a scoped defect in Tasks 1–8.

- [ ] **Step 1: Run focused acceptance and automation tests**

```powershell
& $py -m unittest `
  tests.test_automation_review_modes `
  tests.test_automation_concurrent_batches `
  tests.test_business_acceptance_config `
  tests.test_business_acceptance_generator `
  tests.test_business_acceptance_batches `
  tests.test_business_acceptance_human_flow `
  tests.test_business_acceptance_verify `
  tests.test_automation_service `
  tests.test_automation_tasks `
  tests.test_automation_routes `
  tests.test_deepseek_review_client -v
```

Expected: zero failures; all LLM clients fake.

- [ ] **Step 2: Run the entire repository suite**

```powershell
& $py -m unittest discover -s tests -v
& $py -m compileall -q app tools tests
node --check app/static/js/automation-center.js
git diff --check
git status --short
```

Expected: zero test failures, compile success, Node syntax success, no whitespace errors, and only intentional plan/implementation changes.

- [ ] **Step 3: Record the gate**

Run:

```powershell
& $py -m tools.business_acceptance.cli verify --phase pre-external
```

Expected:

```text
PRE_EXTERNAL_GATE=PASS real_http_attempts=0 officers=1002 forms=1500 staged_manifest=60 request_ceiling=1200
```

- [ ] **Step 4: Checkpoint to the main task**

Report commits, exact test totals, database path, manifest SHA-256, active schedule coverage, and whether Redis/API-key presence checks pass. Do not include the key or passwords. Wait for the main task to accept this checkpoint before Task 9.

### Task 10: Run real Redis/Celery and the 1000-evaluation DeepSeek exercise

**Files:**

- Create: `tools/business_acceptance/runtime.ps1`
- Create: `tests/test_business_acceptance_runtime.py`
- Modify: `tools/business_acceptance/cli.py` only if a proven runtime defect requires it.

- [ ] **Step 1: Write a failing safe-runtime lifecycle test**

Create a static and temporary-process test that asserts `runtime.ps1`:

```python
source = RUNTIME_SCRIPT.read_text(encoding='utf-8-sig')
self.assertIn('celery_worker.celery_app', source)
self.assertIn('--pool=threads', source)
self.assertIn('--concurrency', source)
self.assertIn('-WindowStyle Hidden', source)
self.assertNotIn('/IM python', source)
self.assertNotIn('/IM celery', source)
self.assertNotIn('Stop-Process -Name', source)
```

The integration portion starts a harmless recorded Python helper, verifies its PID/start time/command line, stops only that PID through the same lifecycle helper, and confirms an unrelated helper remains running.

```powershell
& $py -m unittest tests.test_business_acceptance_runtime -v
```

Expected before implementation: missing runtime script failure.

- [ ] **Step 2: Implement and verify the safe worker lifecycle**

`runtime.ps1` accepts `start-flask`, `stop-flask`, `status-flask`, `start-worker`, `stop-worker`, and `status-worker`, plus an integer `Concurrency` restricted to 1–32 for worker actions. It writes PID, UTC start time, command line, hostname and log path to separate ignored state files. Stop actions reread the live process through CIM, compare PID/start time/command line, then stop only the recorded PID tree. `Start-Process` always uses `-WindowStyle Hidden`.

```powershell
& $py -m unittest tests.test_business_acceptance_runtime -v
git add tools/business_acceptance/runtime.ps1 tests/test_business_acceptance_runtime.py
git commit -m "test: add safe acceptance worker lifecycle"
```

- [ ] **Step 3: Verify infrastructure without exposing secrets**

```powershell
if (-not $env:DEEPSEEK_API_KEY) { throw 'DEEPSEEK_API_KEY is missing' }
$redis = Test-NetConnection -ComputerName 127.0.0.1 -Port 6379 -WarningAction SilentlyContinue
if (-not $redis.TcpTestSucceeded) { throw 'REDIS_REQUIRED_FOR_STAGED_CONCURRENCY' }
```

If Redis is absent, stop this task, report `BLOCKED: REDIS_REQUIRED_FOR_STAGED_CONCURRENCY`, and ask the main task to obtain user authorization for a local Redis approach. Do not install anything automatically.

- [ ] **Step 4: Start the isolated Flask server without preparing or opening browsers**

```powershell
$env:LOCAL_DEBUG_MODE='1'
$env:INSTANCE_DIR="$repo\data\instance\acceptance-2026-08-11"
$env:SQLITE_DB_PATH="$env:INSTANCE_DIR\swu-tic-acceptance.db"
$env:DATABASE_URL=''
$env:AUTOMATION_UPLOAD_DIR="$repo\data\storage\acceptance-2026-08-11\uploads"
$env:FLASK_RUN_HOST='127.0.0.1'
$env:FLASK_RUN_PORT='5087'
$env:LOCAL_DEBUG_PORT='5087'
$env:LOCAL_DEBUG_LOG_PATH="$repo\data\storage\acceptance-2026-08-11\logs\flask.log"
$env:DEEPSEEK_MAX_RETRIES='0'
& "$repo\tools\business_acceptance\runtime.ps1" -Action start-flask
```

The runtime starts `tools/local_debug_server.py` directly and never calls `tools/prepare_local_debug.py`, so it cannot add demo users or reset acceptance passwords. Confirm `http://127.0.0.1:5087` is reachable, then rerun organization and logical-form counts to prove the 1002 synthetic officers, 10 administrators and 1500 logical forms are unchanged.

- [ ] **Step 5: Start and stop only recorded Celery worker processes**

`runtime.ps1` writes a PID/state file under the acceptance runtime. It starts hidden workers with:

```text
python -m celery -A celery_worker.celery_app worker --pool=threads --concurrency=<4|8|16> --loglevel=INFO --hostname=acceptance-<n>@%h
```

Before stopping, verify PID, start time and command line match the state file; then terminate only that PID tree. Never kill all Python or Celery processes by image name.

- [ ] **Step 6: Run 60 staged real evaluations**

For each stage, start the matching worker, run 20 forms, poll to terminal state, save metrics, and stop the recorded worker. Run:

```powershell
& $py -m tools.business_acceptance.cli run-batches --phase staged --manifest "$runtime\manifest.json"
```

Expected closure:

```text
STAGED=PASS forms=60 concurrency=4,8,16 processed=60 http_attempts<=60
```

- [ ] **Step 7: Complete R, D and RD at the selected safe concurrency**

Start one worker at the selected concurrency. Run:

```powershell
& $py -m tools.business_acceptance.cli run-batches --phase main --manifest "$runtime\manifest.json"
```

The orchestrator must not repeat the 60 staged forms. Expected:

```text
MAIN_BATCHES=PASS rules_only=500 llm_only=500 combined=500 logical_llm=1000 http_attempts<=1200
```

- [ ] **Step 8: Run explicit transient retries and cache reuse**

```powershell
& $py -m tools.business_acceptance.cli run-batches --phase retry
& $py -m tools.business_acceptance.cli run-batches --phase cache --sample-size 50
```

Expected: retries reserve only remaining budget; cache phase reports `cache_hits=50 http_attempt_delta=0`.

- [ ] **Step 9: Verify protected human fields immediately**

```powershell
& $py -m tools.business_acceptance.cli verify --phase post-automation
```

Expected: `AUTOMATION_HUMAN_FIELD_IMMUTABILITY=PASS`. Stop the entire run if it fails.

- [ ] **Step 10: Checkpoint to the main task**

Report all three batch IDs, classification counts, failures, retry codes, cache hits, selected concurrency, P50/P95/max duration, logical LLM count and HTTP attempts. Do not include prompts, raw responses, reasoning or identities.

### Task 11: Perform the full human review simulation and browser evidence capture

**Files:** Runtime artifacts only unless a business defect is separately approved for repair.

- [ ] **Step 1: Run the bulk route-backed human flow**

```powershell
& $py -m tools.business_acceptance.cli run-human-flow --manifest "$runtime\manifest.json"
```

Expected: all 4 group admins, 4 department admins, 1 center admin and 1 super admin perform both approve and reject actions; all first-stage rejections are resubmitted and re-assessed; final rejection cohort is recorded.

- [ ] **Step 2: Open one browser tab and run information-officer samples**

Use the in-app browser. Reuse one tab and sequentially log out/log in. Across four departments, use at least three officers per department and cover draft save, draft resume, submit, own records, one-form, multi-form and cross-week records.

Save:

```text
screenshots/01-officer-dept1-submit.png
screenshots/02-officer-dept2-draft.png
screenshots/03-officer-dept3-multiple-forms.png
screenshots/04-officer-dept4-cross-week.png
```

- [ ] **Step 3: Capture schedule and three automation modes**

As super admin, show active school/class/personal datasets, coverage totals and rule version. Then capture one assessment from each batch where expected sources are visible:

```text
screenshots/05-schedule-coverage.png
screenshots/06-rules-only-result.png
screenshots/07-deepseek-only-result.png
screenshots/08-combined-result.png
```

- [ ] **Step 4: Execute representative group and department admin actions manually**

Log in as one group admin from each department and one department admin from each department. For each identity, confirm queue scope and perform at least one approve and one reject through visible controls. Do not use developer tools to alter state.

Save representative evidence:

```text
screenshots/09-group-admin-queue.png
screenshots/10-group-admin-action.png
screenshots/11-department-admin-queue.png
screenshots/12-department-admin-action.png
```

- [ ] **Step 5: Execute representative center and super-admin final actions manually**

Use different department-reviewed forms. Center and super-admin approvals must both produce `中心已审核`; super admin also rejects one abnormal form and confirms global statistics.

```text
screenshots/13-center-final-review.png
screenshots/14-superadmin-global-review.png
screenshots/15-superadmin-rejection.png
```

- [ ] **Step 6: Execute an information-officer repair manually**

Log back in as the rejected officer, view the reason, edit the feedback to start with “该老师”, submit, and display old/new versions.

```text
screenshots/16-officer-rejection-reason.png
screenshots/17-officer-resubmit.png
screenshots/18-version-history.png
```

- [ ] **Step 7: Capture final scale, batch and export evidence**

```text
screenshots/19-1500-form-statistics.png
screenshots/20-batch-completion-summary.png
screenshots/21-filter-export-count.png
```

Every screenshot must show synthetic data only. If an unexpected real identity appears, do not save the screenshot; stop and report the privacy defect.

- [ ] **Step 8: Checkpoint to the main task**

Provide the screenshot directory, human action totals by role/status, rejected/resubmitted logical-form IDs, and any defects. The main task reviews the images and route/database records before accepting the phase.

### Task 12: Final independent verification and report

**Files:** Runtime report plus any already-approved scoped test fixes.

- [ ] **Step 1: Run final verification**

```powershell
& $py -m tools.business_acceptance.cli verify --phase final
```

Expected: every required check has `PASS`; genuine defects appear as `FAIL`, not omitted.

- [ ] **Step 2: Generate the report**

```powershell
& $py -m tools.business_acceptance.cli report
```

Expected files:

```text
results/forms.csv
results/batches.json
results/deepseek-summary.json
results/verification.json
report/business-flow-acceptance.md
```

- [ ] **Step 3: Rerun full regression after the exercise**

```powershell
& $py -m unittest discover -s tests -v
& $py -m compileall -q app tools tests
node --check app/static/js/automation-center.js
git diff --check
git status --short
```

Expected: zero failures. Runtime data remains ignored; no key, database, screenshots or demo content is staged.

- [ ] **Step 4: Secret and PII scan of tracked changes and report**

Run targeted scans without printing environment variables:

```powershell
git diff --cached --name-only
rg -n "sk-[A-Za-z0-9]{16,}|Authorization:|DEEPSEEK_API_KEY=.+" app tools tests docs .env.example
```

Expected: no credential value. `DEEPSEEK_API_KEY=` in `.env.example` may be empty only.

- [ ] **Step 5: Stop recorded local processes**

Stop the recorded Celery worker and Flask server through `tools/business_acceptance/runtime.ps1`. Do not stop unrelated processes. Confirm port 5087 and the acceptance worker are no longer active; Redis is stopped only if this run started a dedicated, recorded Redis instance.

- [ ] **Step 6: Final checkpoint to the main task**

Report:

- commit SHAs and exact test totals;
- 1002/1500/500+500+500 count closure;
- 1000 logical LLM evaluations and actual HTTP attempt count;
- selected concurrency and timing metrics;
- category/failure/cache totals;
- group/department/center/super-admin action totals;
- final logical statuses and version-chain totals;
- verification PASS/FAIL/BLOCKED matrix;
- clickable paths to report and screenshots;
- Git status and confirmation that no remote push occurred.

Do not claim completion if Redis, real DeepSeek, any administrator role, resubmission, export, final verification or required screenshots are missing.

---

## Main-task review checklist

The main task independently verifies before accepting the Luna task:

- [ ] Commits are scoped and contain genuine RED then GREEN evidence.
- [ ] `ReviewMode` produces actual source isolation and distinct fingerprints.
- [ ] Concurrent progress uses per-form rows and has no lost updates.
- [ ] The run used an isolated database and ignored artifact paths.
- [ ] Dataset arithmetic closes at 1002 officers and 1500 logical forms.
- [ ] The provided 2025-2026-2 schedule is the active school dataset.
- [ ] R/D/RD batches are exactly 500 each and mutually exclusive.
- [ ] Real DeepSeek logical evaluations total 1000; attempts do not exceed 1200.
- [ ] Automation did not modify any protected human-review field.
- [ ] Four group admins, four department admins, one center admin and one super admin all acted.
- [ ] Group/department approvals produce `部门已审核`; center/super approvals produce `中心已审核`.
- [ ] Rejected information officers can repair/resubmit and old versions remain.
- [ ] Page, API, database, statistics and export counts agree.
- [ ] Required screenshots are readable, synthetic and correspond to database evidence.
- [ ] Full regression, compile, JavaScript and diff checks pass.
- [ ] No key, raw prompt/response/reasoning, real identity, database or screenshot was committed.
- [ ] No attack testing, remote push or unapproved installation occurred.
