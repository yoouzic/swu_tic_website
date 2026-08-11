from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date
import json
from pathlib import Path
from typing import Any, Mapping


DEFAULT_RUNTIME_RELATIVE = Path('data/storage/acceptance-2026-08-11')


@dataclass(frozen=True)
class AcceptanceConfig:
    """Frozen arithmetic and safety contract for one acceptance run."""

    seed: int = 260811
    officer_count: int = 1002
    logical_form_count: int = 1500
    per_mode: int = 500
    normal_per_mode: int = 75
    http_attempt_ceiling: int = 1200
    staged_concurrency: tuple[int, ...] = (4, 8, 16)
    staged_size_each: int = 20
    semester: str = '2025-2026-2'
    semester_monday: str = '2026-03-02'
    runtime_root: Path | None = None
    demo_dir: Path | None = None
    school_schedule: Path | None = None

    def __post_init__(self):
        for field in ('runtime_root', 'demo_dir', 'school_schedule'):
            value = getattr(self, field)
            if value is not None and not isinstance(value, Path):
                object.__setattr__(self, field, Path(value))
        object.__setattr__(self, 'staged_concurrency', tuple(self.staged_concurrency))

    @property
    def department_counts(self) -> tuple[int, int, int, int]:
        base, remainder = divmod(self.officer_count, 4)
        return tuple(base + (1 if index < remainder else 0) for index in range(4))

    @property
    def one_form_officer_count(self) -> int:
        return 2 * self.officer_count - self.logical_form_count

    @property
    def two_form_officer_count(self) -> int:
        return self.logical_form_count - self.officer_count

    def validate(self) -> 'AcceptanceConfig':
        """Validate every stop condition before callers create or mutate files."""

        integer_fields = (
            'seed', 'officer_count', 'logical_form_count', 'per_mode',
            'normal_per_mode', 'http_attempt_ceiling', 'staged_size_each',
        )
        for field in integer_fields:
            if not isinstance(getattr(self, field), int) or getattr(self, field) < 1:
                raise ValueError(f'{field} must be a positive integer')
        if self.officer_count != sum(self.department_counts):
            raise ValueError('department counts do not close over officer_count')
        if self.logical_form_count != self.per_mode * 3:
            raise ValueError('logical_form_count must equal three per-mode batches')
        if self.logical_form_count < self.officer_count:
            raise ValueError('logical_form_count cannot be below officer_count')
        if self.normal_per_mode > self.per_mode:
            raise ValueError('normal_per_mode cannot exceed per_mode')
        if self.normal_per_mode * 3 > self.logical_form_count:
            raise ValueError('normal-control arithmetic does not close')
        if self.one_form_officer_count < 0 or self.two_form_officer_count < 0:
            raise ValueError('one-form/two-form officer arithmetic is invalid')
        if self.one_form_officer_count + self.two_form_officer_count != self.officer_count:
            raise ValueError('one-form/two-form officers do not close')
        if self.one_form_officer_count + 2 * self.two_form_officer_count != self.logical_form_count:
            raise ValueError('officer form allocation does not close')
        if not self.staged_concurrency or any(value < 1 for value in self.staged_concurrency):
            raise ValueError('staged_concurrency must contain positive values')
        if tuple(sorted(self.staged_concurrency)) != self.staged_concurrency:
            raise ValueError('staged_concurrency must be ascending')
        try:
            monday = date.fromisoformat(self.semester_monday)
        except (TypeError, ValueError) as exc:
            raise ValueError('semester_monday must be an ISO date') from exc
        if monday.weekday() != 0:
            raise ValueError('semester_monday must be a Monday')
        if not str(self.semester).strip():
            raise ValueError('semester must not be empty')
        for field in ('demo_dir', 'school_schedule'):
            value = getattr(self, field)
            if value is not None and not value.expanduser().resolve().exists():
                raise ValueError(f'{field} does not exist')
        if self.runtime_root is not None:
            runtime = self.runtime_root.expanduser().resolve()
            if runtime.exists() and not runtime.is_dir():
                raise ValueError('runtime_root must be a directory')
        return self

    def output_path(self, relative: str | Path) -> Path:
        """Resolve an output only beneath the configured acceptance runtime."""

        if self.runtime_root is None:
            raise ValueError('runtime_root is required for output paths')
        root = self.runtime_root.expanduser().resolve()
        candidate = (root / Path(relative)).resolve()
        try:
            candidate.relative_to(root)
        except ValueError as exc:
            raise ValueError('acceptance output escaped runtime_root') from exc
        return candidate

    def to_dict(self) -> dict[str, Any]:
        values = asdict(self)
        for field in ('runtime_root', 'demo_dir', 'school_schedule'):
            if values[field] is not None:
                values[field] = str(values[field])
        values['staged_concurrency'] = list(self.staged_concurrency)
        return values

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, sort_keys=True, separators=(',', ':'))

    @classmethod
    def from_mapping(cls, values: Mapping[str, Any]) -> 'AcceptanceConfig':
        accepted = {
            field: values[field]
            for field in cls.__dataclass_fields__
            if field in values
        }
        if 'staged_concurrency' in accepted:
            accepted['staged_concurrency'] = tuple(accepted['staged_concurrency'])
        return cls(**accepted)
