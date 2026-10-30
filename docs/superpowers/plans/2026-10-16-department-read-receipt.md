# Department Read Receipt Implementation Plan

> **For agentic workers:** Execute the backend and frontend tasks within this session, with independent review after integration. Steps use checkbox syntax for tracking.

**Goal:** Keep 部门已审核 while department reviewers can distinguish their own read/unread forms across devices.

**Architecture:** An independent per-user/per-physical-version receipt records the form revision without changing the form. Detail UIs acknowledge successful display with a CSRF-protected POST; list/detail serializers expose personal state. Existing own approvals count as read.

**Tech Stack:** Flask, SQLAlchemy, SQLite, Jinja, browser JavaScript, unittest and node:test.

### Task 1: Durable read state and route

Files: `app/models.py`, `app/app.py`, `app/services/review_read_receipts.py`, `app/services/review_read_receipt_schema.py`, `app/blueprints/admin/review.py`, `tests/test_department_read_receipts.py`, `tests/test_review_read_receipt_schema.py`, `tests/test_app_factory.py`.

- [x] Write isolated tests using `_ReviewMutationCompatibilityBase`: list and GET remain unread; POST with `expected_form_id` and `expected_form_updated_at` marks only the current user and preserves form/status/scores; repeat is idempotent; new versions and timestamp changes become unread; self/cross-department/group/center/inactive users fail closed; own approval is read.
- [x] Run `../SWU_TIC-main/.venv/Scripts/python.exe -m unittest tests.test_department_read_receipts -v`; verify failure is missing receipt behavior.
- [x] Add `ReviewReadReceipt` keyed by `user_id, form_id`, with `form_updated_at, read_at`; add a service for batched serialization and idempotent recording. Add POST `/admin/api/review/form/<int:form_id>/read-receipt` with revision validation and scope/status/latest checks. Return `{success: true, department_read: {state: 'read', read_at: '...'}}`. Add `department_read` to list/detail responses and rendered detail context. GETs must not mutate.
- [x] Repeat the test command until all pass; run existing department recheck, concurrency, draft and scope regressions.

### Task 2: Automatic acknowledgement after detail display

Files: `app/static/js/review-read-receipts.js`, `app/templates/admin/review_forms.html`, `app/templates/admin/review_form.html`, `app/templates/admin/form_detail_content.html`, `app/templates/admin/form_detail_page.html`, `app/templates/admin/view_forms.html`, `app/templates/admin/submission_count_stats.html`, `tests/review_read_receipts.test.cjs`.

- [x] Write node:test cases for rendered read badges, POST revision payload, state updates only after successful response and actionable failure text. Run `node --test tests/review_read_receipts.test.cjs` and observe missing behavior.
- [x] Show a separate personal badge `我未阅／我已阅` next to existing status. Hook receipt POST only after successful visible detail rendering in queue modal/preview, full review page and standalone detail page. Update the current queue row in place after success; preserve badge when queue re-renders; failed requests never claim read. Avoid duplicate writes for the two preview containers and stale previews from rapid clicks.
- [x] Run new tests plus existing review queue/template and JavaScript checks.

### Task 3: Integration evidence

- [x] Review the feature against the confirmed spec and review code quality independently.
- [x] Start a disposable SQLite app, exercise a real manager session with CSRF, verify GET/list versus displayed details, reload persistence, independent accounts and unchanged status; capture desktop/mobile output if browser tools are available.
- [x] Run `git diff --check`; inspect only feature diffs and preserve all pre-existing changes. Leave changes reviewable locally; deployment and publication are outside this request.

## Verification receipts

- Python: 129 checks passed across personal receipts/schema migration, department recheck, review scope/drafts/mutation correctness, factory and template/UI regressions. Fault-injection mutation cases intentionally log exceptions before asserting rollback.
- JavaScript: 50 checks passed, including 13 receipt tests for badges, exact revisions, paint/visibility, deduplication, retry, stale responses, queue cache, and the two additional detail modals.
- Independent review reproduced and then verified fixes for stale concurrent receipt overwrite and Windows SQLite test connection cleanup. Final review found no remaining material issue.
- Real local Chrome, disposable SQLite and real login with CSRF: desktop preview marks only the opened form; reload retains one read and three unread; 390px mobile modal marks a second form and returns to list without horizontal overflow (scroll width 375 <= innerWidth 390); full review and standalone detail mark the remaining forms while all statuses stay 部门已审核.
- Evidence: local visualization task folder `receipt-validation/queue-desktop.jpg`, `queue-mobile.jpg`, `python-regression.log`, `serve_fixture.py`. No production database or deployment involved.
