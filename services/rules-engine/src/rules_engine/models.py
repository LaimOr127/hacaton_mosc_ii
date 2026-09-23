from __future__ import annotations

from typing import Any, Literal, Optional, Union

from pydantic import BaseModel, Field


EntityType = Literal["DOOR", "WINDOW", "WALL"]
Stage = Literal["PROJECT", "WORKING", "AS_BUILT"]
FindingType = Literal[
    "MISSING_ENTITY",
    "EXTRA_ENTITY",
    "VALUE_CHANGED",
    "COUNT_CHANGED",
    "MATERIAL_CHANGED",
    "AMBIGUOUS_MATCH",
]
Severity = Literal["INFO", "LOW", "MEDIUM", "HIGH"]


class BBox(BaseModel):
    x0: float
    y0: float
    x1: float
    y1: float
    coordinate_system: Literal["normalized_top_left"] = "normalized_top_left"


class TextBlock(BaseModel):
    id: Optional[str] = None
    order: int = Field(ge=0)
    text: str = Field(min_length=1)
    bbox: BBox
    confidence: float = Field(default=1.0, ge=0, le=1)
    source: Literal["TEXT_LAYER", "OCR"] = "TEXT_LAYER"


class PageText(BaseModel):
    page_id: Optional[str] = None
    page_number: int = Field(ge=1)
    blocks: list[TextBlock] = Field(default_factory=list)


class ExtractEntitiesRequest(BaseModel):
    document_id: str = Field(min_length=1)
    stage: Optional[Stage] = None
    pages: list[PageText] = Field(min_length=1)


class Evidence(BaseModel):
    document_id: str
    page_id: Optional[str] = None
    page_number: int
    text_block_id: Optional[str] = None
    bbox: BBox
    quote: str
    extraction_method: Literal["RULE"] = "RULE"
    confidence: float = Field(ge=0, le=1)


class AttributeValue(BaseModel):
    raw: str
    normalized: Union[int, str]
    unit: Optional[str] = None
    evidence: Evidence


class Entity(BaseModel):
    id: str
    document_id: str
    stage: Optional[Stage] = None
    entity_type: EntityType
    raw_code: str
    normalized_code: str
    canonical_key: str
    attributes: dict[str, AttributeValue] = Field(default_factory=dict)
    evidence: Evidence
    normalizer_version: str = "rules-engine-normalizer-v1"
    detector_version: str = "rules-engine-v1"
    confidence: float = Field(ge=0, le=1)


class ExtractEntitiesResponse(BaseModel):
    entities: list[Entity]
    detector_version: str = "rules-engine-v1"
    normalizer_version: str = "rules-engine-normalizer-v1"


class CompareRequest(BaseModel):
    inspection_id: Optional[str] = None
    expected_stage: Stage = "PROJECT"
    actual_stage: Stage = "AS_BUILT"
    expected_entities: list[Entity] = Field(default_factory=list)
    actual_entities: list[Entity] = Field(default_factory=list)


class ScoreBreakdown(BaseModel):
    code: float
    type: float
    attributes: float = 0.0


class MatchResult(BaseModel):
    expected_entity_id: Optional[str] = None
    actual_entity_id: Optional[str] = None
    status: Literal["MATCHED", "AMBIGUOUS", "UNMATCHED"]
    method: Literal["EXACT_CODE_TYPE", "UNMATCHED"]
    confidence: float = Field(ge=0, le=1)
    score_breakdown: ScoreBreakdown


class FindingEvidence(BaseModel):
    side: Literal["EXPECTED", "ACTUAL", "CONTEXT"]
    entity_id: str
    document_id: str
    page_id: Optional[str] = None
    page_number: int
    text_block_id: Optional[str] = None
    bbox: BBox
    quote: str


class Finding(BaseModel):
    finding_type: FindingType
    entity_type: EntityType
    canonical_key: str
    field_name: Optional[str] = None
    expected_value: Any = None
    actual_value: Any = None
    absolute_delta: Optional[int] = None
    relative_delta: Optional[float] = None
    severity: Severity
    status: Literal["OPEN", "NEEDS_REVIEW"] = "OPEN"
    title: str
    description: str
    rule_id: str
    detector_version: str = "rules-engine-v1"
    dedup_key: str
    evidence: list[FindingEvidence] = Field(min_length=1)


class CompareResponse(BaseModel):
    matches: list[MatchResult]
    findings: list[Finding]
    detector_version: str = "rules-engine-v1"
