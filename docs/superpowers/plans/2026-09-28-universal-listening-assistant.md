# Universal Listening Assistant Implementation Plan

> For agentic workers: REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox syntax for tracking.

**Goal:** Add a universally available listening assistant to the existing information-officer form flow, with authoritative schedule candidates, explicit correction and fallback paths, server-side revalidation, and preserved legacy submission behavior.

**Architecture:** Keep the assistant as a separate lookup/evidence service. The browser may request candidates and display them, but /user/submit_form remains the authority that validates the selected snapshot before populating or saving the existing LectureForm. Additive assistant evidence and schedule-index tables preserve provenance without changing the meaning of student-only LectureForm fields. Teacher/supervisor submission is explicitly outside this plan.

**Tech Stack:** Flask blueprints, Flask-SQLAlchemy, SQLite-compatible additive tables, Jinja templates, vanilla JavaScript, Bootstrap, Python unittest, Node syntax checks, and the repository bundled Playwright runtime for browser QA.

---

## Scope guard

This plan implements only the approved first phase:

- all authenticated users can open the assistant lookup experience;
- the selected result enters the existing information-officer LectureForm rules;
- the current direct-form route remains usable;
- teacher/supervisor fields, roles, permissions, review rules, and submission storage are not implemented here;
- no voice input, chat AI, automatic evaluation text, geolocation proof, or raw database course-table union is added.

## File map

| File | Responsibility |
|---|---|
| app/services/listening_assistant_contracts.py | Typed query, schedule-entry, candidate, conflict, and confirmation contracts |
| app/services/listening_assistant_schedule.py | Batch-linked schedule-entry persistence and authoritative/backup loading |
| app/services/listening_assistant.py | Candidate search, deduplication, rejection, fallback, and conflict semantics |
| app/services/listening_assistant_evidence.py | Server-side selection revalidation and evidence persistence |
| app/models.py | Additive schedule-entry and assistant-evidence models |
| app/blueprints/admin/schedule.py | Populate the assistant schedule index during approved schedule import |
| app/blueprints/user/listening_assistant.py | Authenticated candidate and confirmation APIs |
| app/blueprints/user/__init__.py | Import the new user route module |
| app/blueprints/user/forms.py | Safe assistant draft payload and submit-time revalidation |
| app/ui/navigation.py | Expose the assistant to every active authenticated role |
| app/templates/user/lecture_form.html | Add the assistant panel without removing legacy fields |
| app/static/js/listening-assistant.js | Accessible client state machine and candidate interactions |
| app/static/css/listening-assistant.css | Focus, cards, source badges, conflict, and mobile styles |
| tests/test_listening_assistant_service.py | Pure candidate and conflict tests |
| tests/test_listening_assistant_schedule.py | Batch/source/index tests |
| tests/test_listening_assistant_routes.py | Authentication, source gating, and response-contract tests |
| tests/test_listening_assistant_evidence.py | Revalidation, evidence, draft, and submit integration tests |
| tests/test_navigation.py | All-role assistant visibility regression tests |
| tools/qa/listening_assistant_browser_check.cjs | Real-browser desktop/mobile flow checks |

### Task 1: Freeze the assistant contracts and pure normalization rules

**Files:**

- Create: app/services/listening_assistant_contracts.py
- Create: tests/test_listening_assistant_service.py

- [ ] Step 1: Write failing contract tests

Add tests defining the exact public values used by later tasks:

~~~python
from datetime import date

from app.services.listening_assistant_contracts import (
    AssistantQuery,
    Candidate,
    normalize_room,
    parse_period,
)


def test_room_normalization_removes_leading_zero_only_for_numeric_room_codes():
    assert normalize_room('08-0309') == '8-309'
    assert normalize_room('荣昌 0309') == '荣昌 0309'


def test_period_parser_accepts_single_and_range_values():
    assert parse_period('第3-4节') == (3, 4)
    assert parse_period('7') == (7, 7)


