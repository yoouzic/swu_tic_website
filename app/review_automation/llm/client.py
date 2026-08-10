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
    try:
        choices = response.choices
        message = choices[0].message
        value = message.content
    except (AttributeError, IndexError, KeyError, TypeError) as exc:
        raise InvalidLLMResponse('missing_content') from exc
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
        openai_client: Any = None,
    ):
        self.model = model
        self.reasoning_effort = reasoning_effort
        self.max_retries = max(0, int(max_retries))
        self.prompt_version = PROMPT_VERSION
        self._client = openai_client or OpenAI(
            api_key=api_key,
            base_url=base_url,
            timeout=timeout,
        )

    def review(self, form: Any) -> DeepSeekReviewResponse:
        messages = build_review_messages(form)
        request = {
            'model': self.model,
            'messages': messages,
            'response_format': {'type': 'json_object'},
            'reasoning_effort': self.reasoning_effort,
            'extra_body': {'thinking': {'type': 'enabled'}},
        }
        for attempt in range(self.max_retries + 1):
            try:
                response = self._client.chat.completions.create(**request)
                return self._parse_response(_content(response))
            except (InvalidLLMResponse, PermanentLLMError):
                raise
            except ValidationError as exc:
                logger.warning('deepseek_review_failed code=schema_validation')
                raise InvalidLLMResponse('schema_validation') from exc
            except (json.JSONDecodeError, TypeError, ValueError) as exc:
                logger.warning('deepseek_review_failed code=invalid_json')
                raise InvalidLLMResponse('invalid_json') from exc
            except Exception as exc:
                error_type, code = _provider_error(exc)
                if error_type is PermanentLLMError:
                    logger.warning('deepseek_review_failed code=%s', code)
                    raise error_type(code) from exc
                if attempt >= self.max_retries:
                    final_code = code if self.max_retries == 0 else 'retry_exhausted'
                    logger.warning('deepseek_review_failed code=%s', final_code)
                    raise TransientLLMError(final_code) from exc
                logger.warning('deepseek_review_retry code=%s attempt=%s', code, attempt + 1)
        raise TransientLLMError('retry_exhausted')

    @staticmethod
    def _parse_response(content: str) -> DeepSeekReviewResponse:
        cleaned = _remove_json_fence(content)
        try:
            payload = json.loads(cleaned)
        except json.JSONDecodeError as exc:
            raise InvalidLLMResponse('invalid_json') from exc
        if not isinstance(payload, dict):
            raise InvalidLLMResponse('schema_validation')
        try:
            return DeepSeekReviewResponse.model_validate(payload)
        except ValidationError as exc:
            raise InvalidLLMResponse('schema_validation') from exc


__all__ = [
    'DeepSeekReviewClient',
    'InvalidLLMResponse',
    'PermanentLLMError',
    'SafeLLMError',
    'TransientLLMError',
]
