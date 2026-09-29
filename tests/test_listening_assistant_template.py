import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = (ROOT / 'app/templates/user/lecture_form.html').read_text(encoding='utf-8')
BASE = (ROOT / 'app/templates/base.html').read_text(encoding='utf-8')
SCRIPT_PATH = ROOT / 'app/static/js/listening-assistant.js'
STYLE_PATH = ROOT / 'app/static/css/listening-assistant.css'


def test_assistant_panel_is_additive_and_precedes_course_fields():
    panel_position = TEMPLATE.index('data-listening-assistant')
    course_position = TEMPLATE.index('id="course-information"')
    assert panel_position < course_position
    assert 'data-assistant-toggle' in TEMPLATE
    assert 'data-assistant-panel' in TEMPLATE
    assert 'type="button"' in TEMPLATE


def test_existing_lecture_form_field_names_are_preserved():
    expected_names = {
        'registration_id',
        'unique_id',
        'listener_name',
        'listener_number',
        'lecture_date',
        'lecture_date_display',
        'start_period',
        'end_period',
        'class_period',
        'lecture_location',
        'teacher_name',
        'teacher_college',
        'course_title',
        'student_grade_class',
        'course_changes',
        'abnormal_situation',
        'teaching_method',
        'classroom_discipline',
        'classroom_atmosphere',
        'courseware_quality',
        'overall_effect',
        'quality_case',
        'course_feedback',
        'suggestions',
        'student_signature1',
        'contact_phone1',
        'student_signature2',
        'contact_phone2',
    }
    names = set(re.findall(r'\bname="([^"]+)"', TEMPLATE))
    assert expected_names <= names


def test_assistant_exposes_explicit_states_and_actions():
    script = SCRIPT_PATH.read_text(encoding='utf-8')
    for state in ('find', 'rescue', 'review', 'manual', 'done', 'error'):
        assert state in script
    for hook in (
        'data-assistant-search',
        'data-assistant-none',
        'data-assistant-manual',
        'data-assistant-rescue',
        'data-assistant-select',
        'data-assistant-reject',
        'data-assistant-confirm',
        'data-assistant-back',
        'data-assistant-backup-ack',
    ):
        assert hook in TEMPLATE
    assert '都不是 / 手动填写' in TEMPLATE
    assert 'candidates[0]' not in script
    assert 'candidates.at(0)' not in script


def test_assistant_source_and_authority_contract_hooks_are_present():
    script = SCRIPT_PATH.read_text(encoding='utf-8')
    for marker in (
        '当前权威课表',
        '备用课表线索 · 需核对',
        'conflicts',
        'needs_confirmation',
        'rejected_ids',
        'source_batch_id',
        'field_snapshot',
        'candidate_id',
        'overrides',
        'template_version',
        'acknowledged_source',
        'explicit_fallback',
        'fallback_reason',
        'stage',
    ):
        assert marker in script


def test_assistant_uses_existing_csrf_and_all_server_endpoints():
    script = SCRIPT_PATH.read_text(encoding='utf-8')
    assert 'name="csrf-token"' in BASE
    assert 'csrf-token' in script
    assert '/user/api/listening-assistant/candidates' in script
    assert '/user/api/listening-assistant/fallback' in script
    assert '/user/api/listening-assistant/confirm' in script


def test_assistant_accessibility_and_responsive_contract_hooks_exist():
    script = SCRIPT_PATH.read_text(encoding='utf-8')
    style = STYLE_PATH.read_text(encoding='utf-8')
    assert 'aria-live="polite"' in TEMPLATE
    assert 'role="status"' in TEMPLATE
    assert 'tabindex="0"' in TEMPLATE
    assert 'keydown' in script
    assert ':focus-visible' in style
    assert 'min-height: 48px' in style
    assert 'overflow-wrap: anywhere' in style
    assert '@media (max-width: 320px)' in style


def test_assistant_has_no_external_dependency_or_inline_event_sprawl():
    for path in (ROOT / 'app/templates/user/lecture_form.html', SCRIPT_PATH, STYLE_PATH):
        text = path.read_text(encoding='utf-8')
        assert 'https://cdn.' not in text
        assert 'https://unpkg.' not in text
    assert '<script src="{{ url_for(\'static\', filename=\'js/listening-assistant.js\') }}"></script>' in TEMPLATE
    assert '<link rel="stylesheet" href="{{ url_for(\'static\', filename=\'css/listening-assistant.css\') }}">' in TEMPLATE
    script = SCRIPT_PATH.read_text(encoding='utf-8')
    assert 'window.alert' not in script
    assert 'window.confirm' not in script
    panel = TEMPLATE[TEMPLATE.index('data-listening-assistant'):TEMPLATE.index('<!-- 基本信息 -->')]
    assert 'onclick=' not in panel


