# SWU TIC Website — UI Consistency & Edge-case QA Final Pass

Date: 2026-08-13
Branch: `agent/ui-ux-third-pass-finalization`
Baseline: `0a2b193a0f56d0486012c88ec189249b0a9bce85`
Scope: Review, People, Member Detail, Statistics, Workspace, and shared UI contracts

## 1. Decision

`UI_CONSISTENCY_EDGE_CASE_PASS_COMPLETE`

The high-frequency administrative flows now use coherent component semantics, truthful empty and zero states, resource-specific permission feedback, and container-responsive member detail filters. The final recommendation is `UI_DETAIL_REFINEMENT_CAN_FREEZE`.

The final verification ledger in section 8 is green. No commit, push, production data mutation, or department remote operation was performed.

## 2. Baseline and method

The worktree was clean and up to date at the fixed baseline before the pass began. The pre-change full suite established a green baseline:

- 376 tests passed in 464.737 seconds.
- Existing SQLAlchemy `LegacyAPIWarning` output was present; there were no test failures.
- Review, People, member-detail, statistics, workspace, and permission states were then inspected as related workflows rather than isolated pages.

The implementation followed a RED → GREEN sequence. The new focused contract initially produced 15 behavior failures across 10 tests. These were implementation failures, not harness failures. Additional browser-discovered defects—the password autocomplete warning, missing hidden username semantics, and drawer-filter page navigation—were each reproduced with a failing test before being corrected.

## 3. Root causes confirmed

1. **Review semantics were coupled to a table-shaped presentation.** The primary review queue used table markup even though each record is an action-rich work item. This forced narrow layouts into rigid columns and obscured action priority.
2. **Permission scope had an internal representation but no canonical presentation model.** Routes and templates exposed raw values or generic “权限不足” messages, so users could not tell what was denied or what scope applied.
3. **Member detail depended on viewport breakpoints inside an off-canvas container.** Bootstrap `col-md-*` behavior tracked the browser width rather than the drawer width, causing the filter form to remain multi-column when its actual container was narrow.
4. **Evaluation display conflated absence with zero.** A member without evaluation samples could appear to have a real rating or a negative-zero deduction; “current week” could also render as week zero outside a teaching week.
5. **Shared emphasis conventions were not consistently applied.** Status, primary action, summary metric, and decorative color roles were mixed, especially in Review statistics and historic Bootstrap color utilities.
6. **Permission denial was handled route by route.** Generic HTML flashes and ad-hoc JSON bodies produced inconsistent wording and weak machine-readable errors.

## 4. Findings and fixes by workflow

### 4.1 Review

**CONFIRMED and fixed**

- Replaced the main review queue table with a semantic list of `article` work items; version history remains a real table.
- Flattened nested card shells and made the queue, metadata, status, and primary action hierarchy explicit.
- Added a clear empty state and retained disabled/view-only behavior for forms that are outside the current reviewer stage.
- Added canonical Chinese presentation labels for group, department, center, and unavailable review permissions.
- Removed the redundant scope-search button; the remaining input is a live filter.
- Made statistics cards neutral by default so color communicates state rather than decoration.

### 4.2 People

**CONFIRMED and protected**

- Existing mature department/group cards and user-management actions were retained.
- Add/edit user forms now provide correct username and password autocomplete semantics.
- The confirmation form includes a hidden username field, eliminating the remaining browser password-form warning without changing the deletion contract.

### 4.3 Member Detail

**CONFIRMED and fixed**

- Introduced a neutral member-detail component structure and container-query responsive layout.
- The filter grid now reacts to drawer width, not global viewport width, and becomes a single column at narrow container sizes.
- Rebalanced identity, metric, and section density for desktop and mobile drawers.
- Filter submissions now refresh the fragment in place, keep the drawer open, preserve the selected user, and avoid a full-page route transition.
- Long/empty content and narrow widths remain horizontally contained.

### 4.4 Statistics

**CONFIRMED and fixed**

- Members with no evaluation samples are excluded from percentile calculation and receive the truthful label `暂无评级`.
- The deduction display normalizes negative zero to `0.00`.
- When the current date is outside a teaching week, the UI states that condition instead of showing week 0.
- Review statistics modal cards use the same neutral metric language at mobile and desktop widths.

### 4.5 Workspace

**NOT REGRESSED**

- Shared shell, navigation, role-specific primary actions, and existing workspace metrics were kept intact.
- Browser checks on adjacent People and permission-return flows found no new horizontal overflow or shell regression.

### 4.6 Shared components and permission feedback

**CONFIRMED and fixed**

- Added one permission-feedback utility for resource naming, HTML flash messages, and JSON payloads.
- HTML denial now identifies the inaccessible resource: `无法打开“{资源}”：当前账号没有该功能权限。`
- JSON denial now consistently carries `success: false`, `code: "FORBIDDEN"`, `resource`, and `message`.
- Core administration occurrences of the naked `权限不足` message were replaced with resource-aware feedback.

## 5. New issues discovered during the pass

### Fixed

- Password inputs lacked complete autocomplete context, producing browser console warnings.
- The member-detail confirmation form lacked a hidden username autocomplete field.
- Submitting filters inside the drawer navigated the whole page to the fragment URL instead of updating the open drawer.

### Deferred

- SQLAlchemy `Query.get()` deprecation warnings remain. They are backend modernization debt and do not affect this UI freeze decision.
- Legacy pages outside the high-frequency scope still contain semantic Bootstrap color utilities, inline styles, and grid classes. A blind global rewrite would change mature behavior; these should be migrated route by route with focused contracts.

### Not worth changing in this pass

