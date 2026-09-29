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
    ):
        assert marker in TEMPLATE + SCRIPT
    assert 'applyFieldSnapshot' in SCRIPT
    assert 'storeAssistantPayload' in SCRIPT
    assert 'field_snapshot' in SCRIPT
    assert "data-assistant-confirm'" in SCRIPT or 'data-assistant-confirm]' in SCRIPT


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
