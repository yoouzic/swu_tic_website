# Component Robustness & UI Consistency Implementation Plan

> **For Codex:** REQUIRED SUB-SKILL: Use `executing-plans` to implement this plan task by task.

**Goal:** Close the remaining reproducible component-width, surface, action-hierarchy, and accessibility defects without changing routes, API contracts, permissions, or protected review fields.

**Architecture:** Keep the existing Flask/Jinja/Bootstrap shell. Extend the shared Metric macro with an explicit visual variant; make narrow Review controls respond to their own container; add narrowly scoped equal-height and action-style contracts; repair first-party close-button names. Validate static contracts first, then route regressions, browser geometry, and full-suite behavior.

**Tech Stack:** Flask, Jinja, Bootstrap 5, existing CSS/JavaScript, Python `unittest`, Playwright CLI.

---

### Task 1: Freeze baseline and classify findings

**Files:**
- Inspect: `app/templates/**/*.html`
- Inspect: `app/static/css/style.css`
- Inspect: `app/static/js/**/*.js`
- Record: `docs/ui/2026-08-13-component-robustness-consistency-final.md`

- [x] Record nested checkout, branch, HEAD, dirty state, runtime isolation, tool versions, and full-suite baseline.
- [x] Inspect priority pages at desktop and mobile widths and measure the actual component widths.
- [x] Separate confirmed defects from already-fixed, valid-semantic, and deferred findings.

### Task 2: Establish RED layout contracts

**Files:**
- Modify: `tests/test_ui_consistency_edge_cases.py`
- Test: `tests/test_ui_consistency_edge_cases.py`

- [ ] Add a failing contract for `Metric(plain|card)` and Workspace plain metrics.
- [ ] Add a failing contract for a container-responsive Review scope toolbar.
- [ ] Add a failing contract for equal-height group cards.
- [ ] Run the three tests and capture genuine RED evidence.

### Task 3: Implement minimal layout fixes

**Files:**
- Modify: `app/templates/partials/_metric.html`
- Modify: `app/templates/main/workspace.html`
- Modify: `app/templates/admin/review_forms.html`
- Modify: `app/static/css/style.css`

- [ ] Add the explicit Metric variant while preserving the default card behavior.
- [ ] Replace the Review scope viewport grid with a component-scoped grid/flex layout.
- [ ] Stretch group cards only within their existing Bootstrap row.
- [ ] Run focused contracts to GREEN.

### Task 4: Establish RED consistency and accessibility contracts

**Files:**
- Modify: `tests/test_ui_consistency_edge_cases.py`
- Inspect/Modify: `app/templates/**/*.html`

- [ ] Add targeted failing checks for ordinary actions that still use status colors.
- [ ] Add a repository-wide first-party `.btn-close` accessible-name check.
- [ ] Run the new tests and capture genuine RED evidence.

### Task 5: Normalize actions and close controls

**Files:**
- Modify: `app/templates/admin/review_forms.html`
- Modify: `app/templates/admin/manage_departments.html`
- Modify: first-party templates containing unnamed `.btn-close` buttons

- [ ] Normalize only high-frequency ordinary actions; retain true danger/state semantics.
- [ ] Add `aria-label="关闭"` to every unnamed first-party `.btn-close` button.
- [ ] Run focused contracts and named UI regressions to GREEN.

### Task 6: Browser and regression verification

**Files:**
- Create: `docs/ui/screenshots/2026-08-13-final-robustness/after/*.png`

- [ ] Exercise 1440x900, 1280x800, 1024x768, 960x800, 900x800, 768x1024, and 390x844.
- [ ] Capture Review queue/scope/statistics, Member Drawer/filter, group cards, permission feedback, Workspace metrics, and mature-page regression samples.
- [ ] Check horizontal overflow, card heights, close-button names, console errors, and network failures.
- [ ] Run focused tests, template parse/AST checks, JavaScript syntax checks, full suite, `git diff --check`, and scope audit.

### Task 7: Report and freeze decision

**Files:**
- Create: `docs/ui/2026-08-13-component-robustness-consistency-final.md`

- [ ] Record before/after evidence, component widths, issue classifications, RED/GREEN proof, and residual risks.
- [ ] Perform three independent self-review passes: code/contracts, browser/regression, and screenshots/report.
- [ ] State exactly one UI-freeze decision supported by current evidence.