def test_assistant_payload_is_namespaced_hidden_json_field():
    assert re.search(
        r'<input[^>]+type="hidden"[^>]+id="assistant_payload"[^>]+name="assistant_payload"',
        TEMPLATE,
    )
    assert 'data-payload-namespace="listening-assistant"' in TEMPLATE
    script = SCRIPT_PATH.read_text(encoding='utf-8')
    assert 'JSON.stringify' in script
    assert 'assistant_payload' in script


def test_rescue_and_manual_transitions_invalidate_stale_requests_before_state_change():
    script = SCRIPT_PATH.read_text(encoding='utf-8')
    rescue_start = script.index('function openRescue()')
    rescue_end = script.index('function handleClick', rescue_start)
    rescue_body = script[rescue_start:rescue_end]
    assert 'invalidateRequests();' in rescue_body
    assert rescue_body.index('invalidateRequests();') < rescue_body.index('state.fallbackReason')

    manual_start = script.index('function openManual()')
    manual_end = script.index('function returnToFind', manual_start)
    manual_body = script[manual_start:manual_end]
    assert 'invalidateRequests();' in manual_body
    assert manual_body.index('invalidateRequests();') < manual_body.index('state.selectedCandidate')


def test_assistant_tracks_filled_fields_and_writes_null_clears_on_submit():
    script = SCRIPT_PATH.read_text(encoding='utf-8')
    for marker in (
        'assistantFilledFields',
        'assistantFilledGroups',
        'form.addEventListener(\'submit\'',
        'JSON.parse',
        'overrides[fieldName] = null',
    ):
        assert marker in script
    assert 'assistantFilledGroups.add(\'period\')' in script
    for excluded in ('student_signature1', 'student_signature2', 'contact_phone1', 'contact_phone2'):
        assert excluded not in script


def test_back_action_invalidates_inflight_confirm_before_navigation():
    script = SCRIPT_PATH.read_text(encoding='utf-8')
    back_start = script.index("if (target.closest('[data-assistant-back]'))")
    back_end = script.index("if (target.closest('[data-assistant-retry]'))", back_start)
    back_body = script[back_start:back_end]
    assert 'invalidateRequests();' in back_body
    assert back_body.index('invalidateRequests();') < back_body.index('setState(')


def test_backup_rescue_preserves_period_and_safe_query_filters():
    script = SCRIPT_PATH.read_text(encoding='utf-8')
    rescue_start = script.index('async function rescueFallback()')
    rescue_end = script.index('function selectCandidate', rescue_start)
    rescue_body = script[rescue_start:rescue_end]
    assert 'fallbackQuery.period = query.period' in rescue_body
    assert 'fallbackQuery.student_grade_class = query.student_grade_class' in rescue_body
    assert 'fallbackPayload.period = state.query.period' in rescue_body
    assert 'fallbackPayload.student_grade_class = state.query.student_grade_class' in rescue_body
    assert 'result.student_grade_class = query.student_grade_class' in script


def test_async_request_finally_only_releases_current_request_controls():
    script = SCRIPT_PATH.read_text(encoding='utf-8')
    function_ranges = (
        ('async function searchCandidates()', 'function updateRescueControls'),
        ('async function rescueFallback()', 'function selectCandidate'),
        ('async function confirmSelection()', 'async function rescueFallback'),
    )
    guarded_finally = re.compile(
        r"finally\s*\{\s*"
        r"if \(isCurrentRequest\(request\.requestId\)\) \{\s*"
        r"setBusy\([\s\S]*?false\);\s*"
        r"[\s\S]*?state\.controller = null;[\s\S]*?\}",
    )
    for start_marker, end_marker in function_ranges:
        start = script.index(start_marker)
        end = script.index(end_marker, start)
        assert guarded_finally.search(script[start:end]), start_marker


def test_assistant_filled_provenance_is_persisted_and_safely_merged_after_reload():
    script = SCRIPT_PATH.read_text(encoding='utf-8')
    for marker in (
        'assistant_filled_fields:',
        'assistant_filled_groups:',
        'Array.from(state.assistantFilledFields)',
        'Array.from(state.assistantFilledGroups)',
        'mergePersistedAssistantProvenance',
        'ASSISTANT_FILLED_FIELD_WHITELIST',
        'ASSISTANT_FILLED_GROUP_WHITELIST',
        'payload.assistant_filled_fields',
        'payload.assistant_filled_groups',
    ):
        assert marker in script
    for excluded in ('student_signature1', 'student_signature2', 'contact_phone1', 'contact_phone2'):
        assert excluded not in script


def test_lecture_form_draft_bridges_namespaced_assistant_payload_safely():
    draft_source = TEMPLATE + SCRIPT_PATH.read_text(encoding='utf-8')
    for marker in (
        'parseAssistantPayloadObject',
        'data.assistant = assistantPayload',
        'delete data.assistant_payload',
        'data.assistant || data.assistant_payload',
        'assistant_payload',
        'JSON.stringify(assistantPayload)',
        'assistant_filled_fields',
        'assistant_filled_groups',
    ):
        assert marker in draft_source


