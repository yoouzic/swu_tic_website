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


def test_legacy_additive_and_semester_entry_contract_is_preserved():
    assert TEMPLATE.index('data-listening-assistant') < TEMPLATE.index('id="course-information"')
    assert 'data-assistant-toggle' in TEMPLATE
    assert 'data-assistant-panel' in TEMPLATE
    assert re.search(
        r'<label[^>]+for="assistantSemester"[^>]*>[^<]*课表学期',
        TEMPLATE,
    )
    assert re.search(
        r'<input[^>]+id="assistantSemester"[^>]+data-assistant-semester',
        TEMPLATE,
    )
    assert 'placeholder="如 2026-2027-1"' in TEMPLATE
    assert 'data-assistant-semester-error' in TEMPLATE
    assert 'assistantSemesterLegacy' in TEMPLATE


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
        'data-assistant-rescue',
        'data-assistant-select',
        'data-assistant-reject',
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
        '备用来源确认', 'room choice', 'period override', '手动填写', '都不是',
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
        '请填写日期覆盖值',
        'fallbackPayload.period',
        'fallbackPayload.student_grade_class',
        "question.kind === 'memory' || question.kind === 'candidate'",
        'data-assistant-fallback-panel',
    ):
        assert marker in TEMPLATE + SCRIPT


def test_current_request_guards_and_provenance_clear_contracts_are_retained():
    for marker in (
        'beginRequest()',
        'invalidateRequests()',
        'guideState.controller.abort()',
        'isCurrentRequest(request.requestId)',
        'finally',
        'mergePersistedAssistantProvenance',
        'payload.assistant_filled_fields',
        'payload.assistant_filled_groups',
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


def test_legacy_candidate_region_and_inline_event_safety_remain_present():
    candidate_start = TEMPLATE.index('data-assistant-candidates')
    candidate_end = TEMPLATE.index('data-assistant-none', candidate_start)
    candidate_region = TEMPLATE[candidate_start:candidate_end]
    assert 'role="region"' in candidate_region
    assert 'tabindex="0"' in candidate_region
    assistant_panel = TEMPLATE[TEMPLATE.index('data-listening-assistant'):TEMPLATE.index('<!-- 基本信息 -->')]
    assert 'onclick=' not in assistant_panel
    assert 'data-assistant-select' in candidate_region
    assert 'data-assistant-reject' in candidate_region


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