def test_query_requires_exactly_one_primary_lookup_anchor():
    assert AssistantQuery(lecture_date=date(2026, 9, 18), room='8-309').anchor == 'room'
    assert AssistantQuery(lecture_date=date(2026, 9, 18), teacher_name='张老师').anchor == 'teacher'


def test_candidate_serializes_source_and_conflicts_without_personal_data():
    candidate = Candidate(
        candidate_id='primary:batch-1:row-2',
        lecture_date=date(2026, 9, 18),
        room='8-309',
        period=(3, 4),
        course_title='数据结构',
        teacher_name='张老师',
        teacher_college='计算机学院',
        student_grade_class='2025计算机01班',
        source_kind='primary',
        conflicts=('period_mismatch',),
    )
    payload = candidate.to_public_dict()
    assert payload['source_kind'] == 'primary'
    assert payload['conflicts'] == ['period_mismatch']
    assert 'phone' not in payload
    assert 'student_signature1' not in payload
~~~

- [ ] Step 2: Run the focused test and verify it fails

Run:

~~~powershell
& 'D:/Users/yoouzico/Desktop/worksoace_codex/students_work/bumen/swu_tic_website/SWU_TIC-main/.venv/Scripts/python.exe' -m pytest tests/test_listening_assistant_service.py -q
~~~

Expected: FAIL because the contracts module and functions do not exist.

- [ ] Step 3: Implement the minimal contracts

Create frozen dataclasses for AssistantQuery, ScheduleEntry, Candidate, and ConfirmationResult. Implement normalize_room, parse_period, normalize_class_for_display, and overlap_periods by delegating to existing normalization semantics where possible. Reject a query with no date or with both room and teacher empty; allow a query to contain both only for later conflict comparison.

Candidate.to_public_dict() must return only course identity, schedule fields, source_kind, source_label, source_batch_id, conflicts, and needs_confirmation.

- [ ] Step 4: Run the focused test and verify it passes

Run the same pytest command. Expected: all contract tests PASS.

- [ ] Step 5: Commit the contract slice

~~~powershell
git add app/services/listening_assistant_contracts.py tests/test_listening_assistant_service.py
git commit -m "feat: define listening assistant contracts"
~~~

### Task 2: Add a batch-linked assistant schedule index

**Files:**

- Modify: app/models.py
- Create: app/services/listening_assistant_schedule.py
- Modify: app/blueprints/admin/schedule.py
- Create: tests/test_listening_assistant_schedule.py

- [ ] Step 1: Write failing schedule-index tests

Use a synthetic DataFrame containing two rooms, two periods, two class variants, and two semesters:

~~~python
def test_persisted_entries_are_linked_to_the_import_batch():
    batches = persist_import_snapshot(frame, source_filename='current.xlsx', source_sha256='a' * 64)
    persist_listening_assistant_entries(frame, batches)
    entries = ListeningAssistantScheduleEntry.query.filter_by(batch_id=batches[0].id).all()
    assert len(entries) == 4
    assert {entry.venue_start_week_raw for entry in entries} == {'1-16'}


def test_primary_loader_reads_only_the_authoritative_current_batch():
    active = load_schedule_entries(source_kind='primary', semester='2026-2027-1')
    assert all(entry.source_kind == 'primary' for entry in active)
    assert {entry.batch_id} == {authoritative_batch_id}


def test_backup_loader_is_explicit_and_never_used_by_primary_search():
    assert load_schedule_entries(source_kind='primary', semester='2026-2027-1')
    assert load_schedule_entries(source_kind='backup', semester='2026-2027-1')
~~~

- [ ] Step 2: Run the focused test and verify it fails

~~~powershell
& 'D:/Users/yoouzico/Desktop/worksoace_codex/students_work/bumen/swu_tic_website/SWU_TIC-main/.venv/Scripts/python.exe' -m pytest tests/test_listening_assistant_schedule.py -q
~~~

Expected: FAIL because the additive model and loaders do not exist.

- [ ] Step 3: Add the additive schedule-entry model

