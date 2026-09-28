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
