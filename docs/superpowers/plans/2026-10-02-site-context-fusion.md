# Site Context Fusion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking. Inline execution selected; do not disturb unrelated existing changes.

**Goal:** Implement the approved robust evidence ranking and adaptive question loop in the existing photo-first trial.

**Architecture:** A pure `site_context_model.py` ranks primary schedule candidates and chooses questions. OCR stores correlated hypotheses without voting; capture endpoints retrieve current primary data and reuse existing confirmation. Original/legacy assistant paths and production feature enablement stay unchanged.

**Tech Stack:** Python/pytest, RapidOCR/Pillow, Flask, native JS/Node tests, offline preview and isolated browser replay.

### Task 1: Model behaviour
- [x] Create `tests/test_site_context_model.py` covering the approved scenarios. Example: `assert result['candidates'][0]['candidate_id']=='eight'` when GPS is at25, OCR supports0609 and only8-609 matches the clock.
- [x] Observe missing-feature test failures with `.venv-audit/Scripts/python.exe -m pytest tests/test_site_context_model.py -q`.
- [x] Implement `app/services/site_context_model.py`: robust GPS summary, building-area mixture likelihood, bounded OCR reliability and edit matching, soft time likelihood, confirmed constraints, explanatory reasons, cost-adjusted entropy questions and four-kind budget. No score is a calibrated probability.
- [x] Run the behavioural tests; fix implementation until they pass.

### Task 2: Capture evidence
- [x] Add OCR tests for same-photo contradictory readings, crop extraction, nonnumeric distractors and legacy JSON; add Node tests for returned bounded, fresh raw GPS observations. Verify RED.
- [x] Extend `lecture_site_capture.py` with automatic detected-region single-line OCR and compatible evidence decoding; extend `site-location.js` with `samples()` while retaining real best observations.
- [x] Preserve bounded samples alongside the best location in the existing JSON field. Late updates merge evidence without changing manually confirmed context. Preserve existing owner/archive/time limits.
- [x] Run focused Python and Node tests, plus three real photos offline with exact labels only for door numbers.

### Task 3: Trial integration
- [x] Add route tests for read-only suggestions, human constraints, no source, cross-user access, and candidate confirmation after context changes; observe failure.
- [x] Add `/site-capture/<id>/suggestions` using current authoritative same-date candidates, existing source failure behaviour and pure ranking.
- [x] Add `site-context-guide.js` and a small template mount: targeted option buttons, editable facts, top3 cards with expandable alternatives, explicit course confirmation, cancellation/rejection and stale-request guards. Persist chosen room through existing PATCH and confirm through existing POST; no inferred whole-form submission.
- [x] Run focused capture/draft/assistant contracts and Node tests. Existing record switching and manual entry must remain usable.

### Task 4: Reviewable evidence
- [x] Create `tools/site_context_replay.py` for seeded workbook-based paired synthetic inputs and a standalone loopback fusion preview. Every injected location/OCR/time perturbation is labelled simulated; preserve JSON metrics and all ground truth independently of the ranker.
- [x] Verify browser interactions and mobile widths, then test a copy of the local-debug database at a separate loopback port. Save final basic-field snapshots/screenshots; do not submit formal forms.
- [x] Write `docs/SITE_CONTEXT_FUSION.md` covering formulas/parameters, provenance, metrics, failure modes, sample limits and usage. Update this checklist with actual outcomes; no merge/push/release.

## Execution evidence

Implemented and integrated into the existing capture trial. Review preview uses a copied DB and separate cookie at5100 through tools/site_context_trial_server.py rather than another mock frontend. Python116 +10 subtests passed, Node13 passed. Three actual photos retain the true room in alternatives. Paired synthetic replays:300 cases57.7% to83.7% Top3, alternate seed200 cases67.0% to91.5%; biased-location subsets regress slightly and are explicitly reported. Independent reviewer found a Find/manual-input race; locked inputs and guide during pending Find, added regression, reviewer rechecked. Browser verified human-confirmed full1-3 periods and eight basic fields, manual no-GPS/OCR fallback, and mobile width. No formal form submission, source promotion, original-DB mutation, commit, push or release.
