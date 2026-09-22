from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from pathlib import Path
from typing import Any, Literal
from urllib.parse import unquote, urlparse

import anyio
import fitz
from fastapi import Depends, FastAPI, Header, HTTPException, Request, status
from pydantic import BaseModel, Field


MAX_PDF_PAGES = int(os.getenv("MAX_PDF_PAGES", "200"))
PDF_PARSE_TIMEOUT_SECONDS = float(os.getenv("PDF_PARSE_TIMEOUT_SECONDS", "15"))
PDF_CONCURRENCY = int(os.getenv("PDF_CONCURRENCY", "2"))
INTERNAL_API_TOKEN = os.getenv("INTERNAL_API_TOKEN", "")
MIN_TEXT_CHARS_PER_PAGE = int(os.getenv("MIN_TEXT_CHARS_PER_PAGE", "20"))

_semaphore = asyncio.Semaphore(PDF_CONCURRENCY)


app = FastAPI(title="pdf-parser", version="0.1.0")
logger = logging.getLogger("pdf-parser")
logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))


@app.middleware("http")
async def request_log(request: Request, call_next):
    started = time.monotonic()
    response = await call_next(request)
    logger.info(
        json.dumps(
            {
                "event": "request",
                "method": request.method,
                "path": request.url.path,
                "status_code": response.status_code,
                "correlation_id": request.headers.get("x-correlation-id"),
                "processing_ms": int((time.monotonic() - started) * 1000),
            }
        )
    )
    return response


class PdfRef(BaseModel):
    bucket: str | None = None
    storage_key: str | None = None
    document_id: str
    correlation_id: str = Field(min_length=1)
    local_path: str | None = None
    file_url: str | None = None


class PageExtractRequest(PdfRef):
    page_number: int = Field(ge=1)


class BBox(BaseModel):
    x0: float
    y0: float
    x1: float
    y1: float
    coordinate_system: Literal["normalized_top_left"] = "normalized_top_left"


class TextBlock(BaseModel):
    order: int
    text: str
    bbox: BBox
    confidence: float = 1.0
    source: Literal["TEXT_LAYER"] = "TEXT_LAYER"


def _auth(
    authorization: str | None = Header(default=None),
    x_internal_api_token: str | None = Header(default=None),
) -> None:
    if not INTERNAL_API_TOKEN:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "INTERNAL_API_TOKEN is not configured")

    bearer = None
    if authorization and authorization.lower().startswith("bearer "):
        bearer = authorization[7:]

    if INTERNAL_API_TOKEN not in {bearer, x_internal_api_token}:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid internal API token")


def _typed_error(code: str, message: str, http_status: int = status.HTTP_422_UNPROCESSABLE_ENTITY) -> HTTPException:
    return HTTPException(http_status, {"error": code, "message": message})


def _resolve_pdf_path(ref: PdfRef) -> Path:
    value = ref.local_path or ref.file_url or ref.storage_key
    if not value:
        raise _typed_error("PDF_SOURCE_REQUIRED", "Provide local_path or file_url for the test-mode parser")

    if value.startswith("file://"):
        parsed = urlparse(value)
        if parsed.netloc not in {"", "localhost"}:
            raise _typed_error("UNSUPPORTED_PDF_SOURCE", "Only local file URLs are supported")
        value = unquote(parsed.path)
    elif "://" in value:
        raise _typed_error("UNSUPPORTED_PDF_SOURCE", "Only local paths and file:// URLs are supported")

    path = Path(value)
    if not path.is_file():
        raise _typed_error("PDF_NOT_FOUND", "PDF file was not found", status.HTTP_404_NOT_FOUND)
    return path


