from __future__ import annotations

import re
import unicodedata
import uuid
from decimal import Decimal, InvalidOperation
from typing import Iterable, Optional, Union

from .models import (
    AttributeValue,
    CompareResponse,
    Entity,
    Evidence,
    ExtractEntitiesResponse,
    ExtractEntitiesRequest,
    Finding,
    FindingEvidence,
    MatchResult,
    ScoreBreakdown,
    TextBlock,
)

DETECTOR_VERSION = "rules-engine-v1"
NORMALIZER_VERSION = "rules-engine-normalizer-v1"

CODE_ALIASES = {
    "D": "Д",
    "Д": "Д",
    "OK": "ОК",
    "ОК": "ОК",
    "C": "С",
    "S": "С",
    "С": "С",
}

CODE_RE = re.compile(r"\b(?P<prefix>Д|D|ОК|OK|С|C|S)\s*[-—–]?\s*(?P<number>0*\d+)\b", re.IGNORECASE)
DIMENSIONS_RE = re.compile(
    r"(?P<w>\d+(?:[,.]\d+)?)\s*(?P<w_unit>мм|mm|см|cm|м|m)?\s*[xх×]\s*"
    r"(?P<h>\d+(?:[,.]\d+)?)\s*(?P<h_unit>мм|mm|см|cm|м|m)?",
    re.IGNORECASE,
)
COUNT_RE = re.compile(r"(?:кол(?:-?во|ичество)?|quantity)\s*[:=]?\s*(?P<count>\d+)\s*(?:шт\.?)?", re.IGNORECASE)
THICKNESS_RE = re.compile(r"(?:толщин\w*|блоки?|thickness)\D{0,24}(?P<value>\d+(?:[,.]\d+)?)\s*(?P<unit>мм|mm|см|cm|м|m)?", re.IGNORECASE)
MATERIAL_RE = re.compile(r"\b(?P<material>газобетон\w*|сталь|бетон|кирпич|aerated concrete|steel|concrete|brick)\b", re.IGNORECASE)
FLOOR_RE = re.compile(r"этаж\D{0,8}(?P<floor>\d+)", re.IGNORECASE)
ROOM_RE = re.compile(r"помещени\w*\D{0,8}(?P<room>\d+)", re.IGNORECASE)


def extract_entities(req: ExtractEntitiesRequest) -> ExtractEntitiesResponse:
    found: dict[tuple[str, str], Entity] = {}

    for page in req.pages:
        for block in page.blocks:
            text = clean_text(block.text)
            codes = list(CODE_RE.finditer(text))
            if not codes and "стен" in text.lower():
                codes = list(CODE_RE.finditer("С-1 " + text))

            for match in codes:
                code = normalize_code(match.group("prefix"), match.group("number"))
                entity_type = type_for_code(code)
                if entity_type is None:
                    continue
                evidence = make_evidence(req.document_id, page.page_id, page.page_number, block, text)
                key = (entity_type, code)
                entity = found.get(key)
                if entity is None:
                    entity = Entity(
                        id=stable_id(req.document_id, str(page.page_number), entity_type, code),
                        document_id=req.document_id,
                        stage=req.stage,
                        entity_type=entity_type,
                        raw_code=match.group(0),
                        normalized_code=code,
                        canonical_key=f"{entity_type}:{code}",
                        evidence=evidence,
                        confidence=block.confidence,
                    )
                    found[key] = entity

                entity.attributes.update(extract_attributes(entity_type, text, evidence))

    return ExtractEntitiesResponse(entities=sorted(found.values(), key=lambda e: (e.entity_type, e.normalized_code)))


def compare_entities(expected: list[Entity], actual: list[Entity]) -> CompareResponse:
    actual_by_key: dict[tuple[str, str], list[Entity]] = {}
    for entity in actual:
        actual_by_key.setdefault((entity.entity_type, entity.normalized_code), []).append(entity)

    matched_actual_ids: set[str] = set()
    matches: list[MatchResult] = []
    findings: list[Finding] = []

    for exp in sorted(expected, key=entity_sort_key):
        candidates = actual_by_key.get((exp.entity_type, exp.normalized_code), [])
        if len(candidates) == 1:
            act = candidates[0]
            matched_actual_ids.add(act.id)
            matches.append(match_result(exp, act))
            findings.extend(compare_fields(exp, act))
        elif len(candidates) > 1:
            matches.append(unmatched(exp, "AMBIGUOUS"))
            findings.append(entity_finding("AMBIGUOUS_MATCH", exp, candidates[0], "MEDIUM", "ambiguous.match.v1"))
        else:
            matches.append(unmatched(exp, "UNMATCHED"))
            findings.append(entity_finding("MISSING_ENTITY", exp, None, "HIGH", "entity.missing.v1"))

    for act in sorted(actual, key=entity_sort_key):
        if act.id not in matched_actual_ids:
            findings.append(entity_finding("EXTRA_ENTITY", act, act, "MEDIUM", "entity.extra.v1"))

    return CompareResponse(matches=matches, findings=dedupe_findings(findings))


