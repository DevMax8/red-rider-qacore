"""Validated data contracts and deterministic CQA scoring.

The model returns criterion values and evidence. Python, rather than the model,
calculates totals and validates the organizer's submission format.
"""
from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP
from typing import Annotated, Literal

from pydantic import (
    AfterValidator, BaseModel, ConfigDict, Field, StrictBool, StrictFloat,
    StrictInt, StringConstraints, field_validator, model_serializer, model_validator,
)


def _nonblank(value: str) -> str:
    if not value.strip():
        raise ValueError("Text must not be blank")
    return value


# Preserve source identifiers and time strings exactly, including their whitespace.
NonEmptyText = Annotated[str, StringConstraints(min_length=1), AfterValidator(_nonblank)]
NumericValue = StrictInt | StrictFloat


class ContractModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid", allow_inf_nan=False, validate_assignment=True,
        revalidate_instances="always",
    )


class TranscriptTurn(ContractModel):
    timestamp: str
    speaker: Literal["client", "operator", "bot", "system"]
    text: str


class Conversation(ContractModel):
    conversation_id: NonEmptyText
    channel: Literal["agent chat", "voice call"]
    language: Literal["RU", "KZ", "EN"]
    transcript: list[TranscriptTurn] = Field(min_length=1)
    audio_url: str | None = None


class CriterionConfig(ContractModel):
    criterion_id: NonEmptyText
    label: NonEmptyText
    description: NonEmptyText
    weight: float = Field(ge=0, strict=True)
    answer_type: Literal["boolean", "scale"]
    allow_na: StrictBool = False
    scale: list[NumericValue] = Field(default_factory=list)

    @model_validator(mode="after")
    def valid_scale(self) -> "CriterionConfig":
        if self.answer_type == "boolean" and self.scale:
            raise ValueError("Boolean criteria cannot define a numeric scale")
        if self.answer_type == "scale":
            if len(self.scale) < 2 or self.scale != sorted(set(self.scale)):
                raise ValueError("Numeric scales need at least two unique increasing steps")
            if self.scale[0] < 0 or self.scale[-1] <= 0:
                raise ValueError("Scale steps must be nonnegative with a positive maximum")
        if self.criterion_id == "critical" and (
            self.answer_type != "boolean" or self.weight != 0 or self.allow_na
        ):
            raise ValueError("critical must be an unweighted boolean without N/A")
        return self


class Checklist(ContractModel):
    id: NonEmptyText
    name: NonEmptyText
    version: StrictInt = Field(default=1, ge=1)
    criteria: list[CriterionConfig] = Field(min_length=1)

    @model_validator(mode="after")
    def unique_ids(self) -> "Checklist":
        ids = [criterion.criterion_id for criterion in self.criteria]
        if len(ids) != len(set(ids)):
            raise ValueError("Checklist criterion IDs must be unique")
        return self


class CriterionResult(ContractModel):
    criterion_id: NonEmptyText
    value: StrictBool | NumericValue | None
    rationale: NonEmptyText
    evidence_turns: list[StrictInt] = Field(min_length=1)

    @field_validator("evidence_turns")
    @classmethod
    def valid_evidence(cls, values: list[int]) -> list[int]:
        if any(value < 1 for value in values):
            raise ValueError("Evidence uses 1-based original transcript turn numbers")
        if len(values) != len(set(values)):
            raise ValueError("Evidence turn numbers must not repeat")
        return values


class Score(ContractModel):
    final_score: float | None = Field(ge=0, le=100, strict=True)
    critical_flag: StrictBool


class EvaluationResult(ContractModel):
    conversation_id: NonEmptyText
    status: Literal["ok", "error"]
    criteria: list[CriterionResult] = Field(default_factory=list)
    score: Score | None = None
    error: NonEmptyText | None = None

    @model_validator(mode="after")
    def coherent_status(self) -> "EvaluationResult":
        if self.status == "ok":
            if not self.criteria or self.score is None or self.error is not None:
                raise ValueError("Successful results need criteria and score, without an error")
        elif self.criteria or self.score is not None or not self.error:
            raise ValueError("Errors need empty criteria, null score, and an error reason")
        return self

    @model_serializer(mode="wrap")
    def status_fields(self, handler):
        result = handler(self)
        if self.status == "ok":
            result.pop("error", None)
        return result


class ModelInfo(ContractModel):
    mode: Literal["local", "external_api", "hybrid"]
    name: NonEmptyText


class ExportBundle(ContractModel):
    schema_version: Literal["1.0"] = "1.0"
    team_name: NonEmptyText
    model: ModelInfo
    results: list[EvaluationResult] = Field(min_length=1)


