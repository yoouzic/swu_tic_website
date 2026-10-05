# Submission Permissions Implementation Plan

> **For agentic workers:** Use subagent-driven-development for independent route, interface and permission-lifecycle tasks. Steps use checkbox syntax for tracking.

**Goal:** Separate shared course lookup from authorized personal submission and global management.

**Architecture:** A shared capability resolver in app/utils/submission_permissions.py supplies a method-aware guard in app/security.py and the template context. Keep existing role_required management bypass and nonpersistent assistant APIs. Express explicit submission revocation in the existing SystemSetting table within the permission-update transaction.

**Tech Stack:** Flask, SQLAlchemy, Jinja, vanilla JavaScript, pytest/unittest and isolated browser verification.

## Task 1: Capability and guards

Files: app/utils/submission_permissions.py, app/security.py, tests/test_submission_permissions.py.

- [x] Write and run failing capability/guard tests for officer, permitted/unpermitted manager, super even with permission, inactive and NULL active, personal override/default, explicit revocation and forged session role.
- [x] Implement the shared API:

```python
can_submit_lecture_form(user)
set_submission_permission_revoked(user_id, revoked)
submission_permission_revoked(user_id)

protected_draft_handler = submission_required(
    api=True, methods=('PUT', 'DELETE'),
)(lecture_form_draft)
```

The guard authenticates against the database, returns JSON success/data/message for APIs, or flashes a Chinese reason and redirects HTML to main.index. Methods excluded by the optional methods tuple retain authenticated read access. Markers are added/deleted using db.session without committing inside helpers.

- [x] Run: `..\SWU_TIC-main\.venv\Scripts\python.exe -m pytest tests/test_submission_permissions.py -q` until all pass.

## Task 2: Guard backend writes, keep reads

Files: app/blueprints/user/forms.py, reservations.py, site_capture.py; tests/test_submission_route_permissions.py.

- [x] Add failing route-level tests and snapshot business tables plus upload directory before denied requests.
- [x] Add `@submission_required()` to submit_form/edit_form, `@submission_required(api=True)` to delete_form and reservation writes; use method tuples for mixed draft/site-capture routes.
- [x] Force unpermitted listening-center requests to the records tab and compute reservation mutation flags from the shared capability. Keep own historical reads and nonpersistent assistant APIs.
- [x] Run focused route tests plus submission transaction, draft, photo lifecycle, reservations and security suites.

## Task 3: Role-correct navigation and query/read-only UI

Files: app/app.py, app/ui/navigation.py, app/ui/workspace.py, app/templates/main/_leave_reminder_modal.html, app/templates/user/activity_center.html, _records_panel.html, profile.html, app/templates/admin/course_feedback_management.html; new app/blueprints/user/course_lookup.py, app/templates/user/course_lookup.html, app/static/js/course-lookup.js; app/blueprints/user/__init__.py; relevant navigation/query/template tests.

- [x] First run failing tests asserting super has no fill links, unpermitted manager receives lookup/records UI, authorized manager retains fill links and standalone lookup never renders lectureForm/site-capture/draft scripts.
- [x] Inject `can_submit_lecture_form` capability into the shared context; navigation receives the same computed boolean and role/auth/capability conditions all apply.
- [x] Register standalone authenticated course_lookup. Fetch only existing candidates GET API; display escaped course identity/candidate conflicts and explicit empty/error states. Use the configured semester with no user-selectable semester.
- [x] Hide record mutation controls and registration tab when capability is false, preserving read links; add course lookup link to super course management.
- [x] Run affected Python tests and `node --check app/static/js/course-lookup.js`.

## Task 4: Revocation and role transitions

Files: app/blueprints/admin/users.py; tests/test_submission_permission_lifecycle.py.

- [x] Run failing tests for permission clearing under an inherited default, explicit regrant, direct role changes and preserving histories.
- [x] During permission update, set the deny marker iff selected valid permissions omit 填表; include it in the same transaction. Filter revoked 填表 from effective permission readback.
- [x] During an actual direct role change, remove old personal 填表 grants and set deny marker, preserving other permission rows/history. Delete marker when the user is physically deleted.
- [x] Run lifecycle and existing functional/security authorization tests.

## Task 5: Review and verification

- [x] Review spec coverage, implementation scope, API shapes, early rejection, permission freshness and preserved history.
- [x] Run affected suites once together, then full Python regression; run affected JS checks/tests, template compilation and git diff --check for the task changes.
- [x] Use Playwright on an isolated loopback runtime to verify officer, manager with/without permission and super at desktop/mobile widths. Inspect direct access feedback, lookup results, history controls and console/overflow, saving screenshots under output/2026-10-04-role-boundaries.
- [x] Save a concise verification report and mark these checkboxes with observed results. Retain all existing uncommitted work and do not publish or merge.

## Plan self-review

Each spec requirement maps to Tasks 1–5. The same capability function is used by guards and context. Every mutable route is explicitly included; pure assistant confirm is preserved. Revocation markers cannot grant rights. All test runtimes use isolated SQLite/storage. Implementation authorization already covers this plan.

## Execution results

100 targeted tests passed; full regression 1666 passed plus 3 launcher cases passed after runtime correction; 145 JS tests and 25 browser scenarios passed. Detailed evidence: output/2026-10-04-role-boundaries/实施验收报告.md.