Add ListeningAssistantScheduleEntry with batch_id, source_row, semester, academic_year, course_code, selection_code, teacher_name, teacher_college, course_title, student_grade_class, venue_id, location_raw, start_week_raw, weekday_raw, period_raw, venue_start_week_raw, and venue_period_raw. Add indexes on (batch_id, semester), (batch_id, location_raw), and (batch_id, teacher_name).

Do not add these fields to LectureForm and do not merge rows from the detached historical SQLite backup.

- [ ] Step 4: Implement batch persistence and loading

Implement persist_listening_assistant_entries(dataframe, batches) so it groups imported rows by batch semester, stores raw cells plus normalized lookup fields, and participates in the caller transaction. Implement load_schedule_entries(source_kind, semester) using resolve_current_schedule_snapshot() for primary authority. Primary must return only the selected batch; backup may return retired batches for the same semester only after the caller explicitly requests it.

- [ ] Step 5: Wire schedule import and schema initialization

Call persist_listening_assistant_entries() immediately after persist_import_snapshot() in app/blueprints/admin/schedule.py, before the existing commit. Add an idempotent listening-assistant init-schema CLI command or equivalent startup command that calls db.create_all() and reports the created/available tables. Do not silently rebuild or merge old schedule sources.

- [ ] Step 6: Run focused schedule tests and the existing snapshot suite

~~~powershell
& 'D:/Users/yoouzico/Desktop/worksoace_codex/students_work/bumen/swu_tic_website/SWU_TIC-main/.venv/Scripts/python.exe' -m pytest tests/test_listening_assistant_schedule.py tests/test_schedule_snapshots.py tests/test_canonical_cutover.py -q
~~~

Expected: PASS with no changes to existing canonical-source behavior.

- [ ] Step 7: Commit the schedule-index slice

~~~powershell
git add app/models.py app/services/listening_assistant_schedule.py app/blueprints/admin/schedule.py tests/test_listening_assistant_schedule.py
git commit -m "feat: add batch-linked listening assistant schedule index"
~~~

### Task 3: Implement candidate search, rejection, fallback, and conflict semantics

**Files:**

- Create: app/services/listening_assistant.py
- Modify: tests/test_listening_assistant_service.py

- [ ] Step 1: Add failing behavior tests

Use an in-memory provider containing three primary candidates in one room, two class variants with the same title/teacher/room/period, one teacher candidate in another room, one backup-only candidate, and one candidate with a period mismatch.

Assert:

~~~python
result = service.search(AssistantQuery(date, room='8-309'))
assert len(result.candidates) == 3
assert result.candidates[0].source_kind == 'primary'
assert result.always_show_none is True

rejected = service.reject(result.candidates)
fallback = service.search_by_teacher(AssistantQuery(date), '赵老师', rejected_ids=rejected)
assert fallback.candidates[0].source_kind == 'primary'

backup = service.search_by_teacher(
    AssistantQuery(date), '赵老师', rejected_ids=rejected,
    source_kind='backup', explicit_fallback=True
)
assert backup.candidates[0].source_kind == 'backup'
assert backup.candidates[0].needs_confirmation is True
~~~

- [ ] Step 2: Run the focused test and verify it fails

~~~powershell
& 'D:/Users/yoouzico/Desktop/worksoace_codex/students_work/bumen/swu_tic_website/SWU_TIC-main/.venv/Scripts/python.exe' -m pytest tests/test_listening_assistant_service.py -q
~~~

Expected: FAIL because the search service does not exist.

- [ ] Step 3: Implement the primary search path

Implement ListeningAssistantService.search() with these rules:

1. require an exact date and one room or teacher anchor;
2. filter by exact normalized room or teacher;
3. use period overlap only to attach period_mismatch/needs_confirmation, never to silently discard a found candidate;
4. merge exact visible duplicates and preserve distinct class variants;
5. return candidates in stable period-start, course-title, teacher-name, candidate-id order;
6. always expose always_show_none=True.

- [ ] Step 4: Implement rejection and explicit fallback

