# SWU TIC Website — Component Robustness & UI Consistency Final Report

Date: 2026-08-13
Repository: `SWU_TIC-submit`
Branch: `agent/ui-ux-third-pass-finalization`
Baseline HEAD: `874320d474c8b04b721c30eee8076cf6864c6b85`

## 1. Baseline

- The Git checkout is the nested `SWU_TIC-submit` repository, not its parent workspace. It is a normal checkout on a dedicated `agent/*` branch; no commit, reset, checkout, merge, or push was performed.
- The initial tracked worktree was clean. This pass added only the implementation, tests, plan, report, and screenshot evidence listed below.
- Baseline full regression: `388 tests in 331.585s — OK`.
- Runtime: sibling Python 3.10.11 virtual environment, Node 24.15.0, npm/npx 11.12.1.
- Browser QA used `http://127.0.0.1:5095` with runtime/database/storage rooted under `../tmp/final-robustness-5095`; it did not use or modify a business database.
- Browser roles: `super` for privileged flows and `user001` for contextual denial feedback.

## 2. Root Causes

1. **Visual semantics were implicit.** The shared Metric macro encoded tone but not surface role, so a Workspace strip reused the card surface and produced card-inside-container borders.
2. **Viewport width was standing in for component width.** Review scope controls used Bootstrap `col-md-*` inside a split pane that was only 342.8px wide even on a 1440px viewport. The viewport breakpoint therefore could not describe the real constraint.
3. **Column equality did not guarantee card equality.** Bootstrap columns shared a row height, but their inner group cards did not stretch to that height.
4. **Button color was being used as an action category.** Ordinary export, edit, move, add, permission, and review actions accumulated `success/info/warning` variants instead of a primary/neutral/destructive hierarchy.
5. **Copied close-control markup lacked a repository-level contract.** Thirty-eight first-party `.btn-close` buttons had no accessible name.
6. **Password confirmation semantics were incomplete.** The System Settings destructive-confirmation form had neither `current-password` metadata nor an associated real account identifier, causing browser-native diagnostics.
7. **Business tabs assumed spare mobile width.** The shared four-tab row allowed labels to wrap; the first no-wrap fix still exceeded its 358px container by 22px because button padding was not included in the component-width budget.

## 3. Verified Hypotheses

| Finding | Classification | Evidence |
|---|---|---|
| Workspace metrics rendered as bordered cards inside a bordered strip | CONFIRMED | Before: each item had `1px` border, `10px` radius, opaque surface; after: base border/radius/background are removed for `plain` while strip dividers remain. |
| Review scope controls fail according to pane width, not viewport width | CONFIRMED | At 1440px viewport the pane was 342.8125px and overflowed by 4px before the fix; at 1280px it was only 283.6875px. |
| Same-row People group cards were not equal height | CONFIRMED | Before inner heights were `109 / 124.296875 / 124.296875`; after all are `124.296875`. |
| Ordinary actions had residual rainbow semantics | CONFIRMED | Review and People contained ordinary `success/info/warning` actions; the touched high-frequency templates now have no such matches. |
| First-party close buttons lacked accessible names | CONFIRMED | Repository scan found 38 unnamed controls; final scan covers 68 total and finds 0 unnamed. |
| Member Drawer still depended on viewport grid classes | NOT_CONFIRMED | Current implementation already uses a named container and container query; runtime overflow remained 0 at 720px and 390px drawer widths. |
| Review queue still used a fixed table | NOT_CONFIRMED | Current queue is a semantic summary list; populated card overflow was 0 at 402.8125px and 334px queue widths. |
| Review statistics still relied on decorative status cards | NOT_CONFIRMED | Current neutral structural cards were retained; modal overflow was 0 at 1140px and 374px dialog widths. |
| Bare permission denial and invented zero-data state remained on priority paths | NOT_CONFIRMED | Contextual denial identifies “人员与部门”; no display `-0.00`, `第0周`, or invented rating was found. The lone `第0周` match is a parser negative test. |
| `审表_*` still leaks to users | PARTIALLY_CONFIRMED | Raw values remain, correctly, in internal permission comparisons; user-facing Review surfaces use `permission_label` and `scope_label`. |

## 4. Newly Discovered Issues

- **FIXED:** System Settings `#passwordInput` lacked `autocomplete="current-password"`.
- **FIXED:** The same password form lacked an associated username field. The route now passes the authenticated `student_id`, and the form includes a labelled visually-hidden `autocomplete="username"` input. Fresh browser console result: 0 messages, 0 errors, 0 warnings.
- **FIXED:** Four System Settings labels wrapped on a 390px viewport. A first no-wrap/scroll pass preserved words but measured 22px of tab overflow; compact component padding then produced four complete 84px × 48px labels with 0 tab and document overflow.
- **NOT_WORTH_CHANGING:** `word-break: break-word` / `overflow-wrap: anywhere` remain on bounded descriptions and file paths where containment is intentional.
- **NOT_WORTH_CHANGING:** The course import key's `break-all` rule remains scoped to an imported identifier, not prose.
- **DEFERRED:** Legacy full-page Bootstrap grids remain in mature forms. They were not converted without a reproduced container-width defect.