- Bootstrap `btn-close` is the correct Bootstrap 5 close control and is not a legacy standalone `close` class.
- `word-break` rules remain where they deliberately contain long identifiers or labels.
- Real tabular history stays a table; only the action-oriented Review queue was converted to a list.

## 6. Frozen component standards

| Component | Frozen standard |
| --- | --- |
| Button hierarchy | One visually primary action per immediate decision area; secondary navigation/actions stay neutral; danger color is reserved for destructive actions. |
| Panel/card | Use one structural surface per section. Avoid card-inside-card decoration unless the nested surface has an independent interaction or state. |
| Status | Color and badge treatment describe workflow state, never decorative variety. Text remains the authoritative status signal. |
| Permission feedback | Name the denied resource and explain that the current account lacks that feature permission. JSON also exposes `FORBIDDEN` and the resource name. |
| Container-responsive form | Forms embedded in drawers/modals respond to their container. Do not infer usable width solely from viewport breakpoints. |
| Metric | Distinguish zero from missing data. Neutral cards are the default; semantic emphasis must have a state meaning. |
| Empty state | State what is empty, avoid implying an error, and leave no dead primary action. |
| Review work item | Use a semantic list/article for action-rich review records; use a table only for genuinely comparative or historical rows. |

## 7. Browser evidence

All checks used the isolated local debug instance at `127.0.0.1:5094`, with dedicated runtime/storage roots. Two temporary Review records were created only in the isolated database and removed after verification; the isolated `LectureForm` count returned to zero.

### Pages and states exercised

- Review queue: empty, reviewable, wrong-stage/disabled, long content, scope label, and live filtering.
- Review statistics modal: populated cards at desktop and mobile widths.
- People: established department/group card layout and user actions.
- Member detail drawer: populated identity, no-sample statistics, non-teaching-week state, filter interaction, and narrow container behavior.
- Permission feedback: HTML redirect/flash and JSON 403 response under a non-privileged account.

### Viewport and component-width matrix

| Viewport | Queue / list / item width (px) | Preview | Horizontal overflow |
| --- | ---: | --- | ---: |
| 1280×800 | 338 / 308 / 306 | visible | 0 |
| 1024×768 | 736 / 706 / 704 | hidden | 0 |
| 960×800 | 672 / 642 / 640 | hidden | 0 |
| 900×800 | 828 / 798 / 796 | hidden | 0 |
| 768×1024 | 712 / 694 / 692 | hidden | 0 |
| 390×844 | 319 / 301 / 299 | hidden | 0 |

Additional component checks:

- At 1440 px, the member drawer was 720 px wide; its content and 412 px filter area had no horizontal overflow.
- At 390 px, the drawer matched the 390 px viewport, the filter grid collapsed to one column, and horizontal overflow remained zero.
- After an in-drawer `NO_MATCH` filter submit, the browser remained at `/admin/manage_departments`, the drawer stayed open for user 3, the search value remained present, and the fragment rendered its empty state.
- Review statistics used two contained cards at 390 px and two balanced columns inside a 1140 px modal at 1440 px.
- A non-privileged account received the exact resource-aware HTML message and a 403 JSON body with the expected `FORBIDDEN` contract.
- The post-fix browser console contained no new password/autocomplete warnings. The only intentional error observed was the expected 403 from the explicit permission probe.

The retained screenshots in `docs/ui/screenshots/2026-08-13-consistency-final/` include before/after desktop and mobile states, Review list and empty states, scope labels, People cards, the member drawer, statistics modal, and permission feedback.

## 8. Verification ledger

| Gate | Result |
| --- | --- |
| Pre-change full suite | PASS — 376 tests, 464.737 s |
| Genuine RED | PASS — 10 tests exposed 15 behavior failures |
| Focused UI/regression suite | PASS — 54 tests, 5.391 s |
| Intermediate full suite | PASS — 387 tests, 428.942 s |
| Final full suite on latest code | PASS — 388 tests, 431.235 s test time / 433.992 s wall time, exit 0 |
| Python AST parse | PASS — `AST_OK 52` |
| JavaScript syntax check | PASS — `JS_CHECK_OK 5`; changed inline scripts also executed in browser without syntax errors |
| `git diff --check` | PASS — exit 0; only Git's informational LF→CRLF working-copy warnings |
| Browser desktop/mobile/component-width matrix | PASS |
| Browser interactions and console | PASS |
| Screenshot visual review | PASS |

The final static scan also confirmed zero naked `权限不足` messages and zero `table-layout` declarations in the scoped templates/static sources. Remaining class counts were classified rather than globally rewritten because many represent correct semantic status, Bootstrap 5 components, deliberate containment, or out-of-scope mature routes. The final full-suite log contained 12 `LegacyAPIWarning` lines and no failures.

## 9. Remaining concrete debt

1. Replace SQLAlchemy legacy `Query.get()` calls with session-based APIs in a dedicated backend change.
2. Continue route-by-route removal of legacy inline styles and decorative contextual colors only when that route has a focused contract and browser evidence.
3. Consider promoting permission-resource names into a typed registry if more blueprints adopt the shared denial contract.
4. Keep future drawer/modal forms container-responsive; viewport-only grid utilities should be treated as a regression risk.

None of these items requires another UI-detail pass for the workflows covered here.

## 10. Freeze recommendation

`UI_DETAIL_REFINEMENT_CAN_FREEZE`

The remaining work is maintainability debt or deliberate out-of-scope migration, not a known blocking inconsistency in the reviewed flows. Future changes should preserve the frozen component standards and re-run the focused contracts plus representative 1440 px, 1024 px, 768 px, and 390 px browser checks.
