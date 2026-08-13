# SWU TIC Second-Pass UI/UX Continuation Implementation Plan

**Execution status (2026-08-13): COMPLETE.** The continuation also discovered and fixed the course existing-ban removal gap and the member-menu overflow clipping issue. All disposable browser fixtures were removed and the isolated database returned to baseline.

> **For agentic workers:** REQUIRED SUB-SKILL: Use `test-driven-development` for every behavior change. The main session owns design, contracts, runtime evidence, review, and final documentation. Luna Max may implement only the explicitly delegated mechanical H2-14 task.

**Goal:** Close the feasible evidence and interaction gaps left by the second-pass audit without broadening permissions, inventing review defaults, or touching production data.

**Architecture:** Keep the existing Flask/Jinja/Bootstrap 5 architecture. Reduce member-row action density with Bootstrap dropdown semantics, replace legacy full-page refreshes with a same-route server-rendered list-fragment refresh, and close the course-selection evidence gap through disposable data in the isolated debug database. The review queue's explicit first scope selection remains a safety contract because its URL restoration is already implemented.

**Tech Stack:** Flask, Jinja, Bootstrap 5, Bootstrap Icons, vanilla JavaScript, Python `unittest`, isolated SQLite debug runtime, Codex in-app browser.

---

## Scope Decisions

| Previous debt | Decision | Reason |
|---|---|---|
| Review first entry requires a scope | Preserve | `review-queue.js` already persists filters and selected `user_ids` into the URL. Automatically selecting a centre-wide scope would broaden queries and reviewer intent. |
| Four member-row actions | Implement | Real 1440px and 390px screenshots show repeated visible actions consuming row width and height. |
| Course selected-state screenshot missing | Verify with disposable fixture | The contextual toolbar exists and is contract-tested; the isolated database simply contains zero courses. |
| Broad write-flow QA missing | Partially close | Exercise only isolated, reversible writes related to this continuation: group add/edit/disband and course batch-ban/add/remove. Do not submit reviews or delete real users. |
| `manage_groups.html` full-page reload | Implement | A same-route fetch can reuse server rendering and permission filtering while updating only the group-list fragment. |

## File Map

- Modify `app/templates/admin/manage_departments.html`: member-row action hierarchy only.
- Modify `tests/test_people_responsive_contract.py`: member action contract.
- Modify `app/templates/admin/manage_groups.html`: identify the server-rendered group list and refresh it after successful writes.
- Create `tests/test_manage_groups_interactions.py`: H2-14 template/interaction contract.
- Modify `docs/ui/2026-08-13-ui-ux-second-pass-audit-and-implementation.md`: continuation findings and final evidence.
- Create evidence under `docs/ui/screenshots/2026-08-13-second-pass/continuation/`.

## Task 1: People Member Action Hierarchy — Main Session

**Files:**

- Modify: `tests/test_people_responsive_contract.py`
- Modify: `app/templates/admin/manage_departments.html`

- [x] **Step 1: Write a failing contract test**

Add assertions that the dynamic member row keeps one visible `viewUser(...)` button and renders a Bootstrap dropdown with a unique `userActions${user.id}` toggle. Assert that `editUser`, `departUser`, and super-admin-only `deleteUser` remain present as `dropdown-item` buttons, with destructive actions separated and labelled. Assert the old four visible outline-button pattern is absent from the member-row fragment.

- [x] **Step 2: Verify RED**

Run:

```powershell
& '..\SWU_TIC-main\.venv\Scripts\python.exe' -B -m unittest -v tests.test_people_responsive_contract
```

Expected: failure because the member row does not yet contain `userActions${user.id}` or member action dropdown items.

- [x] **Step 3: Implement the minimal hierarchy**

Use this semantic shape inside `group-user-row__actions`:

```html
<button type="button" class="btn btn-sm btn-outline-secondary" onclick="viewUser(${user.id})">
    <i class="bi bi-eye" aria-hidden="true"></i> 查看
</button>
<div class="dropdown group-user-row__menu">
    <button class="btn btn-sm btn-outline-secondary dropdown-toggle" id="userActions${user.id}"
            data-bs-toggle="dropdown" aria-expanded="false"
            aria-label="更多用户操作：${user.name}">更多</button>
    <ul class="dropdown-menu dropdown-menu-end" aria-labelledby="userActions${user.id}">
        <li><button type="button" class="dropdown-item" onclick="editUser(${user.id})">编辑</button></li>
        <li><hr class="dropdown-divider"></li>
        <li><button type="button" class="dropdown-item text-danger" onclick="departUser(${user.id}, '${user.name}')">离任</button></li>
        <!-- physical delete stays within the existing super-admin Jinja permission gate -->
    </ul>
</div>
```

