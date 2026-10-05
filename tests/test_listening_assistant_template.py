import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = (ROOT / 'app/templates/user/lecture_form.html').read_text(encoding='utf-8')
BASE = (ROOT / 'app/templates/base.html').read_text(encoding='utf-8')
SCRIPT = (ROOT / 'app/static/js/listening-assistant.js').read_text(encoding='utf-8')
STYLE = (ROOT / 'app/static/css/listening-assistant.css').read_text(encoding='utf-8')


def test_adaptive_guide_starts_with_memory_question_and_required_hooks():
    panel_position = TEMPLATE.index('data-listening-assistant')
    course_position = TEMPLATE.index('id="course-information"')
    assert panel_position < course_position
    assert 'data-assistant-toggle' in TEMPLATE
    assert 'data-assistant-panel' in TEMPLATE
    assert 'type="button"' in TEMPLATE
    assert '你还记得哪类信息？' in TEMPLATE
    for hook in (
        'data-assistant-question',
        'data-assistant-options',
        'data-assistant-custom',
        'data-assistant-custom-input',
        'data-assistant-custom-submit',
        'data-assistant-history',
        'data-assistant-progress',
        'data-assistant-back',
        'data-assistant-candidate-confirm',
        'data-assistant-confirm',
    ):
        assert hook in TEMPLATE
    assert 'A. 听课日期' in TEMPLATE
    assert 'B. 授课教师' in TEMPLATE
    assert 'C. 教室' in TEMPLATE
    assert 'D. 我自己填写' in TEMPLATE


def test_semester_is_internal_admin_default_without_student_entry():
    assert re.search(r'<input[^>]+type="hidden"[^>]+id="assistantSemester"[^>]+data-assistant-semester', TEMPLATE)
    assert '切换课表学期' not in TEMPLATE
    assert 'assistantSemesterLegacy' not in TEMPLATE
    assert 'data-assistant-settings' not in TEMPLATE
    assert 'data-assistant-fallback-panel' not in TEMPLATE


def test_guide_client_uses_server_recomputed_answer_protocol():
    assert "'/user/api/listening-assistant/guide/start'" in SCRIPT
    assert "'/user/api/listening-assistant/guide/answer'" in SCRIPT
    for marker in (
        'guideState',
        'state:',
        'question:',
        'history:',
        'requestId:',
        'controller:',
        'customMode:',
        'renderQuestion',
        'renderHistory',
        'renderCandidateConfirm',
        'answerOption',
        'submitCustomAnswer',
        'option_code',
        'custom_value',
    ):
        assert marker in SCRIPT
    assert 'candidates[0]' not in SCRIPT
    assert 'candidates.at(0)' not in SCRIPT


def test_existing_csrf_and_endpoint_contracts_are_retained():
    assert 'name="csrf-token"' in BASE
    assert 'csrf-token' in SCRIPT
    for endpoint in (
        '/user/api/listening-assistant/candidates',
        '/user/api/listening-assistant/fallback',
        '/user/api/listening-assistant/confirm',
        '/user/api/listening-assistant/guide/start',
        '/user/api/listening-assistant/guide/answer',
    ):
        assert endpoint in SCRIPT
    for hook in (
        'data-assistant-manual',
        'data-assistant-backup-ack',
    ):
        assert hook in TEMPLATE


def test_guide_keeps_safe_dom_rendering_and_request_cancellation():
    assert 'window.alert' not in SCRIPT
    assert 'window.confirm' not in SCRIPT
    assert 'innerHTML' not in SCRIPT
    for marker in ('createElement', 'textContent', 'setAttribute', 'AbortController', 'signal'):
        assert marker in SCRIPT
    assert 'isCurrentRequest' in SCRIPT
    assert 'guideState.controller.abort()' in SCRIPT
    assert 'option_code: optionCode' in SCRIPT
    assert 'custom_value: customValue' in SCRIPT


def test_candidate_confirmation_retains_evidence_and_manual_safety_hooks():
    for marker in (
        '课程', '教师', '教室', '节次', '班级', '来源', 'conflicts',
        '旧课表信息与实际一致', '实际在哪间教室', '实际听课节次', '手动填写', '都不是',
        'data-assistant-source', 'data-assistant-conflict',
        'data-assistant-backup-ack', 'data-assistant-room-choice',
        'data-assistant-period-override',
        'data-assistant-date-override',
        'data-assistant-fallback',
        'data-assistant-fallback-submit',
        'data-assistant-fallback-date',
        'data-assistant-fallback-teacher',
    ):
        assert marker in TEMPLATE + SCRIPT
    assert 'applyFieldSnapshot' in SCRIPT
    assert 'storeAssistantPayload' in SCRIPT
    assert 'field_snapshot' in SCRIPT
    assert "data-assistant-confirm'" in SCRIPT or 'data-assistant-confirm]' in SCRIPT


