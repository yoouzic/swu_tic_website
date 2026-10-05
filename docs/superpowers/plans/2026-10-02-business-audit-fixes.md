# Business Audit Fixes Implementation Plan

> **For agentic workers:** Use test-driven-development and independent, file-owned workers. Root integrates and performs spec compliance review before quality review. Follow the user's confirmed two-stage review policy.

**Goal:** Repair the 15 confirmed current business defects and directly related input-loss/entry gaps in the 2026-10-02 audit, while preserving group/department as a shared first review stage.

**Architecture:** Keep Flask/Jinja routes and existing version/history contracts. Guard review transactions against stale concurrent writes; share authoritative timetable selection for selectable courses. Preserve historical registrations and photos while making current operations and UI state consistent. No production data migration, remote publication, security redesign, new review stage, or unrelated photo/OCR changes.

**Tech Stack:** Python 3.11, Flask, SQLAlchemy, SQLite, Jinja/Bootstrap, browser JavaScript, pytest and Node tests.

**Baseline:** HEAD e0ef4ef plus user changes. Existing audit: 1447 passed / 9 failed with capture mode on; retry in normal mode restored 6, leaving 3 obsolete architecture counts. JS 68 passed. Preserve baseline source bytes under output/2026-10-02-business-fixes.

## Phase 1: P1 correctness

### Task A: Concurrent review protection (root)

Files: app/services/review_application.py, app/blueprints/admin/review.py, tests/test_business_review_fixes.py; existing review policy/transaction tests.

- [x] Add a real two-client concurrency regression using the audit barrier after status/latest checks; assert only one append succeeds and the winner's correction stays latest.
- [x] Run the new test and preserve RED output.
- [x] Add an atomic transaction-level compare-and-set for the expected original/latest physical version and unchanged state, including rejection racing with approval; return an actionable conflict while preserving the losing draft.
- [x] Verify successful sequential group/department initial review and center final review remain unchanged; errors roll back scores/history/drafts together.

Acceptance shape:
```python
assert sorted(result['success'] for result in responses) == [False, True]
assert latest.teacher_name == winner.teacher_name
assert losing_reviewer_draft_exists
```

### Task B: Authoritative course availability and atomic imports (center worker)

Files: app/blueprints/admin/schedule.py, app/blueprints/admin/courses.py, new app/services/current_courses.py if needed, app/templates/admin/_settings_imports.html, relevant schedule tests. User/reservations integration is coordinated with its owner.

- [x] Add RED replacement test: same-semester room 32-302 -> 32-303; admin and user available-course APIs expose only active-version rooms, old registration remains readable.
- [x] Select current courses from existing authoritative semester snapshot without deleting historical evidence or silently falling back from an invalid pointer.
- [x] Add RED invalid numeric-cell import: validation identifies source row, failed import retains prior canonical batch, Course and assistant rows.
- [x] Refuse partial failed imports atomically and display actionable failure details; test clean import and repeated identical import.

Acceptance shape:
```python
assert available_locations == {'32-303'}
assert previous_registration_still_exists
assert invalid_import_json['success'] is False
assert selected_batch_id == previous_batch_id
```

## Phase 2: P2 listener operations (listener worker)

Files: app/blueprints/user/forms.py, app/blueprints/user/reservations.py, app/utils/course_registration_limits.py, app/templates/user/lecture_form.html, app/templates/user/_records_panel.html; tests/test_business_listener_fixes.py.

- [x] RED -> GREEN: editing/refill keeps registration_id unless explicitly changed; hidden initial value reflects binding.
- [x] RED -> GREEN: same-date/from-only/to-only ranges parse stored display dates through canonical date semantics.
- [x] RED -> GREEN: editing an unbound registration enforces the same weekly limit and excludes itself from counts.
- [x] RED -> GREEN: deleting a pending form releases its photo record back into resumable pending state within the same transaction, without losing photo/evaluation evidence.
- [x] RED -> GREEN: failed edit validation keeps submitted values, unique_id and edit mode; do not discard or clear original draft.
- [x] RED -> GREEN: latest pending/rejected records have direct modify/refill links, including single-version legacy rejected forms.
- [x] Re-run normal reject -> refill -> submit and capture -> draft -> submit route checks.

Acceptance shape:
```python
assert edited.registration_id == original_registration_id
assert date_filtered_ids == expected_ids
assert edited_registration_exceeding_limit.status_code == 400
assert restored_capture.form_id is None
assert 'edit_mode' in failed_edit_template_context
```

## Phase 2: P2 review UI and member departure (review UI worker)

