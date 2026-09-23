from __future__ import annotations

import asyncio
import io
import json
import logging
import os
import time
from typing import Any, Literal

import anyio
import fitz
from fastapi import Depends, FastAPI, Header, HTTPException, Request, status
from minio import Minio
from pydantic import BaseModel, Field
from pydantic import ConfigDict, model_validator


MAX_PDF_PAGES = int(os.getenv("MAX_PDF_PAGES", "200"))
MAX_PDF_BYTES = int(os.getenv("MAX_PDF_BYTES", str(50 * 1024 * 1024)))
PDF_PARSE_TIMEOUT_SECONDS = float(os.getenv("PDF_PARSE_TIMEOUT_SECONDS", "15"))
PDF_CONCURRENCY = int(os.getenv("PDF_CONCURRENCY", "2"))
INTERNAL_API_TOKEN = os.getenv("INTERNAL_API_TOKEN", "")
MIN_TEXT_CHARS_PER_PAGE = int(os.getenv("MIN_TEXT_CHARS_PER_PAGE", "20"))
MINIO_ENDPOINT = os.getenv("MINIO_ENDPOINT", "minio:9000")
MINIO_ACCESS_KEY = os.getenv("MINIO_ACCESS_KEY") or os.getenv("MINIO_ROOT_USER") or ""
MINIO_SECRET_KEY = os.getenv("MINIO_SECRET_KEY") or os.getenv("MINIO_ROOT_PASSWORD") or ""
MINIO_SECURE = os.getenv("MINIO_SECURE", "false").lower() == "true"

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
    model_config = ConfigDict(extra="forbid")

    bucket: str = Field(min_length=1)
    storage_key: str = Field(min_length=1)
    document_id: str
    correlation_id: str = Field(min_length=1)

    @model_validator(mode="after")
    def reject_path_like_storage_key(self) -> "PdfRef":
        if "://" in self.storage_key or self.storage_key.startswith("/"):
            raise ValueError("storage_key must be a MinIO object key")
        return self


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


def _minio_client() -> Minio:
    if not MINIO_ACCESS_KEY or not MINIO_SECRET_KEY:
        raise _typed_error("MINIO_NOT_CONFIGURED", "MinIO credentials are not configured", status.HTTP_503_SERVICE_UNAVAILABLE)
    return Minio(
        MINIO_ENDPOINT,
        access_key=MINIO_ACCESS_KEY,
        secret_key=MINIO_SECRET_KEY,
        secure=MINIO_SECURE,
    )


def _load_pdf_bytes(ref: PdfRef) -> bytes:
    client = _minio_client()
    stat = client.stat_object(ref.bucket, ref.storage_key)
    if stat.size is not None and stat.size > MAX_PDF_BYTES:
        raise _typed_error("PDF_TOO_LARGE", f"PDF is larger than {MAX_PDF_BYTES} bytes", status.HTTP_413_REQUEST_ENTITY_TOO_LARGE)

    response = client.get_object(ref.bucket, ref.storage_key)
    try:
        data = response.read(MAX_PDF_BYTES + 1)
    finally:
        response.close()
        response.release_conn()
    if len(data) > MAX_PDF_BYTES:
        raise _typed_error("PDF_TOO_LARGE", f"PDF is larger than {MAX_PDF_BYTES} bytes", status.HTTP_413_REQUEST_ENTITY_TOO_LARGE)
    return data


def _open_pdf(pdf_bytes: bytes) -> fitz.Document:
    try:
        doc = fitz.open(stream=io.BytesIO(pdf_bytes), filetype="pdf")
    except Exception as exc:
        raise _typed_error("INVALID_PDF", "PDF is invalid or corrupt") from exc

    if doc.is_encrypted:
        doc.close()
        raise _typed_error("ENCRYPTED_PDF", "Encrypted PDF is not supported")
    if doc.page_count > MAX_PDF_PAGES:
        doc.close()
        raise _typed_error("PDF_TOO_LARGE", f"PDF has more than {MAX_PDF_PAGES} pages", status.HTTP_413_REQUEST_ENTITY_TOO_LARGE)
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
    doc = _open_pdf(_load_pdf_bytes(ref))
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
    doc = _open_pdf(_load_pdf_bytes(req))
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
            for line in block.get("lines", []):
                text = "".join(span.get("text", "") for span in line.get("spans", [])).strip()
                if not text:
                    continue
                blocks.append(
                    TextBlock(
                        order=len(blocks) + 1,
                        text=text,
                        bbox=_normalized_bbox(fitz.Rect(line["bbox"]), page),
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