Implement search_by_teacher() and search_backup() so rejected IDs are excluded, backup access requires explicit_fallback=True and a reason of no_result or rejected_candidates, and every backup candidate is marked source_kind=backup, source_label=备用课表线索 · 需核对, and needs_confirmation=True.

- [ ] Step 5: Run the focused service tests and commit

Run the focused pytest command again. Expected: PASS.

~~~powershell
git add app/services/listening_assistant.py tests/test_listening_assistant_service.py
git commit -m "feat: add explainable listening assistant candidate search"
~~~

### Task 4: Add evidence, safe drafts, and submit-time revalidation

**Files:**

- Modify: app/models.py
- Create: app/services/listening_assistant_evidence.py
- Modify: app/blueprints/user/forms.py
- Modify: tests/test_lecture_form_draft.py
- Create: tests/test_listening_assistant_evidence.py

- [ ] Step 1: Write failing evidence tests

Test that a confirmed primary candidate creates evidence with source batch, query, selected candidate, overrides, and template version; a stale batch or unknown candidate is rejected; a backup candidate requires explicit source acknowledgement; and evidence is scoped to the current user/form.

- [ ] Step 2: Run the focused tests and verify failure

~~~powershell
& 'D:/Users/yoouzico/Desktop/worksoace_codex/students_work/bumen/swu_tic_website/SWU_TIC-main/.venv/Scripts/python.exe' -m pytest tests/test_listening_assistant_evidence.py tests/test_lecture_form_draft.py -q
~~~

Expected: FAIL because the evidence model and service do not exist.

- [ ] Step 3: Add the additive evidence model

Add ListeningAssistantEvidence with foreign keys to users.id and nullable lecture_forms.id, source_kind, source_batch_id, template_version, query_json, candidate_json, overrides_json, confirmation_json, confirmed_at, and created_at. Store only normalized course/schedule data and user actions; never store phone numbers, signatures, raw evaluation text, or credentials.

- [ ] Step 4: Implement revalidation and draft namespacing

Implement revalidate_selection(user, selection_payload) so it reloads the current source, verifies candidate identity and explicit backup acknowledgement, and returns a normalized field snapshot. Extend _normalize_draft_payload() with a whitelist for an assistant object containing only stage, query, rejected_ids, source_kind, candidate_id, overrides, and template_version; reject arbitrary nested keys.

- [ ] Step 5: Integrate /user/submit_form without changing legacy direct submissions

Read the optional assistant payload. If absent, preserve the existing path. If present, revalidate it before building form_data, use assistant values only for fields the user has not manually changed, create evidence after the LectureForm row is flushed, and delete draft/evidence staging state in the same successful transaction. Invalid assistant payloads return a clear 400/flash error and do not create a form.

- [ ] Step 6: Run evidence, draft, and submit regression tests

~~~powershell
& 'D:/Users/yoouzico/Desktop/worksoace_codex/students_work/bumen/swu_tic_website/SWU_TIC-main/.venv/Scripts/python.exe' -m pytest tests/test_listening_assistant_evidence.py tests/test_lecture_form_draft.py tests/test_submit_form_transaction.py tests/test_form_version_semantics.py -q
~~~

Expected: PASS with legacy direct-submit and duplicate/version behavior unchanged.

- [ ] Step 7: Commit the evidence slice

~~~powershell
git add app/models.py app/services/listening_assistant_evidence.py app/blueprints/user/forms.py tests/test_listening_assistant_evidence.py tests/test_lecture_form_draft.py
git commit -m "feat: revalidate and record listening assistant selections"
~~~

### Task 5: Expose authenticated candidate APIs

**Files:**

- Create: app/blueprints/user/listening_assistant.py
- Modify: app/blueprints/user/__init__.py
- Create: tests/test_listening_assistant_routes.py

- [ ] Step 1: Write route contract tests

Cover 401 for anonymous requests, 200 for any active authenticated role, 400 for invalid date/anchor, primary candidate responses without personal fields, rejection exclusion, and backup rejection unless the request contains an explicit fallback reason.