Files: app/templates/admin/review_form.html, app/blueprints/admin/users.py; tests/review_form_state.test.cjs, tests/test_business_departure_fixes.py. Backend review.py is root-owned.

- [x] RED -> GREEN: cancel/reopen preserves manual score rows; draft restore followed by autosave retains score_data even while dialog hidden.
- [x] Make immutable listener number read-only and exclude it from edit confirmation; preserve normal ownership.
- [x] Validate all required fields, both names/11-digit phones and 50-character feedback before confirmation; coordinate identical server validation with root.
- [x] RED -> GREEN: reject departure while unresolved latest pending/department-approved forms remain, return clear count/action without deactivating the member; allow departure after resolution. Existing already-inactive historical handling is root-coordinated.
- [x] Browser verify cancel/reopen, refresh/autosave, invalid fields blocked and normal review success.

Acceptance shape:
```javascript
assert.deepEqual(reopenedScores, manuallyEnteredScores);
assert.deepEqual(savedAfterDraftRestore.score_data, restoredDraft.score_data);
```

## Phase 2: P2 center settings and statistics (center worker)

Files: app/templates/admin/_settings_teaching.html, app/services/assessment_calc.py, app/blueprints/admin/assessment_stats.py; tests/test_business_center_fixes.py and Node settings tests.

- [x] RED -> GREEN: required submission 0 remains 0 through rendering and saving other fields.
- [x] RED -> GREEN: 1 member * 1 required * 4 weeks = monthly required 4; missing counts/penalties unchanged.
- [x] RED -> GREEN: reject overlapping teaching months before persistence; preserve previous valid definition, accept touching non-overlapping spans and validate calendar bounds.
- [x] Normalize assessment latest-version choice to highest physical ID; regression from compatibility timestamp drift must keep scoring/history correct without changing legacy endpoint policy.

## Phase 2: P2 review field validation and work dashboard (root)

Files: new app/services/lecture_form_validation.py, app/blueprints/admin/review.py, app/ui/workspace.py; tests/test_business_review_fixes.py, tests/test_workspace_routes.py, tests/test_business_workspace_fixes.py.

- [x] RED -> GREEN: server refuses cleared required signature, invalid phone, too-short feedback or incoherent period/date on review; validation errors do not mutate rows/scores/drafts. Preserve unchanged legacy incomplete data by validating edits according to existing field semantics where necessary and document any policy limitation.
- [x] RED -> GREEN: dashboard counts unique latest logical forms actionable by the current role/scope; group/department do not count center-stage work or old versions.
- [x] Keep group and department in the same first stage. Center only handles department-approved latest forms.

Acceptance shape:
```python
assert department_dashboard.pending_forms == 0
assert center_dashboard.pending_forms == 2
assert invalid_review_json['success'] is False
assert logical_version_count_after == logical_version_count_before
```

## Phase 3: Integration and acceptance

- [x] Root reviews each task against the audit scenario and user-authorized scope.
- [x] Independent review of spec compliance, then quality/edge cases; fix review findings before final acceptance.
- [x] Update obsolete route/function manifests to enforce current meaningful endpoint/partition contracts; do not mask actual business failures.
- [x] Focused Python suites, Node business-state suites, compile/Jinja validation and git diff --check.
- [x] Run one integrated full regression under the normal default capture flag with isolated paths and no external providers. Run capture-specific suites with their explicit fixtures.
- [x] Start independent loopback browser fixture and revalidate date filter, edit/refill binding, invalid review, cancel/reopen score, zero setting and current course replacement; save screenshots.
- [x] Save per-issue fixed/evidence/limitations ledger in output/2026-10-02-business-fixes; stop only our temporary server/tabs.
- [x] Deliver local changes and verified outcome. Do not commit user pre-existing changes, push, merge or deploy.

## Scope decisions

- Two-stage review is confirmed correct by the user; no group-review status is added.
- Incremental export's documented 'new since last export' semantics and standard historical exports are retained. Add a concise operational hint if needed; no speculative export-policy replacement.
- Existing compatibility endpoint mutation policies are not silently changed. Guard concurrency and fix shared latest-selection consumers; explicitly report any remaining legacy history contract.
- Camera/GPS/OCR and no-photo fallback retain the already approved design.

## Acceptance completion

Completed on 2026-10-03. See output/2026-10-02-business-fixes/修复报告.md for the issue ledger, browser evidence and exact regression time points. Full Python regression passed before final quality patches; fresh focused Python, final Node, final template compilation, independent reviews and isolated browser replay cover the final changes. Current local source is delivered without Git submission or deployment.