## 5. Implemented Phases

### Phase 1 — Shared surface and component-width contracts

- Added `variant='card'` to the Metric macro and explicit `variant='plain'` use in Workspace.
- Added the plain Metric reset while preserving tone classes and the default card behavior.
- Replaced the Review scope toolbar's viewport grid with an auto-fit component grid and wrapping action group.
- Standardized the scope action copy to “全选当前范围”.
- Stretched group cards within their existing Bootstrap columns.

### Phase 2 — Action hierarchy and accessibility

- Normalized high-frequency ordinary Review and People actions to primary or neutral styles.
- Kept actual disband/delete actions destructive.
- Added accessible names to all 38 previously unnamed first-party close buttons.

### Phase 3 — Native control semantics

- Added current-password metadata and a truthful associated username to the destructive confirmation form.
- Preserved the existing password check, endpoint, modal, and destructive confirmation flow.

### Phase 4 — Browser-discovered mobile tab resilience

- Prevented business-tab labels from breaking across lines and retained horizontal scrolling as a safe fallback for narrower or translated content.
- Reduced tab-inline padding only after a 390px DOM measurement showed 22px overflow; the final 358px component contains four complete 84px labels with no overflow.

## 6. Component Standards

- **Button:** one primary action per local task area; ordinary alternatives use secondary/outline-secondary; destructive actions alone use danger.
- **Panel/Card/Section:** use a card surface for standalone grouped content; use a plain section when a parent already supplies border/background.
- **Metric:** `variant='card'` is the compatibility default; strips explicitly request `variant='plain'`; tone changes the value meaning, not the structural surface.
- **Permission Feedback:** name the blocked resource/action and give the reason; never show only a context-free “权限不足”. Internal enum values are not product copy.
- **State Display:** unknown week is descriptive, zero deduction is unsigned, and no evaluation sample produces no rating.
- **Container Responsive Layout:** controls in panes/drawers/modals must react to their own width using grid auto-fit, flex wrapping, or container queries.
- **Feedback / Empty State:** retain in-page live feedback and honest empty states; do not fabricate business data to fill a surface.

## 7. Changed Files

### Shared/layout

- `app/templates/partials/_metric.html`
- `app/templates/main/workspace.html`
- `app/static/css/style.css`
- `app/templates/admin/review_forms.html`
- `app/templates/admin/manage_departments.html`

### Native semantics and accessibility

- `app/blueprints/admin.py`
- `app/templates/admin/_settings_imports.html`
- `app/templates/admin/course_feedback_management.html`
- `app/templates/admin/department_monthly_assessment_stats.html`
- `app/templates/admin/manage_groups.html`
- `app/templates/admin/registration_statistics.html`
- `app/templates/admin/review_assessment_stats.html`
- `app/templates/admin/submission_count_stats.html`
- `app/templates/user/lecture_form.html`
- `app/templates/user/my_forms.html`

### Tests and evidence

- `tests/test_ui_consistency_edge_cases.py`
- `tests/test_metric_component.py`
- `docs/superpowers/plans/2026-08-13-component-robustness-consistency.md`
- `docs/ui/2026-08-13-component-robustness-consistency-final.md`
- `docs/ui/screenshots/2026-08-13-final-robustness/`

## 8. Automated Verification

### RED → GREEN evidence

- Layout batch RED: 3/3 new tests failed for missing Metric variant, Review component grid, and group-card stretch. GREEN: 3/3 passed.
- Consistency/accessibility batch RED: both new methods failed; action subchecks identified all targeted class mismatches and close scan listed 38 unnamed controls. GREEN: both passed.
- Native-control batch RED: first the missing `current-password`, then the missing associated username failed independently. GREEN: both contracts passed.
- Mobile-tab batch RED: the strengthened component contract rejected 14px inline padding after browser measurement found 22px overflow. GREEN: compact padding passed the contract and a fresh 390px browser measurement reported 0 overflow.
- Existing Metric regression exposed two stale exact-string expectations after the intentional contract change; the tests were updated to assert default `card` and Workspace `plain`, then `20 tests — OK` for Metric + consistency.

### Gates

- Focused multi-area regression: `100 tests in 80.039s — OK`.
- Jinja compile: `59 templates — OK`.
- JavaScript syntax: `5 static files — node --check OK`.
- Final scope/whitespace gate: `git diff --check — OK`.
- First full run: `394 tests in 427.910s — 2 failures`, both stale Metric test expectations described above.
- Frozen-code full rerun: `395 tests in 409.073s — OK` (outer process timer: `411.746s`).
- Final first-party close scan: `68 total / 0 unnamed`.
- Final touched action-color scan: no Review/People ordinary `success/info/warning` matches.

## 9. Browser QA

### Viewports

`1440x900`, `1280x800`, `1024x768`, `960x800`, `900x800`, `768x1024`, and `390x844`.

### Measured component widths and outcomes