def test_candidate_conflicts_have_reachable_overrides_and_fallback_keeps_filters():
    for marker in (
        "conflicts.includes('date_needs_confirmation')",
        'overrides.lecture_date',
        'originalRoomForFallback',
        'knownRoom !== cleanText(candidate.room)',
        '请选择实际听课日期',
        'fallbackPayload.period',
        'fallbackPayload.student_grade_class',
        "question.kind === 'memory' || question.kind === 'candidate'",
        'data-assistant-fallback-panel',
    ):
        assert marker in TEMPLATE + SCRIPT
    assert TEMPLATE.count('id="assistantBackupAck"') == 1
    assert 'id="assistantBackupAckLegacy"' not in TEMPLATE


def test_current_request_guards_and_provenance_clear_contracts_are_retained():
    for marker in (
        'beginRequest()',
        'invalidateRequests()',
        'guideState.controller.abort()',
        'isCurrentRequest(request.requestId)',
        'finally',
        'mergePersistedAssistantProvenance',
        'clearAssistantConfirmation',
        'payload.assistant_filled_fields',
        'payload.assistant_filled_groups',
        'guide_state',
        'answer_code',
        'custom_value',
        'overrides[fieldName] = null',
        'Array.from(guideState.assistantFilledFields)',
        'Array.from(guideState.assistantFilledGroups)',
    ):
        assert marker in SCRIPT
    for start_marker in (
        'async function startGuide',
        'async function sendAnswer',
        'async function requestFallback',
        'async function confirmSelection',
    ):
        start = SCRIPT.index(start_marker)
        end = SCRIPT.find('\n        async function ', start + len(start_marker))
        body = SCRIPT[start:] if end == -1 else SCRIPT[start:end]
        assert 'const request = beginRequest()' in body
        assert 'if (isCurrentRequest(request.requestId))' in body


def test_only_live_guide_remains_with_inline_event_safety():
    assistant_panel = TEMPLATE[TEMPLATE.index('data-listening-assistant'):TEMPLATE.index('<!-- 基本信息 -->')]
    assert 'onclick=' not in assistant_panel
    assert 'data-assistant-legacy' not in assistant_panel
    assert 'data-assistant-options' in assistant_panel


def test_source_conflict_and_confirmation_contract_stays_additive():
    for marker in (
        '当前权威课表',
        '备用课表线索 · 需核对',
        'source_batch_id',
        'needs_confirmation',
        'candidate_id',
        'field_snapshot',
        'overrides',
        'template_version',
        'acknowledged_source',
        'explicit_fallback',
        'fallback_reason',
        'stage',
        'rejected_ids',
    ):
        assert marker in SCRIPT


def test_original_form_fields_and_namespaced_draft_bridge_remain():
    expected_names = {
        'registration_id', 'unique_id', 'listener_name', 'listener_number',
        'lecture_date', 'lecture_date_display', 'start_period', 'end_period',
        'class_period', 'lecture_location', 'teacher_name', 'teacher_college',
        'course_title', 'student_grade_class', 'course_changes',
        'abnormal_situation', 'teaching_method', 'classroom_discipline',
        'classroom_atmosphere', 'courseware_quality', 'overall_effect',
        'quality_case', 'course_feedback', 'suggestions', 'student_signature1',
        'contact_phone1', 'student_signature2', 'contact_phone2',
    }
    names = set(re.findall(r'\bname="([^"]+)"', TEMPLATE))
    assert expected_names <= names
    assert re.search(
        r'<input[^>]+type="hidden"[^>]+id="assistant_payload"[^>]+name="assistant_payload"',
        TEMPLATE,
    )
    assert 'data-payload-namespace="listening-assistant"' in TEMPLATE
    assert 'parseAssistantPayloadObject' in TEMPLATE
    assert 'data.assistant = assistantPayload' in TEMPLATE
    assert 'delete data.assistant_payload' in TEMPLATE
    assert 'assistant_filled_fields' in SCRIPT
    assert 'assistant_filled_groups' in SCRIPT