- [ ] Step 2: Run the route tests and verify failure

~~~powershell
& 'D:/Users/yoouzico/Desktop/worksoace_codex/students_work/bumen/swu_tic_website/SWU_TIC-main/.venv/Scripts/python.exe' -m pytest tests/test_listening_assistant_routes.py -q
~~~

Expected: FAIL because the blueprint and endpoints do not exist.

- [ ] Step 3: Implement the JSON endpoints

Add:

- GET /user/api/listening-assistant/candidates?date=YYYY-MM-DD&room=...&teacher=...&rejected_ids=...
- POST /user/api/listening-assistant/fallback with {date, teacher, rejected_ids, reason};
- POST /user/api/listening-assistant/confirm with {candidate_id, source_kind, acknowledged_source, overrides, template_version}.

All endpoints use @login_required, return {success, data, message} consistently, and never trust a client-supplied candidate snapshot as authoritative. Import the module in app/blueprints/user/__init__.py.

- [ ] Step 4: Run route tests and commit

Run the focused route test again. Expected: PASS.

~~~powershell
git add app/blueprints/user/listening_assistant.py app/blueprints/user/__init__.py tests/test_listening_assistant_routes.py
git commit -m "feat: expose authenticated listening assistant APIs"
~~~

### Task 6: Add the universal assistant UI to the existing form

**Files:**

- Modify: app/templates/user/lecture_form.html
- Create: app/static/js/listening-assistant.js
- Create: app/static/css/listening-assistant.css
- Create: tests/test_listening_assistant_template.py

- [ ] Step 1: Write template and static-contract tests

Assert that the form contains data-listening-assistant, date/room/teacher controls, candidate list, 都不是, 修改日期, 修改教室, backup source badge hooks, conflict confirmation hooks, manual path, aria-live, and the assistant script/style references. Assert that the JS contains no window.alert, window.confirm, or automatic first-candidate selection.

- [ ] Step 2: Implement the assistant panel

Place an expandable assistant panel before the existing basic-information fields. Keep all existing inputs visible and editable. Add hidden fields only for assistant session state; do not replace lecture_date, lecture_location, teacher_name, course_title, or other existing names.

- [ ] Step 3: Implement the client state machine

Use explicit states find, rescue, review, manual, and done. On every state change:

- abort or ignore stale fetch responses with a request token;
- clear rejected candidates when date or room changes;
- keep unrelated evaluation fields intact;
- show source badges and conflict messages;
- require source acknowledgement for backup candidates;
- never submit until the existing form validation passes.

Use fetch with the CSRF token already exposed by base.html; render feedback in aria-live nodes.

- [ ] Step 4: Add accessible styling

Use the existing Bootstrap/paper theme, minimum 48px action targets, visible focus states, readable card metadata, a max-width 576px layout, and no external CDN dependency.

- [ ] Step 5: Run static checks

~~~powershell
node --check app/static/js/listening-assistant.js
& 'D:/Users/yoouzico/Desktop/worksoace_codex/students_work/bumen/swu_tic_website/SWU_TIC-main/.venv/Scripts/python.exe' -m pytest tests/test_listening_assistant_template.py tests/test_activity_center.py -q
~~~

Expected: PASS.

- [ ] Step 6: Commit the UI slice

~~~powershell
git add app/templates/user/lecture_form.html app/static/js/listening-assistant.js app/static/css/listening-assistant.css tests/test_listening_assistant_template.py
git commit -m "feat: add universal listening assistant form flow"
~~~

### Task 7: Make the assistant reachable for every active authenticated role

**Files:**

- Modify: app/ui/navigation.py
- Modify: tests/test_navigation.py or create it if absent
- Modify: app/templates/user/activity_center.html only if the shared CTA needs a label change

- [ ] Step 1: Write failing navigation tests

Create users with roles 信息员, 管理员, 超级管理员, and a future-compatible role string 教师. Assert that the assistant entry is present for every active authenticated user, while existing registration/records and administration entries retain their current role restrictions.

