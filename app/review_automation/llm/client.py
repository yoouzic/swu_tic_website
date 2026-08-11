"""Small, validated DeepSeek OpenAI-compatible client."""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from openai import OpenAI
from pydantic import ValidationError

from .prompts import PROMPT_VERSION, build_review_messages
from .schemas import DeepSeekReviewResponse


logger = logging.getLogger(__name__)
_ALLOWED_REASONING_EFFORTS = {'high', 'max'}
_MAX_OUTPUT_TOKENS = 8192


class SafeLLMError(Exception):
    """Base error whose public message contains only a safe classification."""

    kind = 'llm'

    def __init__(self, code: str):
        self.code = code
        super().__init__(f'{self.kind}:{code}')


class TransientLLMError(SafeLLMError):
    kind = 'transient'


class PermanentLLMError(SafeLLMError):
    kind = 'permanent'


class InvalidLLMResponse(SafeLLMError):
    kind = 'invalid'


def _status_code(error: BaseException) -> int | None:
    value = getattr(error, 'status_code', None)
    return value if isinstance(value, int) else None


def _provider_error(error: BaseException) -> tuple[type[SafeLLMError], str]:
    status = _status_code(error)
    name = type(error).__name__.lower()
    if isinstance(error, (TimeoutError,)) or 'timeout' in name:
        return TransientLLMError, 'timeout'
    if status == 429 or 'ratelimit' in name or 'rate_limit' in name:
        return TransientLLMError, 'rate_limited'
    if status is not None and 500 <= status <= 599:
        return TransientLLMError, 'server_error'
    if 'connection' in name or 'network' in name:
        return TransientLLMError, 'connection_error'
    if status in {400, 401, 403, 404, 422}:
        return PermanentLLMError, 'authentication_failed' if status == 401 else 'provider_rejected'
    return PermanentLLMError, 'provider_error'


def _content(response: Any) -> str:
    missing_content = False
    try:
        choices = response.choices
        choice = choices[0]
        message = choice.message
        value = message.content
        finish_reason = getattr(choice, 'finish_reason', None)
    except (AttributeError, IndexError, KeyError, TypeError):
        missing_content = True
    if missing_content:
        raise InvalidLLMResponse('missing_content')
    if finish_reason == 'length':
        raise InvalidLLMResponse('truncated_output')
    if not isinstance(value, str) or not value.strip():
        raise InvalidLLMResponse('empty_content')
    return value.strip()


def _remove_json_fence(content: str) -> str:
    if not content.startswith('```'):
        return content
    match = re.fullmatch(r'```(?:json)?\s*\n?(.*?)\n?```', content, flags=re.DOTALL | re.IGNORECASE)
    if not match:
        raise InvalidLLMResponse('invalid_json_fence')
    return match.group(1).strip()


class DeepSeekReviewClient:
    """Call DeepSeek and return only a Pydantic-validated final object."""

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str = 'https://api.deepseek.com',
        model: str = 'deepseek-v4-flash',
        timeout: float = 60,
        reasoning_effort: str = 'high',
        max_retries: int = 3,
        max_tokens: int = 2048,
        openai_client: Any = None,
    ):
        if reasoning_effort not in _ALLOWED_REASONING_EFFORTS:
            raise ValueError('reasoning_effort must be high or max')
        if isinstance(max_tokens, bool) or not isinstance(max_tokens, int):
            raise ValueError('max_tokens must be an integer')
        if not 1 <= max_tokens <= _MAX_OUTPUT_TOKENS:
            raise ValueError('max_tokens is outside the allowed bound')
        self.model = model
        self.reasoning_effort = reasoning_effort
        self.max_retries = max(0, int(max_retries))
        self.max_tokens = max_tokens
        self.prompt_version = PROMPT_VERSION
        self.last_attempt_count = 0
        self._client = openai_client or OpenAI(
            api_key=api_key,
            base_url=base_url,
            timeout=timeout,
        )

    def review(self, form: Any) -> DeepSeekReviewResponse:
        self.last_attempt_count = 0
        messages = build_review_messages(form)
        request = {
            'model': self.model,
            'messages': messages,
            'response_format': {'type': 'json_object'},
            'reasoning_effort': self.reasoning_effort,
            'extra_body': {'thinking': {'type': 'enabled'}},
            'max_tokens': self.max_tokens,
        }
        for attempt in range(self.max_retries + 1):
            provider_failure = None
            try:
                self.last_attempt_count += 1
                response = self._client.chat.completions.create(**request)
            except Exception as exc:
                provider_failure = _provider_error(exc)
            if provider_failure is not None:
                error_type, code = provider_failure
                if error_type is PermanentLLMError:
                    logger.warning('deepseek_review_failed code=%s', code)
                    raise error_type(code)
                if attempt >= self.max_retries:
                    final_code = code if self.max_retries == 0 else 'retry_exhausted'
                    logger.warning('deepseek_review_failed code=%s', final_code)
                    raise TransientLLMError(final_code)
                logger.warning('deepseek_review_retry code=%s attempt=%s', code, attempt + 1)
                continue
            return self._parse_response(_content(response))
        raise TransientLLMError('retry_exhausted')

    @staticmethod
    def _parse_response(content: str) -> DeepSeekReviewResponse:
        cleaned = _remove_json_fence(content)
        invalid_json = False
        try:
            payload = json.loads(cleaned)
        except json.JSONDecodeError:
            invalid_json = True
        if invalid_json:
            raise InvalidLLMResponse('invalid_json')
        if not isinstance(payload, dict):
            raise InvalidLLMResponse('schema_validation')
        validation_error = False
        try:
            result = DeepSeekReviewResponse.model_validate(payload)
        except ValidationError:
            validation_error = True
        if validation_error:
            raise InvalidLLMResponse('schema_validation')
        return result


__all__ = [
    'DeepSeekReviewClient',
    'InvalidLLMResponse',
    'PermanentLLMError',
    'SafeLLMError',
    'TransientLLMError',
]
