"""Pure contracts for the adaptive listening-assistant guide."""

from dataclasses import dataclass
from typing import Any, Mapping

from .listening_assistant_contracts import Candidate


MAX_GUIDED_QUESTIONS = 4
MAX_GUIDED_FACT_LENGTH = 120
MAX_GUIDED_CANDIDATES = 20
ALLOWED_GUIDED_STAGES = frozenset({'question', 'candidate', 'confirm', 'manual', 'done'})
_ALLOWED_GUIDED_FACTS = frozenset({'date', 'teacher', 'room', 'period', 'student_grade_class'})
_ALLOWED_GUIDED_QUESTION_KINDS = _ALLOWED_GUIDED_FACTS | {'memory'}


def _require_text(value: Any, field_name: str, *, max_length: int | None = None) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f'{field_name} must be a non-empty string')
    normalized = value.strip()
    if max_length is not None and len(normalized) > max_length:
        raise ValueError(f'{field_name} exceeds maximum length')
    return normalized


def _require_sequence(value: Any, field_name: str) -> tuple[Any, ...]:
    if isinstance(value, (str, bytes, Mapping)):
        raise ValueError(f'{field_name} must be a sequence')
    try:
        return tuple(value)
    except TypeError as error:
        raise ValueError(f'{field_name} must be a sequence') from error


def normalize_known_facts(value: Mapping[str, str] | None) -> dict[str, str]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise ValueError('known_facts must be a mapping')
    normalized: dict[str, str] = {}
    for key, fact in value.items():
        if key not in _ALLOWED_GUIDED_FACTS:
            raise ValueError('known_facts contains an unknown key')
        normalized[key] = _require_text(fact, f'known_facts[{key}]', max_length=MAX_GUIDED_FACT_LENGTH)
    return normalized


def normalize_candidate_ids(value: Any) -> tuple[str, ...]:
    values = _require_sequence(value, 'candidate_ids')
    if len(values) > MAX_GUIDED_CANDIDATES:
        raise ValueError('candidate_ids exceeds maximum count')
    normalized: list[str] = []
    for candidate_id in values:
        item = _require_text(candidate_id, 'candidate_id', max_length=MAX_GUIDED_FACT_LENGTH)
        if item in normalized:
            raise ValueError('candidate_ids must not contain duplicates')
        normalized.append(item)
    return tuple(normalized)


def normalize_question_kinds(value: Any) -> tuple[str, ...]:
    values = _require_sequence(value, 'asked_question_kinds')
    normalized: list[str] = []
    for kind in values:
        item = _require_text(kind, 'question kind')
        if item not in _ALLOWED_GUIDED_QUESTION_KINDS:
            raise ValueError('question kind is not allowed')
        if item in normalized:
            raise ValueError('asked_question_kinds must not contain duplicates')
        normalized.append(item)
    return tuple(normalized)


def normalize_question_count(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError('question_count must be an integer')
    if not 0 <= value <= MAX_GUIDED_QUESTIONS:
        raise ValueError('question_count is outside the allowed budget')
    return value


def normalize_stage(value: Any) -> str:
    stage = _require_text(value, 'stage')
    if stage not in ALLOWED_GUIDED_STAGES:
        raise ValueError('stage is not allowed')
    return stage


@dataclass(frozen=True)
class GuidedOption:
    code: str
    label: str
    value: str
    candidate_count: int | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, 'code', _require_text(self.code, 'code', max_length=MAX_GUIDED_FACT_LENGTH))
        object.__setattr__(self, 'label', _require_text(self.label, 'label', max_length=MAX_GUIDED_FACT_LENGTH))
        object.__setattr__(self, 'value', _require_text(self.value, 'value', max_length=MAX_GUIDED_FACT_LENGTH))
        if self.candidate_count is not None and (
            isinstance(self.candidate_count, bool)
            or not isinstance(self.candidate_count, int)
            or self.candidate_count < 0
        ):
            raise ValueError('candidate_count must be a non-negative integer or None')

    def to_public_dict(self) -> dict[str, Any]:
        return {
            'code': self.code,
            'label': self.label,
            'value': self.value,
            'candidate_count': self.candidate_count,
        }


