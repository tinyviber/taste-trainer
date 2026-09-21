"""Small validation/normalisation schemas for model-produced machine data."""
from typing import Any

from pydantic import BaseModel, Field

try:  # Keep local/dev environments on Pydantic 1 usable as well.
    from pydantic import ConfigDict, field_validator
    PYDANTIC_V2 = True
except ImportError:  # pragma: no cover - exercised only by Pydantic 1
    from pydantic import validator as field_validator
    ConfigDict = None
    PYDANTIC_V2 = False

from .renders import DIMS


class _Model(BaseModel):
    if PYDANTIC_V2:
        model_config = ConfigDict(extra="ignore")
    else:
        class Config:
            extra = "ignore"


class ScoreItem(_Model):

    dim: str
    score: int = Field(ge=0, le=10)
    evidence: str = ""


class RewriteRow(_Model):

    time: str = ""
    visual: str = ""
    function: str = ""
    intent: str = ""


class WeakestItem(_Model):

    dim: str
    original: str = ""
    rewrite_rows: list[RewriteRow] = Field(default_factory=list)
    gap: str = ""


class Revision(_Model):

    rows: list[RewriteRow] = Field(default_factory=list)
    changes: list[dict[str, str]] = Field(default_factory=list)


class GradeResult(_Model):

    one_liner: str = ""
    total: int = 0
    scores: list[ScoreItem]
    strengths: list[str] = Field(default_factory=list)
    weakest: list[WeakestItem]
    next_focus: list[str] = Field(default_factory=list)
    revision: Revision = Field(default_factory=Revision)

    @field_validator("scores")
    @classmethod
    def scores_have_expected_dimensions(cls, value: list[ScoreItem]) -> list[ScoreItem]:
        if len(value) != len(DIMS):
            raise ValueError(f"scores must contain exactly {len(DIMS)} dimensions")
        by_dim = {item.dim: item for item in value}
        if set(by_dim) != set(DIMS):
            raise ValueError("scores dimensions do not match RUBRIC")
        # Keep the canonical rubric order even if the model shuffled the array.
        return [by_dim[dim] for dim in DIMS]

    @field_validator("weakest")
    @classmethod
    def has_two_weakest(cls, value: list[WeakestItem]) -> list[WeakestItem]:
        if len(value) != 2:
            raise ValueError("weakest must contain exactly two dimensions")
        return value


class MicroFeedbackResult(_Model):
    """A short, targeted response to the learner's micro-v2 rewrite."""

    estimated_score: int = Field(ge=0, le=10)
    fixed: str
    remaining_gap: str
    next_step: str

    @field_validator("fixed", "remaining_gap", "next_step")
    @classmethod
    def feedback_must_be_specific(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("micro feedback fields must not be empty")
        return value.strip()


def normalize_grade(data: dict[str, Any]) -> dict:
    """Validate a grade and compute the authoritative total locally."""
    result = (GradeResult.model_validate(data) if PYDANTIC_V2
              else GradeResult.parse_obj(data))
    output = (result.model_dump(mode="json") if PYDANTIC_V2
              else result.dict())
    output["total"] = sum(item["score"] for item in output["scores"])
    output["schema_version"] = 1
    return output


def normalize_micro_feedback(data: dict[str, Any], target_dim: str,
                             v1_score: int) -> dict:
    """Validate model feedback while keeping target metadata server-owned."""
    result = (MicroFeedbackResult.model_validate(data) if PYDANTIC_V2
              else MicroFeedbackResult.parse_obj(data))
    output = (result.model_dump(mode="json") if PYDANTIC_V2
              else result.dict())
    output.update({
        "target_dim": target_dim,
        "v1_score": max(0, min(10, int(v1_score))),
        "schema_version": 1,
    })
    return output