def test_accessibility_responsive_and_local_assets_are_present():
    assert 'aria-live="polite"' in TEMPLATE
    assert 'role="group"' in TEMPLATE
    assert 'aria-label=' in TEMPLATE
    assert 'keydown' in SCRIPT
    assert ':focus-visible' in STYLE
    assert 'min-height: 48px' in STYLE
    assert 'overflow-wrap: anywhere' in STYLE
    assert '@media (max-width: 320px)' in STYLE
    for path in (ROOT / 'app/templates/user/lecture_form.html', ROOT / 'app/static/js/listening-assistant.js', ROOT / 'app/static/css/listening-assistant.css'):
        text = path.read_text(encoding='utf-8')
        assert 'https://cdn.' not in text
        assert 'https://unpkg.' not in text
    assert '<script src="{{ url_for(\'static\', filename=\'js/listening-assistant.js\') }}"></script>' in TEMPLATE
    assert '<link rel="stylesheet" href="{{ url_for(\'static\', filename=\'css/listening-assistant.css\') }}">' in TEMPLATE


def test_guide_does_not_auto_start_before_user_enters_semester():
    init_start = SCRIPT.index("document.addEventListener('DOMContentLoaded', initListeningAssistant)")
    init_body = SCRIPT[SCRIPT.rfind('function initListeningAssistant()', 0, init_start):init_start]
    init_tail = init_body[init_body.rfind('renderAll();'):]
    assert 'if (readSemester())' not in init_tail
    assert 'startGuide(readKnownFacts(), false)' not in init_tail
    assert 'ensureStarted' in init_body


def test_confirmation_query_and_fallback_payload_cover_missing_facts_safely():
    assert 'function queryForConfirmation(candidate)' in SCRIPT
    query_start = SCRIPT.index('function queryForConfirmation(candidate)')
    query_end = SCRIPT.index('function collectOverrides', query_start)
    query_body = SCRIPT[query_start:query_end]
    for marker in ('candidate.lecture_date', 'candidate.teacher_name', 'candidate.room', 'query.lecture_date', 'query.teacher_name', 'query.semester'):
        assert marker in query_body
    assert 'source_batch_id' in SCRIPT
    assert 'explicit_fallback: true' in SCRIPT
    assert 'rejected_ids' in SCRIPT
    assert 'reason' in SCRIPT


def test_confirmation_only_applies_snapshot_after_successful_confirm_response():
    confirm_start = SCRIPT.index('async function confirmSelection()')
    confirm_end = SCRIPT.index('function handleClick', confirm_start)
    confirm_body = SCRIPT[confirm_start:confirm_end]
    assert confirm_body.index('await requestJson') < confirm_body.index('applyFieldSnapshot')
    assert confirm_body.index('applyFieldSnapshot') < confirm_body.index('storeAssistantPayload')


def test_assistant_makes_drafts_and_prefilled_facts_explicit():
    for marker in (
        'data-assistant-draft-notice',
        'data-assistant-clear-draft',
        'data-assistant-use-form-facts',
        'data-assistant-known-facts-summary',
        'lecture-form-draft-loaded',
        '新建表单',
        '核对课程',
        'localStorage',
    ):
        assert marker in TEMPLATE + SCRIPT


def test_assistant_invalidates_stale_candidates_after_form_changes():
    for marker in (
        'assistantQueryFingerprint',
        'markAssistantStateStale',
        '已检测到表单信息变化，原候选已失效',
        'data-assistant-form-context',
    ):
        assert marker in TEMPLATE + SCRIPT


def test_assistant_confirmation_has_side_by_side_context_and_terminal_done_state():
    for marker in (
        'data-assistant-comparison',
        'listening-assistant__comparison-table',
        '你已填写',
        '课表候选',
        '已完成：候选已确认',
        'elements.confirm.hidden = completed',
    ):
        assert marker in TEMPLATE + SCRIPT


def test_legacy_confirmation_hook_does_not_duplicate_live_confirmation_hook():
    assert len(re.findall(r'\bdata-assistant-confirm(?=\s|=)', TEMPLATE)) == 1
    assert 'data-legacy-assistant-confirm' not in TEMPLATE


def test_new_draft_clears_stale_date_and_period_display_text():
    assert 'if (dateInput.value)' in TEMPLATE
    assert 'dateDisplay.textContent = \'\'' in TEMPLATE
    assert 'if (!startPeriod || !endPeriod)' in TEMPLATE
    assert 'periodDisplay.textContent = \'\'' in TEMPLATE
def test_completed_candidate_view_replaces_pending_confirmation_copy():
    assert '已确认的候选与来源' in TEMPLATE + SCRIPT
    assert '课程信息已填入。' in SCRIPT
    assert 'data-completion-issues' in TEMPLATE
    assert 'data-completion-recommendation' in TEMPLATE


def test_draft_semester_does_not_override_admin_configuration():
    assert 'elements.semester.value = draftSemester' not in SCRIPT
    assert "assistant.source_kind === 'backup'" in SCRIPT
    assert 'cleanText(assistant.semester) !== readSemester()' in SCRIPT
