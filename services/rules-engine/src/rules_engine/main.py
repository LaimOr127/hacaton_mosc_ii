from __future__ import annotations

import os
from typing import Optional

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException, status

from .engine import compare_entities, extract_entities
from .models import CompareRequest, CompareResponse, ExtractEntitiesRequest, ExtractEntitiesResponse
from .pipeline import AnalyzeRequest, analyze


INTERNAL_API_TOKEN = os.getenv("INTERNAL_API_TOKEN", "")

app = FastAPI(title="rules-engine", version="0.1.0")


def _auth(
    authorization: Optional[str] = Header(default=None),
    x_internal_api_token: Optional[str] = Header(default=None),
) -> None:
    if not INTERNAL_API_TOKEN:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "INTERNAL_API_TOKEN is not configured")

    bearer = None
    if authorization and authorization.lower().startswith("bearer "):
        bearer = authorization[7:]

    if INTERNAL_API_TOKEN not in {bearer, x_internal_api_token}:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid internal API token")


@app.get("/health/live")
def live() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/health/ready")
def ready() -> dict[str, str]:
    return {"status": "ready"}


@app.post("/v1/text/entities", response_model=ExtractEntitiesResponse, dependencies=[Depends(_auth)])
def entities(req: ExtractEntitiesRequest) -> ExtractEntitiesResponse:
    return extract_entities(req)


@app.post("/v1/compare/findings", response_model=CompareResponse, dependencies=[Depends(_auth)])
def findings(req: CompareRequest) -> CompareResponse:
    return compare_entities(req.expected_entities, req.actual_entities)


@app.post("/v1/inspections/analyze", dependencies=[Depends(_auth)])
def analyze_inspection(req: AnalyzeRequest) -> dict:
    try:
        with httpx.Client() as client:
            return analyze(
                req, client, INTERNAL_API_TOKEN,
                os.getenv("PDF_PARSER_URL", "http://pdf-parser:8000"),
                os.getenv("OCR_SERVICE_URL", "http://ocr-service:8000"),
            )
    except (httpx.HTTPError, KeyError, ValueError) as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "processing dependency failed") from exc