@dataclass(frozen=True)
class GuidedQuestion:
    kind: str
    prompt: str
    options: tuple[GuidedOption, ...]
    allow_custom: bool = True
    custom_label: str = 'D. 我自己填写'

    def __post_init__(self) -> None:
        kind = _require_text(self.kind, 'kind')
        if kind not in _ALLOWED_GUIDED_QUESTION_KINDS:
            raise ValueError('kind is not allowed')
        object.__setattr__(self, 'kind', kind)
        object.__setattr__(self, 'prompt', _require_text(self.prompt, 'prompt', max_length=MAX_GUIDED_FACT_LENGTH))
        options = _require_sequence(self.options, 'options')
        if len(options) > 3 or any(not isinstance(option, GuidedOption) for option in options):
            raise ValueError('options must contain at most three GuidedOption values')
        object.__setattr__(self, 'options', options)
        if not isinstance(self.allow_custom, bool):
            raise ValueError('allow_custom must be a bool')
        object.__setattr__(
            self,
            'custom_label',
            _require_text(self.custom_label, 'custom_label', max_length=MAX_GUIDED_FACT_LENGTH),
        )

    @classmethod
    def initial_memory_question(cls) -> 'GuidedQuestion':
        return cls(
            kind='memory',
            prompt='你还记得哪类信息？',
            options=(
                GuidedOption('A', '听课日期', 'date'),
                GuidedOption('B', '授课教师', 'teacher'),
                GuidedOption('C', '教室', 'room'),
            ),
        )

    def to_public_dict(self) -> dict[str, Any]:
        return {
            'kind': self.kind,
            'prompt': self.prompt,
            'options': [option.to_public_dict() for option in self.options],
            'allow_custom': self.allow_custom,
            'custom_label': self.custom_label,
        }


@dataclass(frozen=True)
class GuidedAssistantState:
    known_facts: Mapping[str, str]
    candidate_ids: tuple[str, ...]
    asked_question_kinds: tuple[str, ...]
    question_count: int
    stage: str

    def __post_init__(self) -> None:
        object.__setattr__(self, 'known_facts', normalize_known_facts(self.known_facts))
        object.__setattr__(self, 'candidate_ids', normalize_candidate_ids(self.candidate_ids))
        object.__setattr__(self, 'asked_question_kinds', normalize_question_kinds(self.asked_question_kinds))
        object.__setattr__(self, 'question_count', normalize_question_count(self.question_count))
        object.__setattr__(self, 'stage', normalize_stage(self.stage))

    @property
    def is_valid(self) -> bool:
        return True

    def to_public_dict(self) -> dict[str, Any]:
        return {
            'known_facts': dict(self.known_facts),
            'candidate_ids': list(self.candidate_ids),
            'asked_question_kinds': list(self.asked_question_kinds),
            'question_count': self.question_count,
            'stage': self.stage,
        }

    @classmethod
    def from_public_dict(cls, value: Mapping[str, Any]) -> 'GuidedAssistantState':
        if not isinstance(value, Mapping):
            raise ValueError('state must be a mapping')
        expected = {'known_facts', 'candidate_ids', 'asked_question_kinds', 'question_count', 'stage'}
        if set(value) != expected:
            raise ValueError('state contains unknown or missing keys')
        return cls(**value)


@dataclass(frozen=True)
class GuidedResult:
    state: GuidedAssistantState
    question: GuidedQuestion | None
    candidates: tuple[Candidate, ...]
    needs_confirmation: bool

    def __post_init__(self) -> None:
        if not isinstance(self.state, GuidedAssistantState):
            raise ValueError('state must be a GuidedAssistantState')
        if self.question is not None and not isinstance(self.question, GuidedQuestion):
            raise ValueError('question must be a GuidedQuestion or None')
        candidates = _require_sequence(self.candidates, 'candidates')
        if len(candidates) > MAX_GUIDED_CANDIDATES or any(not isinstance(candidate, Candidate) for candidate in candidates):
            raise ValueError('candidates must contain at most twenty Candidate values')
        object.__setattr__(self, 'candidates', candidates)
        if not isinstance(self.needs_confirmation, bool):
            raise ValueError('needs_confirmation must be a bool')

    def to_public_dict(self) -> dict[str, Any]:
        return {
            'state': self.state.to_public_dict(),
            'question': self.question.to_public_dict() if self.question is not None else None,
            'candidates': [candidate.to_public_dict() for candidate in self.candidates],
            'needs_confirmation': self.needs_confirmation,
        }


__all__ = [
    'ALLOWED_GUIDED_STAGES',
    'MAX_GUIDED_CANDIDATES',
    'MAX_GUIDED_FACT_LENGTH',
    'MAX_GUIDED_QUESTIONS',
    'GuidedAssistantState',
    'GuidedOption',
    'GuidedQuestion',
    'GuidedResult',
    'normalize_candidate_ids',
    'normalize_known_facts',
    'normalize_question_count',
    'normalize_question_kinds',
    'normalize_stage',
]