def clean_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).replace("—", "-").replace("–", "-")
    return re.sub(r"\s+", " ", normalized).strip()


def normalize_code(prefix: str, number: str) -> str:
    canonical_prefix = CODE_ALIASES[prefix.upper()]
    return f"{canonical_prefix}-{number}"


def type_for_code(code: str) -> Optional[str]:
    if code.startswith("Д-"):
        return "DOOR"
    if code.startswith("ОК-"):
        return "WINDOW"
    if code.startswith("С-"):
        return "WALL"
    return None


def extract_attributes(entity_type: str, text: str, evidence: Evidence) -> dict[str, AttributeValue]:
    attrs: dict[str, AttributeValue] = {}

    if dimensions := DIMENSIONS_RE.search(text):
        width = to_mm(dimensions.group("w"), dimensions.group("w_unit") or dimensions.group("h_unit"))
        height = to_mm(dimensions.group("h"), dimensions.group("h_unit") or dimensions.group("w_unit"))
        attrs["width_mm"] = attr(dimensions.group("w"), width, "mm", evidence)
        attrs["height_mm"] = attr(dimensions.group("h"), height, "mm", evidence)

    if count := COUNT_RE.search(text):
        attrs["count"] = attr(count.group("count"), int(count.group("count")), "pcs", evidence)

    if entity_type == "WALL":
        if thickness := THICKNESS_RE.search(text):
            attrs["thickness_mm"] = attr(thickness.group("value"), to_mm(thickness.group("value"), thickness.group("unit")), "mm", evidence)
        if material := MATERIAL_RE.search(text):
            attrs["material"] = attr(material.group("material"), normalize_material(material.group("material")), None, evidence)

    if material := MATERIAL_RE.search(text):
        attrs.setdefault("material", attr(material.group("material"), normalize_material(material.group("material")), None, evidence))
    if floor := FLOOR_RE.search(text):
        attrs["floor"] = attr(floor.group("floor"), int(floor.group("floor")), None, evidence)
    if room := ROOM_RE.search(text):
        attrs["room"] = attr(room.group("room"), room.group("room"), None, evidence)

    return attrs


def to_mm(value: str, unit: Optional[str]) -> int:
    try:
        number = Decimal(value.replace(",", "."))
    except InvalidOperation as exc:
        raise ValueError(f"Invalid length: {value}") from exc

    normalized_unit = (unit or "мм").lower()
    if normalized_unit in {"м", "m"}:
        number *= 1000
    elif normalized_unit in {"см", "cm"}:
        number *= 10
    return int(number.to_integral_value())


def normalize_material(value: str) -> str:
    lowered = value.lower()
    if lowered.startswith("газобетон") or lowered == "aerated concrete":
        return "газобетон"
    return lowered


def attr(raw: str, normalized: Union[int, str], unit: Optional[str], evidence: Evidence) -> AttributeValue:
    return AttributeValue(raw=raw, normalized=normalized, unit=unit, evidence=evidence)


def make_evidence(document_id: str, page_id: Optional[str], page_number: int, block: TextBlock, quote: str) -> Evidence:
    return Evidence(
        document_id=document_id,
        page_id=page_id,
        page_number=page_number,
        text_block_id=block.id,
        bbox=block.bbox,
        quote=quote,
        confidence=block.confidence,
    )


def compare_fields(expected: Entity, actual: Entity) -> list[Finding]:
    findings: list[Finding] = []
    fields = sorted(set(expected.attributes) | set(actual.attributes))
    for field in fields:
        exp_attr = expected.attributes.get(field)
        act_attr = actual.attributes.get(field)
        if exp_attr is None or act_attr is None or exp_attr.normalized == act_attr.normalized:
            continue

        finding_type = "COUNT_CHANGED" if field == "count" else "MATERIAL_CHANGED" if field == "material" else "VALUE_CHANGED"
        delta = numeric_delta(exp_attr.normalized, act_attr.normalized)
        findings.append(
            Finding(
                finding_type=finding_type,
                entity_type=expected.entity_type,
                canonical_key=expected.canonical_key,
                field_name=field,
                expected_value=exp_attr.normalized,
                actual_value=act_attr.normalized,
                absolute_delta=delta,
                relative_delta=relative_delta(exp_attr.normalized, act_attr.normalized),
                severity=severity_for(finding_type, expected.entity_type, field, delta),
                title=title_for(expected, field, exp_attr.normalized, act_attr.normalized),
                description=f"{expected.normalized_code}: {field} expected {exp_attr.normalized}, actual {act_attr.normalized}",
                rule_id=f"{expected.entity_type.lower()}.{field}.v1",
                dedup_key=dedup_key(finding_type, expected, field),
                evidence=[finding_evidence("EXPECTED", expected, exp_attr.evidence), finding_evidence("ACTUAL", actual, act_attr.evidence)],
            )
        )
    return findings


