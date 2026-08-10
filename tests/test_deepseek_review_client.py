import json
import logging
import unittest
from types import SimpleNamespace
from unittest.mock import patch


from app.review_automation.llm.client import (
    DeepSeekReviewClient,
    InvalidLLMResponse,
    PermanentLLMError,
    TransientLLMError,
)


class FakeCompletions:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        response = self.responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return response


class FakeOpenAI:
    def __init__(self, responses):
        self.completions = FakeCompletions(responses)
        self.chat = SimpleNamespace(completions=self.completions)


def fake_response(content, *, reasoning_content=None):
    message = SimpleNamespace(content=content)
    if reasoning_content is not None:
        message.reasoning_content = reasoning_content
    return SimpleNamespace(choices=[SimpleNamespace(message=message)])


def fake_api_error(message, status_code):
    error = RuntimeError(message)
    error.status_code = status_code
    return error


class DeepSeekReviewClientTest(unittest.TestCase):
    FORM = {
        'form_id': 'SYNTH-FORM-001',
        'teacher_name': 'SYNTHETIC_TEACHER',
        'contact_phone1': 'SYNTHETIC_PHONE_0001',
        'course_feedback': 'INVENTED_FEEDBACK_MARKER',
    }
    VALID = {
        'compliance': 'needs_review',
        'summary': '合成摘要',
        'findings': [{
            'code': 'feedback_semantics',
            'severity': 'review',
            'message': '建议人工复核语义问题。',
            'evidence': '合成证据片段',
        }],
        'suggested_comment': '建议人工复核反馈语义。',
    }

    def make_client(self, payload=None, *, responses=None, **kwargs):
        fake = FakeOpenAI(responses or [fake_response(json.dumps(payload or self.VALID, ensure_ascii=False))])
        client = DeepSeekReviewClient(
            api_key='SYNTHETIC_API_KEY',
            base_url='https://api.deepseek.com',
            model='deepseek-v4-flash',
            openai_client=fake,
            **kwargs,
        )
        return client, fake

    def test_request_uses_official_thinking_json_contract_without_sampling_options(self):
        client, fake = self.make_client()

        result = client.review(self.FORM)

        self.assertEqual(result.compliance, 'needs_review')
        request = fake.completions.calls[0]
        self.assertEqual(request['model'], 'deepseek-v4-flash')
        self.assertEqual(request['response_format'], {'type': 'json_object'})
        self.assertEqual(request['reasoning_effort'], 'high')
        self.assertEqual(request['extra_body'], {'thinking': {'type': 'enabled'}})
        for forbidden in ('temperature', 'top_p', 'presence_penalty', 'frequency_penalty'):
            self.assertNotIn(forbidden, request)

    def test_prompt_is_json_only_and_preserves_review_boundary(self):
        client, fake = self.make_client()

        client.review(self.FORM)

        prompt = '\n'.join(
            item['content'] for item in fake.completions.calls[0]['messages']
        )
        self.assertIn('json', prompt.lower())
        self.assertIn('该老师', prompt)
        self.assertIn('重复不等于造假', prompt)
        self.assertIn('确定性课表/规则证据优先', prompt)
        self.assertIn('不做最终审批', prompt)
        self.assertIn('没有客观证据的高风险语言仅建议复核', prompt)
        self.assertIn('仅返回 JSON', prompt)
        self.assertIn('"compliance"', prompt)
        self.assertIn('"suggested_comment"', prompt)

    def test_validated_result_discards_reasoning_content_and_keeps_final_json_only(self):
        client, _ = self.make_client(
            responses=[fake_response(
                json.dumps(self.VALID, ensure_ascii=False),
                reasoning_content='INVENTED_PRIVATE_REASONING_MARKER',
            )]
        )

        result = client.review(self.FORM)

        self.assertEqual(result.model_dump(), self.VALID)
        self.assertNotIn('reasoning_content', result.model_dump())

    def test_fenced_json_is_accepted(self):
        fenced = '```json\n' + json.dumps(self.VALID, ensure_ascii=False) + '\n```'
        client, _ = self.make_client(responses=[fake_response(fenced)])

        self.assertEqual(client.review(self.FORM).compliance, 'needs_review')

    def test_empty_content_is_invalid_without_sensitive_details(self):
        client, _ = self.make_client(responses=[fake_response('')])

        with self.assertRaises(InvalidLLMResponse) as raised:
            client.review(self.FORM)

        self.assertEqual(raised.exception.code, 'empty_content')
        self.assertNotIn('SYNTHETIC_API_KEY', str(raised.exception))
        self.assertNotIn('INVENTED_FEEDBACK_MARKER', str(raised.exception))
        self.assertNotIn('SYNTHETIC_PHONE_0001', str(raised.exception))

    def test_truncated_json_is_invalid(self):
        client, _ = self.make_client(responses=[fake_response('{"compliance":"needs_review"')])

        with self.assertRaises(InvalidLLMResponse) as raised:
            client.review(self.FORM)

        self.assertEqual(raised.exception.code, 'invalid_json')

    def test_wrong_enum_is_invalid(self):
        invalid = dict(self.VALID, compliance='approved')
        client, _ = self.make_client(responses=[fake_response(json.dumps(invalid))])

        with self.assertRaises(InvalidLLMResponse) as raised:
            client.review(self.FORM)

        self.assertEqual(raised.exception.code, 'schema_validation')

    def test_extra_key_is_forbidden(self):
        invalid = dict(self.VALID, unexpected='INVENTED_RAW_PAYLOAD_MARKER')
        client, _ = self.make_client(responses=[fake_response(json.dumps(invalid))])

        with self.assertRaises(InvalidLLMResponse) as raised:
            client.review(self.FORM)

        self.assertEqual(raised.exception.code, 'schema_validation')
        self.assertNotIn('INVENTED_RAW_PAYLOAD_MARKER', str(raised.exception))

    def test_timeout_is_typed_transient(self):
        client, _ = self.make_client(responses=[TimeoutError('SYNTHETIC_TIMEOUT')], max_retries=0)

        with self.assertRaises(TransientLLMError) as raised:
            client.review(self.FORM)

        self.assertEqual(raised.exception.code, 'timeout')
        self.assertNotIn('SYNTHETIC_TIMEOUT', str(raised.exception))

    def test_rate_limit_is_typed_transient(self):
        client, _ = self.make_client(
            responses=[fake_api_error('SYNTHETIC_RATE_LIMIT', 429)],
            max_retries=0,
        )

        with self.assertRaises(TransientLLMError) as raised:
            client.review(self.FORM)

        self.assertEqual(raised.exception.code, 'rate_limited')
        self.assertNotIn('SYNTHETIC_RATE_LIMIT', str(raised.exception))

    def test_server_error_is_typed_transient(self):
        client, _ = self.make_client(
            responses=[fake_api_error('SYNTHETIC_SERVER_ERROR', 503)],
            max_retries=0,
        )

        with self.assertRaises(TransientLLMError) as raised:
            client.review(self.FORM)

        self.assertEqual(raised.exception.code, 'server_error')

    def test_authentication_error_is_typed_permanent(self):
        client, _ = self.make_client(
            responses=[fake_api_error('SYNTHETIC_AUTH_ERROR', 401)],
            max_retries=0,
        )

        with self.assertRaises(PermanentLLMError) as raised:
            client.review(self.FORM)

        self.assertEqual(raised.exception.code, 'authentication_failed')
        self.assertNotIn('SYNTHETIC_API_KEY', str(raised.exception))
        self.assertNotIn('SYNTHETIC_AUTH_ERROR', str(raised.exception))

    def test_transient_retries_then_reports_safe_exhausted_error(self):
        client, fake = self.make_client(
            responses=[
                fake_api_error('SYNTHETIC_503_A', 503),
                fake_api_error('SYNTHETIC_503_B', 503),
                fake_api_error('SYNTHETIC_503_C', 503),
            ],
            max_retries=2,
        )

        with self.assertRaises(TransientLLMError) as raised:
            client.review(self.FORM)

        self.assertEqual(raised.exception.code, 'retry_exhausted')
        self.assertEqual(len(fake.completions.calls), 3)
        self.assertNotIn('SYNTHETIC_503_C', str(raised.exception))

    def test_client_never_logs_api_key_or_complete_form(self):
        client, _ = self.make_client(responses=[fake_response('')])
        logger = logging.getLogger('app.review_automation.llm.client')

        with patch.object(logger, 'error') as log_error:
            with self.assertRaises(InvalidLLMResponse):
                client.review(self.FORM)

        rendered = ' '.join(str(call) for call in log_error.call_args_list)
        self.assertNotIn('SYNTHETIC_API_KEY', rendered)
        self.assertNotIn('INVENTED_FEEDBACK_MARKER', rendered)
        self.assertNotIn('SYNTHETIC_PHONE_0001', rendered)


if __name__ == '__main__':
    unittest.main()