def _validate_values(
    checklist: Checklist, criteria: list[CriterionResult], turn_count: int | None = None,
) -> dict[str, CriterionResult]:
    ids = [criterion.criterion_id for criterion in criteria]
    expected = {criterion.criterion_id for criterion in checklist.criteria}
    if len(ids) != len(set(ids)) or set(ids) != expected:
        raise ValueError("Provide every active criterion exactly once, with no extra IDs")
    by_id = {criterion.criterion_id: criterion for criterion in criteria}
    for config in checklist.criteria:
        result = by_id[config.criterion_id]
        if turn_count is not None and any(index > turn_count for index in result.evidence_turns):
            raise ValueError(f"Evidence outside transcript for {config.criterion_id}")
        value = result.value
        if value is None:
            if not config.allow_na:
                raise ValueError(f"N/A is not allowed for {config.criterion_id}")
        elif config.answer_type == "boolean":
            if type(value) is not bool:
                raise ValueError(f"{config.criterion_id} requires true or false")
        elif type(value) not in (int, float) or value not in config.scale:
            raise ValueError(f"{config.criterion_id} requires a configured numeric scale step")
    return by_id


def _calculate_score(checklist: Checklist, by_id: dict[str, CriterionResult]) -> Score:
    earned, applicable_weight = Decimal("0"), Decimal("0")
    for config in checklist.criteria:
        value = by_id[config.criterion_id].value
        if value is None or config.weight == 0:
            continue
        weight = Decimal(str(config.weight))
        applicable_weight += weight
        fraction = Decimal(int(value)) if config.answer_type == "boolean" else (
            Decimal(str(value)) / Decimal(str(config.scale[-1]))
        )
        earned += weight * fraction
    total = None
    if applicable_weight:
        total = float((earned / applicable_weight * Decimal("100")).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP,
        ))
    critical = by_id.get("critical")
    return Score(final_score=total, critical_flag=bool(critical and critical.value is True))


def validate_and_score(
    conversation: Conversation, checklist: Checklist, criteria: list[CriterionResult],
) -> EvaluationResult:
    """Validate all values/evidence and calculate a deterministic weighted total."""
    conversation = Conversation.model_validate(conversation)
    checklist = Checklist.model_validate(checklist)
    parsed = [CriterionResult.model_validate(criterion) for criterion in criteria]
    by_id = _validate_values(checklist, parsed, len(conversation.transcript))
    return EvaluationResult(
        conversation_id=conversation.conversation_id, status="ok",
        criteria=[by_id[config.criterion_id] for config in checklist.criteria],
        score=_calculate_score(checklist, by_id),
    )


def build_export(
    team_name: str, model: ModelInfo, results: list[EvaluationResult],
    expected_ids: list[str], subset: Literal["dev", "prod"],
    checklist: Checklist | None = None,
) -> ExportBundle:
    """Build the official 65-record DEV or 10-record control submission.

    Error records count toward completeness. Configurable demonstration checklists
    remain usable in the app, but a nonstandard schema cannot silently become an
    official eight-criterion submission.
    """
    from app.defaults import default_checklist

    if subset not in {"dev", "prod"}:
        raise ValueError("subset must be dev or prod")
    count = 65 if subset == "dev" else 10
    if len(expected_ids) != count or len(set(expected_ids)) != count:
        raise ValueError(f"{subset} requires exactly {count} unique source conversation IDs")
    parsed = [EvaluationResult.model_validate(result) for result in results]
    ids = [result.conversation_id for result in parsed]
    if len(ids) != len(set(ids)) or set(ids) != set(expected_ids):
        raise ValueError("Export must contain every source conversation ID exactly once")
    official = default_checklist()
    active = Checklist.model_validate(checklist) if checklist is not None else official
    baseline = {config.criterion_id: config for config in official.criteria}
    configured = {config.criterion_id: config for config in active.criteria}
    if configured.keys() != baseline.keys():
        raise ValueError("Official submission requires the original eight criterion IDs")
    for identifier, config in configured.items():
        original = baseline[identifier]
        if (config.answer_type, config.allow_na, config.scale, config.weight) != (
            original.answer_type, original.allow_na, original.scale, original.weight,
        ):
            raise ValueError("Official submission requires original scales, weights, and N/A rules")
    for result in parsed:
        if result.status == "error":
            continue
        by_id = _validate_values(official, result.criteria)
        if result.score != _calculate_score(official, by_id):
            raise ValueError(f"Score is inconsistent with criteria for {result.conversation_id}")
    by_id = {result.conversation_id: result for result in parsed}
    return ExportBundle(team_name=team_name, model=model,
                        results=[by_id[identifier] for identifier in expected_ids])