def _open_pdf(path: Path) -> fitz.Document:
    try:
        doc = fitz.open(path)
    except Exception as exc:
        raise _typed_error("INVALID_PDF", "PDF is invalid or corrupt") from exc

    if doc.is_encrypted:
        doc.close()
        raise _typed_error("ENCRYPTED_PDF", "Encrypted PDF is not supported")
    if doc.page_count > MAX_PDF_PAGES:
        doc.close()
        raise _typed_error("PDF_TOO_LARGE", f"PDF has more than {MAX_PDF_PAGES} pages")
    return doc


def _deadline(started: float) -> None:
    if time.monotonic() - started > PDF_PARSE_TIMEOUT_SECONDS:
        raise _typed_error("PDF_PARSE_TIMEOUT", "PDF parsing exceeded timeout", status.HTTP_504_GATEWAY_TIMEOUT)


def _page_summary(page: fitz.Page) -> dict[str, Any]:
    text = page.get_text("text")
    char_count = len(text.strip())
    return {
        "page_number": page.number + 1,
        "width": page.rect.width,
        "height": page.rect.height,
        "rotation": page.rotation,
        "text_char_count": char_count,
        "text_quality": 1.0 if char_count >= MIN_TEXT_CHARS_PER_PAGE else 0.0,
        "needs_ocr": char_count < MIN_TEXT_CHARS_PER_PAGE,
    }


def _inspect_pdf(ref: PdfRef) -> dict[str, Any]:
    started = time.monotonic()
    path = _resolve_pdf_path(ref)
    doc = _open_pdf(path)
    try:
        pages = []
        for page in doc:
            _deadline(started)
            pages.append(_page_summary(page))
        return {
            "page_count": doc.page_count,
            "metadata": doc.metadata or {},
            "encrypted": False,
            "pages": pages,
        }
    finally:
        doc.close()


def _normalized_bbox(rect: fitz.Rect, page: fitz.Page) -> BBox:
    width = page.rect.width or 1
    height = page.rect.height or 1
    return BBox(
        x0=max(0.0, min(1.0, rect.x0 / width)),
        y0=max(0.0, min(1.0, rect.y0 / height)),
        x1=max(0.0, min(1.0, rect.x1 / width)),
        y1=max(0.0, min(1.0, rect.y1 / height)),
    )


def _extract_page(req: PageExtractRequest) -> dict[str, Any]:
    started = time.monotonic()
    path = _resolve_pdf_path(req)
    doc = _open_pdf(path)
    try:
        if req.page_number > doc.page_count:
            raise _typed_error("PAGE_NOT_FOUND", "Requested page does not exist", status.HTTP_404_NOT_FOUND)

        page = doc.load_page(req.page_number - 1)
        raw_text = page.get_text("text")
        blocks = []
        for block in page.get_text("dict").get("blocks", []):
            _deadline(started)
            if block.get("type") != 0:
                continue
            text = "\n".join(
                "".join(span.get("text", "") for span in line.get("spans", [])).strip()
                for line in block.get("lines", [])
            ).strip()
            if not text:
                continue
            blocks.append(
                TextBlock(
                    order=len(blocks) + 1,
                    text=text,
                    bbox=_normalized_bbox(fitz.Rect(block["bbox"]), page),
                ).model_dump()
            )

        return {
            "page_number": req.page_number,
            "width": page.rect.width,
            "height": page.rect.height,
            "raw_text": raw_text,
            "blocks": blocks,
            "processing_ms": int((time.monotonic() - started) * 1000),
        }
    finally:
        doc.close()


@app.get("/health/live")
def live() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/health/ready")
def ready() -> dict[str, str]:
    return {"status": "ready"}


@app.post("/v1/documents/inspect", dependencies=[Depends(_auth)])
async def inspect_document(req: PdfRef) -> dict[str, Any]:
    async with _semaphore:
        return await anyio.to_thread.run_sync(_inspect_pdf, req)


@app.post("/v1/pages/extract", dependencies=[Depends(_auth)])
async def extract_page(req: PageExtractRequest) -> dict[str, Any]:
    async with _semaphore:
        return await anyio.to_thread.run_sync(_extract_page, req)
