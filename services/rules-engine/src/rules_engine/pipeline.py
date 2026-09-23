from __future__ import annotations

import uuid
from typing import Literal

import httpx
from pydantic import BaseModel, Field

from .engine import compare_entities, extract_entities
from .models import Entity, ExtractEntitiesRequest


class DocumentRef(BaseModel):
    id: uuid.UUID
    stage: Literal["PROJECT", "WORKING", "AS_BUILT"]
    bucket: str = Field(min_length=1)
    storage_key: str = Field(min_length=1)


class AnalyzeRequest(BaseModel):
    inspection_id: uuid.UUID
    documents: list[DocumentRef] = Field(min_length=3, max_length=12)


def stable_uuid(*parts: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, "|".join(parts)))


def analyze(request: AnalyzeRequest, client: httpx.Client, token: str, parser_url: str, ocr_url: str) -> dict:
    documents: list[dict] = []
    by_stage: dict[str, list[Entity]] = {stage: [] for stage in ("PROJECT", "WORKING", "AS_BUILT")}
    ocr_pages = 0

    # ponytail: one bounded result per inspection; partition pages when larger jobs are supported.
    for ref in request.documents:
        base = {"document_id": str(ref.id), "bucket": ref.bucket, "storage_key": ref.storage_key,
                "correlation_id": str(request.inspection_id)}
        inspected = client.post(
            f"{parser_url}/v1/documents/inspect", json=base,
            headers={"X-Internal-Api-Token": token}, timeout=30,
        )
        inspected.raise_for_status()
        summary = inspected.json()
        pages: list[dict] = []
        for page in summary["pages"]:
            page_number = page["page_number"]
            page_id = stable_uuid(str(ref.id), str(page_number))
            if page["needs_ocr"]:
                extracted = client.post(
                    f"{ocr_url}/v1/pages/recognize",
                    json={**base, "page_number": page_number},
                    headers={"X-Internal-Token": token}, timeout=150,
                )
                ocr_pages += 1
            else:
                extracted = client.post(
                    f"{parser_url}/v1/pages/extract",
                    json={**base, "page_number": page_number},
                    headers={"X-Internal-Api-Token": token}, timeout=30,
                )
            extracted.raise_for_status()
            content = extracted.json()
            blocks = [
                {**block, "id": stable_uuid(page_id, str(block["order"]), block["source"])}
                for block in content["blocks"]
            ]
            pages.append({"page_id": page_id, **page, "raw_text": content["raw_text"],
                          "ocr_used": bool(page["needs_ocr"]), "blocks": blocks})

        entities = extract_entities(ExtractEntitiesRequest.model_validate({
            "document_id": str(ref.id), "stage": ref.stage, "pages": pages,
        })).entities
        by_stage[ref.stage].extend(entities)
        documents.append({"document_id": str(ref.id), "stage": ref.stage,
                          "page_count": summary["page_count"], "pages": pages,
                          "entities": [entity.model_dump(mode="json") for entity in entities]})

    comparisons: list[dict] = []
    all_findings: dict[str, dict] = {}
    match_count = 0
    for expected_stage, actual_stage in (("PROJECT", "WORKING"), ("WORKING", "AS_BUILT"), ("PROJECT", "AS_BUILT")):
        compared = compare_entities(by_stage[expected_stage], by_stage[actual_stage])
        matches = [match.model_dump(mode="json") for match in compared.matches]
        findings = [finding.model_dump(mode="json") for finding in compared.findings]
        match_count += sum(match["status"] == "MATCHED" for match in matches)
        for finding in findings:
            all_findings.setdefault(finding["dedup_key"], finding)
        comparisons.append({"expected_stage": expected_stage, "actual_stage": actual_stage,
                            "matches": matches, "findings": findings})

    return {"documents": documents, "comparisons": comparisons,
            "findings": list(all_findings.values()),
            "stats": {"documents": len(documents), "pages": sum(doc["page_count"] for doc in documents),
                      "ocr_pages": ocr_pages, "entities": sum(len(doc["entities"]) for doc in documents),
                      "matches": match_count, "findings": len(all_findings)}}