Do not change `viewUser`, `editUser`, `departUser`, `deleteUser`, permissions, or password-confirmation behavior.

- [x] **Step 4: Verify GREEN and focused regressions**

Run the RED command again, followed by:

```powershell
& '..\SWU_TIC-main\.venv\Scripts\python.exe' -B -m unittest -v `
  tests.test_people_page_header_migration `
  tests.test_people_responsive_contract `
  tests.test_ui_second_pass_contracts
```

Expected: all tests pass.

## Task 2: Group List Local Refresh — Luna Max, Main Review

**Files:**

- Create: `tests/test_manage_groups_interactions.py`
- Modify: `app/templates/admin/manage_groups.html`

- [x] **Step 1: Write the failing contract test**

The test must require:

```text
id="groupsList"
function refreshGroupList()
fetch(window.location.href
DOMParser
document.importNode
await refreshGroupList()
```

It must reject both legacy `location.reload()` calls. It must also assert the existing add/edit/disband endpoints, password-confirmation modal, and `bootstrap.Modal` calls remain present.

- [x] **Step 2: Verify RED**

Run:

```powershell
& '..\SWU_TIC-main\.venv\Scripts\python.exe' -B -m unittest -v tests.test_manage_groups_interactions
```

Expected: failure because the current page has no identified list fragment and contains two `location.reload()` calls.

- [x] **Step 3: Implement same-route fragment refresh**

Wrap only the server-rendered group-list row in `id="groupsList"`. Add an async function that fetches `window.location.href` with same-origin credentials, rejects non-OK responses, parses returned HTML, finds the next `#groupsList`, imports it into the current document, and replaces the old list. On refresh failure, retain the current list and show a warning that the write succeeded but the list could not refresh.

After successful add/edit/disband, hide the existing modal where applicable and `await refreshGroupList()`. Do not change any API route or response contract and do not use client-side JSON to reconstruct group membership.

- [x] **Step 4: Verify GREEN**

Run the RED command again and the closest group/permission tests identified during reconnaissance.

- [x] **Step 5: Main-session review**

The main session must inspect the exact diff, independently rerun the tests, verify that only the list is replaced, and reject any API, permission, confirmation, or broad template rewrite.

## Task 3: Isolated Runtime Acceptance — Main Session

**Files:**

- Evidence only under `docs/ui/screenshots/2026-08-13-second-pass/continuation/`

- [x] **Step 1: Snapshot isolated data counts**

Record counts for courses, bans, groups, users, and forms in `data/instance/debug-ui2-rich/lecture_forms-debug.db`.

- [x] **Step 2: Insert disposable course fixture**

Use the existing `Course` model or explicit SQLite columns to add a uniquely named test course to the isolated debug database. Do not modify the production/default database.

- [x] **Step 3: Verify selected-course UI**

Start the isolated server on port 5089, log in as the existing debug super-admin, select the disposable course, and verify:

```text
courseBatchToolbar.hidden == false
selectedCourseCount == 1
batchBanBtn.disabled == false
body.scrollWidth <= viewport width
```

Capture desktop and 390px screenshots.

- [x] **Step 4: Exercise reversible batch-ban write**

Use the disposable course and an isolated debug user. Add the ban through the real browser flow, verify the persisted ban through the read API/database, then remove it through the supported UI/API and verify the ban count returns to baseline.

- [x] **Step 5: Exercise group add/edit/disband**

Create a uniquely named disposable group, confirm the list updates without navigation/reload, edit its description/name, then disband it with the debug password. Verify the group count returns to baseline.

- [x] **Step 6: Clean disposable data**

Delete only fixture rows identified by their unique continuation marker. Recheck counts against the snapshot and stop the isolated server.

## Task 4: Verification and Documentation — Main Session

- [x] Run focused tests for people, groups, course UI, review queue, and second-pass contracts.
- [x] Run `git diff --check`, Python AST parsing, JavaScript syntax checks, icon-token validation, and legacy-contract scans.
- [x] Run the full `unittest discover` suite and save the complete log.
- [x] Update the audit document with findings, RED/GREEN evidence, browser evidence, cleanup evidence, exact residual debt, and files changed.
- [x] Confirm no commit, push, reset, production data access, or unrelated cleanup occurred.

## Plan Self-Review

- Spec coverage: all feasible previous debts are either implemented, verified, or explicitly preserved as safety boundaries.
- Placeholder scan: no TBD/TODO or unspecified implementation step remains.
- Type/contract consistency: existing route names, DOM IDs, confirmation callbacks, permissions, and API payloads remain unchanged.
- Scope: no review-status writes, user deletion, framework migration, API redesign, or broad visual restyling.