- [ ] Step 2: Implement a separate universal assistant navigation item

Add an authenticated=True navigation definition pointing to user.submit_form with label 听课助手. Update build_navigation() to honor authenticated before role filtering. Do not broaden the existing 听课与填报 registration/records item to roles that should not see information-officer records.

- [ ] Step 3: Run navigation/workspace regressions and commit

~~~powershell
& 'D:/Users/yoouzico/Desktop/worksoace_codex/students_work/bumen/swu_tic_website/SWU_TIC-main/.venv/Scripts/python.exe' -m pytest tests/test_navigation.py tests/test_workspace_routes.py tests/test_activity_center.py -q
git add app/ui/navigation.py tests/test_navigation.py app/templates/user/activity_center.html
git commit -m "feat: expose listening assistant to authenticated users"
~~~

### Task 8: Run browser acceptance and full regression checks

**Files:**

- Create: tools/qa/listening_assistant_browser_check.cjs

- [ ] Step 1: Add browser scenarios

Use the repository bundled Playwright runtime to exercise:

1. desktop: date → room → primary candidate → review;
2. singleton and multi-candidate cards both show 都不是;
3. reject candidates → teacher fallback → backup source badge;
4. backup source cannot confirm until acknowledgement;
5. room conflict requires an explicit choice;
6. modifying date/room clears stale candidates;
7. manual path validates period range and escapes user text;
8. keyboard focus reaches date, room, cards, 都不是, and confirmation;
9. 320px viewport has scrollWidth equal to clientWidth;
10. browser console has zero page errors.

- [ ] Step 2: Run browser checks

~~~powershell
node tools/qa/listening_assistant_browser_check.cjs
~~~

Expected: a JSON summary with all scenarios PASS, consoleErrors equal to 0, and horizontalOverflow equal to false.

- [ ] Step 3: Run the full relevant regression suite

~~~powershell
& 'D:/Users/yoouzico/Desktop/worksoace_codex/students_work/bumen/swu_tic_website/SWU_TIC-main/.venv/Scripts/python.exe' -m pytest tests/test_activity_center.py tests/test_lecture_form_draft.py tests/test_submit_form_transaction.py tests/test_form_version_semantics.py tests/test_form_registration_bindings.py tests/test_schedule_snapshots.py tests/test_canonical_cutover.py tests/test_listening_assistant_service.py tests/test_listening_assistant_schedule.py tests/test_listening_assistant_routes.py tests/test_listening_assistant_evidence.py tests/test_listening_assistant_template.py tests/test_navigation.py -q
git diff --check
~~~

Expected: all selected tests PASS, no whitespace errors, and no changes to the legacy direct-submit contract.

- [ ] Step 4: Verify deployment/schema state

Run the idempotent schema command against the isolated local-debug database, verify the new tables exist, and confirm that production-data paths were not used. Do not run the import command against the operating database without separate explicit authorization.

- [ ] Step 5: Commit QA tooling and handoff

~~~powershell
git add tools/qa/listening_assistant_browser_check.cjs
git commit -m "test: verify universal listening assistant flow"
~~~

## Plan self-review

- Spec coverage: universal access is Task 7; primary/backup authority is Tasks 2–3; 都不是 and correction are Tasks 3 and 6; evidence and revalidation are Task 4; existing information-officer compatibility is Tasks 4 and 8; accessibility/browser checks are Tasks 6 and 8; teacher/supervisor separation is the scope guard and the evidence/template boundary.
- Placeholder scan: no unresolved placeholder markers or unspecified “add validation” steps are used; every task names files, tests, commands, and expected results.
- Type consistency: AssistantQuery, ScheduleEntry, Candidate, ListeningAssistantScheduleEntry, and ListeningAssistantEvidence are introduced before their consumers; API payload names are reused in the draft and submit tasks.
- Scope check: teacher/supervisor submission is explicitly excluded from implementation and requires a separate design/plan.

