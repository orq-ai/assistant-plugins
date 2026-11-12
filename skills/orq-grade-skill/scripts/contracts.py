"""Version-one, harness-independent grading artifact contracts."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Record(BaseModel):
    """Reject unrecognized fields and accidental type coercion at stage boundaries."""

    model_config = ConfigDict(extra='forbid', strict=True)


class Score(Record):
    label: str
    score: float | None
    reason: str = Field(min_length=1)


class Pointer(Record):
    """Graders give an ATIF step id + exact quote; the baseline adds offsets into that step's text."""

    session_id: str = Field(min_length=1)
    step: int = Field(ge=1)
    start: int | None = Field(default=None, ge=0)
    end: int | None = Field(default=None, gt=0)
    quote: str | None = Field(default=None, min_length=1)

    @model_validator(mode='after')
    def require_location(self) -> Pointer:
        if self.quote is None and (self.start is None or self.end is None):
            raise ValueError('A pointer needs an exact quote or start/end offsets')
        return self


class Instruction(Record):
    path: str = Field(min_length=1)
    start_line: int = Field(ge=1)
    end_line: int = Field(ge=1)


class Issue(Record):
    id: str = Field(min_length=1)
    title: str | None = Field(default=None, min_length=1, description='plain-language name a non-engineer understands')
    claim: str = Field(min_length=1)
    priority: Literal['p0', 'p1', 'p2', 'p3']
    cause: Literal['skill_missing', 'skill_wrong', 'skill_ambiguous', 'trigger', 'ignored_rule']
    skill_hash: str = Field(min_length=64, max_length=64)
    instruction: Instruction
    evidence: list[Pointer] = Field(min_length=1)


class Finding(Record):
    decision: Literal['file', 'dont_file']
    reason: str = Field(min_length=1)
    owning_surface: str = Field(min_length=1)
    behavioral_rule: str = Field(min_length=1)
    issue: Issue | None = None

    @model_validator(mode='after')
    def require_filed_issue(self) -> Finding:
        if self.decision == 'file' and self.issue is None:
            raise ValueError('A filed finding needs an issue')
        return self


class SessionGrade(Record):
    session_id: str = Field(min_length=1)
    efficiency: Score
    quality: Score
    usage: Literal['loaded', 'late', 'missed', 'not_applicable', 'unknown']
    usage_reason: str = Field(min_length=1)
    skill_compliance: Score | None = None


class GradeBatch(Record):
    schema_version: Literal[1]
    batch_id: str = Field(min_length=1)
    sessions: list[SessionGrade] = Field(min_length=1)
    findings: list[Finding]


class ScoreSummary(Record):
    mean: float | None
    scored: int = Field(ge=0)
    unavailable: int = Field(ge=0)


class GradedSession(SessionGrade):
    grading_failed: bool


class Baseline(Record):
    schema_version: Literal[1]
    skill: str
    skill_hash: str
    passes: int = Field(ge=1)
    batch_size: int = Field(ge=1)
    source_hashes: dict[str, str]
    scores: dict[str, dict[str, ScoreSummary]]
    statistics: dict[str, dict]
    session_grades: dict[str, GradedSession]
    session_spread: dict[str, dict[str, float | None]] = Field(description='max-min score across passes')
    findings: list[Finding]
    filed_issues: list[Issue]
    pass_filed_ids: list[list[str]] = Field(description='issue ids each pass filed, before the majority vote')
    agreement: dict[str, int] = Field(description='per target issue id, passes that filed it')
    top_issue_ids: list[str]
    limitations: list[str]
    weekly_average: float | None


if __name__ == '__main__':
    import json
    from pathlib import Path

    schemas = {
        'grade-batch': GradeBatch,
        'baseline': Baseline,
    }
    for name, model in schemas.items():
        path = Path(__file__).parents[1] / f'contracts/{name}.schema.json'
        path.write_text(json.dumps(model.model_json_schema(), indent=2) + '\n')
