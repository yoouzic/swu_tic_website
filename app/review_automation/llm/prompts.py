"""Versioned prompt construction for semantic review suggestions."""

from __future__ import annotations

import dataclasses
import json
from enum import Enum
from typing import Any, Mapping


PROMPT_VERSION = '2026-08-10-v2-context'


def _jsonable(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if dataclasses.is_dataclass(value):
        return _jsonable(dataclasses.asdict(value))
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_jsonable(item) for item in value]
    if hasattr(value, 'isoformat'):
        try:
            return value.isoformat()
        except (AttributeError, TypeError, ValueError):
            pass
    if hasattr(value, '__dict__'):
        return {
            str(key): _jsonable(item)
            for key, item in vars(value).items()
            if not key.startswith('_') and key != '_sa_instance_state'
        }
    return value


def build_review_messages(review_context: Any) -> list[dict[str, str]]:
    """Build a complete-form plus evidence-context prompt without retaining it."""
    context_json = json.dumps(
        _jsonable(review_context),
        ensure_ascii=False,
        sort_keys=True,
        separators=(',', ':'),
        default=str,
    )
    example = {
        'compliance': 'needs_review',
        'summary': '简短中文摘要',
        'findings': [{
            'code': 'semantic_concern',
            'severity': 'review',
            'message': '需要人工复核的语义问题',
            'evidence': '对应的客观文本证据',
        }],
        'suggested_comment': '可编辑的人审核意见草稿',
    }
    system = (
        '你是 SWU TIC 表单语义复核助手。确定性课表/规则证据优先；模型只提供语义问题，'
        '不做最终审批，不得直接通过或驳回表单。该老师是必需前缀；重复不等于造假。'
        '没有客观证据的高风险语言仅建议复核。请只返回 JSON，不要 markdown、解释、推理过程或其他文本。'
        '输出必须是合法 json 对象，并严格符合下面的期望对象示例：'
        + json.dumps(example, ensure_ascii=False, separators=(',', ':'))
    )
    user = (
        '请仅根据以下完整表单和相关证据上下文提出语义层面的合规性与真实性复核建议。'
        '输入包含完整表单 form、规范化课表对比 schedule_comparison、确定性规则证据 rule_evidence、'
        '同一信息员历史摘要和冻结依赖；请优先考虑这些证据上下文。\n'
        '不要替代确定性规则，也不要作最终通过/驳回决定。仅返回 JSON。\n'
        '完整表单和证据上下文 JSON：\n'
        + context_json
    )
    return [
        {'role': 'system', 'content': system},
        {'role': 'user', 'content': user},
    ]


__all__ = ['PROMPT_VERSION', 'build_review_messages']