def test_candidate_results_use_a_semantically_valid_region_container():
    start = TEMPLATE.index('data-assistant-candidates')
    end = TEMPLATE.index('data-assistant-none', start)
    candidate_region = TEMPLATE[start:end]
    assert 'role="region"' in candidate_region
    assert 'role="list"' not in candidate_region


def test_request_cancellation_restores_only_the_old_button_and_reorders_begin_request():
    script = SCRIPT_PATH.read_text(encoding='utf-8')
    assert 'activeButton' in script
    assert 'activeRequestId' in script
    invalidate_start = script.index('function invalidateRequests()')
    invalidate_end = script.index('function beginRequest', invalidate_start)
    invalidate_body = script[invalidate_start:invalidate_end]
    assert 'setBusy(state.activeButton, false)' in invalidate_body
    assert invalidate_body.index('setBusy(state.activeButton, false)') < invalidate_body.index('state.controller.abort()')
    assert 'function beginRequest(button)' in script
    assert 'state.activeButton = button' in script

    function_ranges = (
        ('async function searchCandidates()', 'function updateRescueControls'),
        ('async function rescueFallback()', 'function selectCandidate'),
        ('async function confirmSelection()', 'async function rescueFallback'),
    )
    for start_marker, end_marker in function_ranges:
        start = script.index(start_marker)
        end = script.index(end_marker, start)
        body = script[start:end]
        assert body.index('const request = beginRequest(') < body.index('setBusy(', body.index('const request = beginRequest('))


def test_none_action_rejects_displayed_candidates_before_rescue_and_manual_stays_separate():
    script = SCRIPT_PATH.read_text(encoding='utf-8')
    assert 'function rejectDisplayedCandidates()' in script
    reject_start = script.index('function rejectDisplayedCandidates()')
    reject_end = script.index('function openRescue()', reject_start)
    reject_body = script[reject_start:reject_end]
    for marker in ('state.candidates.forEach', 'state.rejectedIds.includes', 'state.rejectedIds.push'):
        assert marker in reject_body
    rescue_start = script.index('function openRescue()')
    rescue_end = script.index('function handleClick', rescue_start)
    rescue_body = script[rescue_start:rescue_end]
    assert 'rejectDisplayedCandidates();' in rescue_body
    assert rescue_body.index('rejectDisplayedCandidates();') < rescue_body.index('state.fallbackReason')
    assert "if (target.closest('[data-assistant-manual]'))" in script
    assert 'openManual();' in script


def test_rescue_clears_primary_cards_and_rejects_ids_before_backup_rendering():
    script = SCRIPT_PATH.read_text(encoding='utf-8')
    clear_start = script.index('function clearPrimaryCandidatesForRescue()')
    clear_end = script.index('function openRescue()', clear_start)
    clear_body = script[clear_start:clear_end]
    assert "candidate.source_kind === 'backup'" in clear_body
    assert 'state.candidates = backupCandidates' in clear_body
    assert 'renderCandidates(state.candidates)' in clear_body
    rescue_start = script.index('function openRescue()')
    rescue_end = script.index('function handleClick', rescue_start)
    rescue_body = script[rescue_start:rescue_end]
    assert 'clearPrimaryCandidatesForRescue();' in rescue_body
    select_start = script.index('function selectCandidate(candidateId)')
    select_end = script.index('function rejectCandidate', select_start)
    select_body = script[select_start:select_end]
    assert 'state.rejectedIds.includes(candidateId)' in select_body
    assert 'setError(' in select_body


def test_unused_registration_rows_use_safe_dom_text_and_click_listeners():
    start = TEMPLATE.index('function renderUnusedRegistrations')
    end = TEMPLATE.index('function selectRegistration', start)
    body = TEMPLATE[start:end]
    assert 'createElement' in body
    assert 'textContent' in body
    assert 'addEventListener' in body
    assert 'selectRegistration(item.id, item.raw_data)' in body
    assert 'onclick' not in body
    assert 'tr.innerHTML' not in body
    assert '${item.' not in body


def test_delete_draft_cancels_pending_save_and_validates_delete_result():
    start = TEMPLATE.index('async function deleteLectureFormDraft')
    end = TEMPLATE.index('// 表单验证', start)
    body = TEMPLATE[start:end]
    assert 'window.clearTimeout(lectureFormDraftTimer)' in body
    assert 'lectureFormDraftTimer = null' in body
    assert 'lectureFormDraftDirty = false' in body
    assert 'const response = await fetch' in body
    assert 'const result = await response.json()' in body
    assert 'response.ok' in body
    assert 'result.success' in body


def test_assistant_busy_state_restores_original_button_markup_safely():
    script = SCRIPT_PATH.read_text(encoding='utf-8')
    start = script.index('function setBusy(')
    end = script.index('function setError(', start)
    body = script[start:end]
    assert 'button.dataset.originalHtml = button.innerHTML' in body
    assert 'button.innerHTML = button.dataset.originalHtml' in body
    assert 'button.textContent = label' in body
    assert 'originalLabel' not in body