def entity_finding(finding_type: str, expected: Entity, actual: Optional[Entity], severity: str, rule_id: str) -> Finding:
    entity = actual or expected
    side = "ACTUAL" if finding_type == "EXTRA_ENTITY" else "EXPECTED"
    return Finding(
        finding_type=finding_type,
        entity_type=entity.entity_type,
        canonical_key=entity.canonical_key,
        field_name="entity",
        expected_value=None if finding_type == "EXTRA_ENTITY" else entity.normalized_code,
        actual_value=entity.normalized_code if finding_type == "EXTRA_ENTITY" else None,
        severity=severity,
        title=f"{finding_type}: {entity.normalized_code}",
        description=f"{finding_type} for {entity.entity_type} {entity.normalized_code}",
        rule_id=rule_id,
        dedup_key=dedup_key(finding_type, entity, None),
        evidence=[finding_evidence(side, entity, entity.evidence)],
    )


def match_result(expected: Entity, actual: Entity) -> MatchResult:
    return MatchResult(
        expected_entity_id=expected.id,
        actual_entity_id=actual.id,
        status="MATCHED",
        method="EXACT_CODE_TYPE",
        confidence=1.0,
        score_breakdown=ScoreBreakdown(code=1.0, type=1.0, attributes=attribute_score(expected, actual)),
    )


def unmatched(entity: Entity, status: str) -> MatchResult:
    return MatchResult(
        expected_entity_id=entity.id,
        status=status,
        method="UNMATCHED",
        confidence=0.0,
        score_breakdown=ScoreBreakdown(code=0.0, type=0.0),
    )


def attribute_score(expected: Entity, actual: Entity) -> float:
    keys = set(expected.attributes) & set(actual.attributes)
    if not keys:
        return 0.0
    same = sum(expected.attributes[key].normalized == actual.attributes[key].normalized for key in keys)
    return same / len(keys)


def numeric_delta(expected: Union[int, str], actual: Union[int, str]) -> Optional[int]:
    if isinstance(expected, int) and isinstance(actual, int):
        return actual - expected
    return None


def relative_delta(expected: Union[int, str], actual: Union[int, str]) -> Optional[float]:
    if isinstance(expected, int) and isinstance(actual, int) and expected:
        return (actual - expected) / expected
    return None


def severity_for(finding_type: str, entity_type: str, field: str, delta: Optional[int]) -> str:
    if finding_type == "COUNT_CHANGED":
        return "HIGH"
    if finding_type == "MATERIAL_CHANGED":
        return "MEDIUM"
    if entity_type == "DOOR" and field == "width_mm" and delta is not None and abs(delta) >= 100:
        return "HIGH"
    if entity_type == "WALL" and field == "thickness_mm" and delta is not None and abs(delta) >= 50:
        return "HIGH"
    return "MEDIUM"


def title_for(entity: Entity, field: str, expected: Union[int, str], actual: Union[int, str]) -> str:
    labels = {
        "width_mm": "Изменена ширина",
        "height_mm": "Изменена высота",
        "thickness_mm": "Изменена толщина",
        "count": "Изменено количество",
        "material": "Изменён материал",
    }
    return f"{labels.get(field, 'Изменено значение')} {entity.normalized_code}: {expected} -> {actual}"


def finding_evidence(side: str, entity: Entity, evidence: Evidence) -> FindingEvidence:
    return FindingEvidence(
        side=side,
        entity_id=entity.id,
        document_id=evidence.document_id,
        page_id=evidence.page_id,
        page_number=evidence.page_number,
        text_block_id=evidence.text_block_id,
        bbox=evidence.bbox,
        quote=evidence.quote,
    )


def dedup_key(finding_type: str, entity: Entity, field: Optional[str]) -> str:
    return "|".join(part for part in [finding_type, entity.entity_type, entity.normalized_code, field] if part)


def dedupe_findings(findings: Iterable[Finding]) -> list[Finding]:
    deduped: dict[str, Finding] = {}
    for finding in findings:
        deduped.setdefault(finding.dedup_key, finding)
    return [deduped[key] for key in sorted(deduped)]


def entity_sort_key(entity: Entity) -> tuple[str, str, str]:
    return entity.entity_type, entity.normalized_code, entity.id


def stable_id(*parts: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, "|".join(parts)))