| Viewport / component | Widths | Outcome |
|---|---|---|
| Review 1440 | queue 402.8125px; scope 342.8125px | toolbar overflow 0; populated item overflow 0 |
| Review 1280 | queue 343.6875px; scope 283.6875px | toolbar overflow 0 |
| Review 1024 | queue 736px; scope 676px | toolbar overflow 0 |
| Review 960 | queue 672px; scope 612px | toolbar overflow 0 |
| Review 900 | queue 828px; scope 768px | toolbar overflow 0 |
| Review 768 | queue 712px; scope 664px | toolbar overflow 0 |
| Review 390 | queue 334px; scope 286px | toolbar overflow 0; populated item overflow 0 |
| Member Drawer 1440 | drawer 720px; main 452.71875px; filters 422.71875px | 2 filter columns; drawer/body overflow 0 |
| Member Drawer 390 | drawer 390px; main 349px; filters 319px | 1 filter column; drawer/body overflow 0 |
| Statistics 1440 / 390 | dialog 1140px / 374px; cards 547px / 340px | modal/body overflow 0 |
| Workspace metric strip | strip 1132px; items about 377.33px | plain items, dividers retained, body overflow 0 |
| People group cards | cards 525px × 124.296875px | all same height; body overflow 0 |
| System Settings 390 | tabs 358px; four buttons 84px × 48px | complete labels; `nowrap`; tab/body overflow 0 |

### Interactions and states

- Review: query permission/scope, select current scope, load empty and populated queues, open/close statistics modal.
- Populated Review evidence used one synthetic row in the isolated debug database only; it was deleted immediately after capture (`remaining=0`). Three Review API requests returned 200 and the browser console reported 0 errors / 0 warnings.
- People: load departments, open Member Drawer, scroll to and inspect the filter component.
- Permission: `user001` was redirected safely and saw `无法打开“人员与部门”：当前账号没有该功能权限。`.
- System Settings: verified real username `super`, `autocomplete="username"`, `autocomplete="current-password"`, four complete mobile tab labels, zero tab/document overflow, and zero console messages.
- Mature-page regression: Workspace, Course, Statistics, Lecture Form, Activity Center, and System Settings at desktop and mobile samples all had zero document overflow.

## 10. Screenshot Evidence

Root: `docs/ui/screenshots/2026-08-13-final-robustness/`

Key before evidence:

- `before/workspace-metrics-1440x900.png`
- `before/group-cards-1440x900.png`
- `before/review-scope-1440x900.png`
- `before/review-scope-390x844.png`
- `before/review-statistics-modal-1440x900.png`

Key after evidence:

- `after/workspace-metrics-1440x900.png`
- `after/group-equal-height-1440x900.png`
- `after/review-scope-1440x900.png`
- `after/review-scope-390x844.png`
- `after/review-queue-populated-1440x900.png`
- `after/review-queue-populated-390x844.png`
- `after/review-statistics-modal-1440x900.png`
- `after/review-statistics-modal-390x844.png`
- `after/member-drawer-1440x900.png`
- `after/member-filter-390x844.png`
- `after/permission-message-1024x768.png`
- `after/system-settings-390x844.png`
- Full Review viewport matrix and mature-page samples in the same `after/` directory.

All named key screenshots above were manually inspected.

### Three-pass self-review

1. **Visual consistency:** manually inspected the key before/after Workspace, Review, People, Statistics, permission, populated-queue, and mature-page screenshots. This review discovered the System Settings mobile tab wrap; the corrected screenshot was re-inspected and all four labels are complete.
2. **Component robustness:** exercised empty and populated Review queues, long synthetic content, component widths from 283.6875px to 768px, 319px Member Drawer filters, modal widths down to 374px, native date/password controls, and contextual denial feedback. No measured horizontal overflow remains.
3. **Regression safety:** rechecked mature Workspace, Course, Statistics, Lecture Form, Activity Center, and System Settings surfaces at desktop/mobile samples, then ran the frozen-code 395-test suite. No application or test file changed after that run began.

## 11. Remaining Debt

- SQLAlchemy `Query.get()` legacy warnings remain in older server code; they did not originate in this UI pass and are not browser failures.
- Internal `审表_*` values remain in authorization logic and JavaScript comparisons by design; changing storage/API enums would be a separate compatibility migration.
- Mature full-page Bootstrap grids and bounded wrapping rules remain where no component-width failure was reproduced. A repository-wide mechanical migration would create risk without current user value.
- Browser chrome zoom was not exposed as a stable controllable primitive by the CLI, so no claim is made for native 125%/150% browser zoom. The seven viewport checks and measured 283.6875px–768px Review scope widths cover the reproduced constraint directly.
- Browser QA proves automated runtime behavior; final human acceptance remains a separate claim.

## 12. Freeze Decision

The frozen implementation meets typography, container responsiveness, button hierarchy, card alignment, contextual permission, truthful state, accessibility, focused/full regression, browser, and screenshot gates. Final static and scope gates are recorded in the handoff response.

`UI_COMPONENT_ROBUSTNESS_FREEZE_READY`
