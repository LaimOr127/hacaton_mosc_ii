import io
import os
import subprocess
import time
from typing import Annotated

import fitz
import pytesseract
from fastapi import Depends, FastAPI, Header, HTTPException, status
from minio import Minio
from PIL import Image
from pydantic import BaseModel, Field


TOKEN_HEADER = "X-Internal-Token"


def env_str(name: str, default: str) -> str:
    return os.getenv(name) or default


def env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    return int(value) if value else default


class Settings(BaseModel):
    internal_api_token: str = Field(default_factory=lambda: env_str("INTERNAL_API_TOKEN", ""))
    ocr_languages: str = Field(default_factory=lambda: env_str("OCR_LANGUAGES", "rus+eng"))
    ocr_dpi: int = Field(default_factory=lambda: env_int("OCR_DPI", 300))
    ocr_timeout_seconds: int = Field(default_factory=lambda: env_int("OCR_TIMEOUT_SECONDS", 120))
    max_pdf_bytes: int = Field(default_factory=lambda: env_int("MAX_PDF_BYTES", 50 * 1024 * 1024))
    max_pdf_pages: int = Field(default_factory=lambda: env_int("MAX_PDF_PAGES", 200))
    minio_endpoint: str = Field(default_factory=lambda: env_str("MINIO_ENDPOINT", "minio:9000"))
    minio_access_key: str = Field(default_factory=lambda: os.getenv("MINIO_ACCESS_KEY") or os.getenv("MINIO_ROOT_USER") or "")
    minio_secret_key: str = Field(default_factory=lambda: os.getenv("MINIO_SECRET_KEY") or os.getenv("MINIO_ROOT_PASSWORD") or "")
    minio_secure: bool = Field(default_factory=lambda: env_str("MINIO_SECURE", "false").lower() == "true")


settings = Settings()
app = FastAPI(title="construction-ocr-service")


class RecognitionRequest(BaseModel):
    document_id: str
    bucket: str
    storage_key: str
    page_number: int = Field(ge=1)
    languages: list[str] | None = None
    dpi: int | None = Field(default=None, ge=72, le=600)
    correlation_id: str | None = None


class BBox(BaseModel):
    x0: float
    y0: float
    x1: float
    y1: float
    coordinate_system: str = "normalized_top_left"


class OcrBlock(BaseModel):
    order: int
    text: str
    bbox: BBox
    confidence: float | None
    source: str = "OCR"


class RecognitionResponse(BaseModel):
    page_number: int
    raw_text: str
    blocks: list[OcrBlock]
    processing_ms: int
    engine: str = "tesseract"
    engine_version: str


def require_internal_token(x_internal_token: Annotated[str | None, Header(alias=TOKEN_HEADER)] = None) -> None:
    if not settings.internal_api_token:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "INTERNAL_API_TOKEN is not configured")
    if x_internal_token != settings.internal_api_token:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid internal token")


def tesseract_version() -> str:
    try:
        return str(pytesseract.get_tesseract_version())
    except Exception as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "tesseract is not ready") from exc


def languages_ready(languages: str) -> bool:
    try:
        available = set(pytesseract.get_languages(config=""))
    except Exception:
        return False
    return set(languages.split("+")).issubset(available)


def minio_client() -> Minio:
    if not settings.minio_access_key or not settings.minio_secret_key:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "MinIO credentials are not configured")
    return Minio(
        settings.minio_endpoint,
        access_key=settings.minio_access_key,
        secret_key=settings.minio_secret_key,
        secure=settings.minio_secure,
    )


def load_object(bucket: str, storage_key: str) -> bytes:
    client = minio_client()
    stat = client.stat_object(bucket, storage_key)
    if stat.size is not None and stat.size > settings.max_pdf_bytes:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "PDF is too large")

    response = client.get_object(bucket, storage_key)
    try:
        data = response.read(settings.max_pdf_bytes + 1)
    finally:
        response.close()
        response.release_conn()
    if len(data) > settings.max_pdf_bytes:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "PDF is too large")
    return data


def render_pdf_page(pdf_bytes: bytes, page_number: int, dpi: int) -> Image.Image:
    try:
        with fitz.open(stream=pdf_bytes, filetype="pdf") as document:
            if document.page_count > settings.max_pdf_pages:
                raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "PDF has too many pages")
            if page_number > document.page_count:
                raise HTTPException(status.HTTP_400_BAD_REQUEST, "page_number is out of range")
            page = document.load_page(page_number - 1)
            pixmap = page.get_pixmap(dpi=dpi, alpha=False)
            return Image.open(io.BytesIO(pixmap.tobytes("png"))).convert("RGB")
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "cannot render PDF page") from exc


def recognize_image(image: Image.Image, languages: str, timeout: int) -> tuple[str, list[OcrBlock]]:
    data = pytesseract.image_to_data(
        image,
        lang=languages,
        output_type=pytesseract.Output.DICT,
        timeout=timeout,
    )
    width, height = image.size
    lines: dict[tuple[int, int, int], list[tuple[str, float, int, int, int, int]]] = {}
    for index, text in enumerate(data.get("text", [])):
        text = text.strip()
        if not text:
            continue
        key = (data["block_num"][index], data["par_num"][index], data["line_num"][index])
        x, y, w, h = data["left"][index], data["top"][index], data["width"][index], data["height"][index]
        lines.setdefault(key, []).append((text, float(data["conf"][index]), x, y, x + w, y + h))

    blocks: list[OcrBlock] = []
    for words in lines.values():
        positive_confidence = [word[1] for word in words if word[1] >= 0]
        blocks.append(
            OcrBlock(
                order=len(blocks) + 1,
                text=" ".join(word[0] for word in words),
                bbox=BBox(
                    x0=min(word[2] for word in words) / width,
                    y0=min(word[3] for word in words) / height,
                    x1=max(word[4] for word in words) / width,
                    y1=max(word[5] for word in words) / height,
                ),
                confidence=sum(positive_confidence) / (100 * len(positive_confidence)) if positive_confidence else None,
            )
        )
    return "\n".join(block.text for block in blocks), blocks


@app.get("/health/live")
def live() -> dict[str, str]:
    return {"status": "live"}


@app.get("/health/ready")
def ready() -> dict[str, str]:
    version = tesseract_version()
    if not languages_ready(settings.ocr_languages):
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "required OCR languages are not installed")
    return {"status": "ready", "engine": "tesseract", "engine_version": version}


@app.post("/v1/pages/recognize", response_model=RecognitionResponse, dependencies=[Depends(require_internal_token)])
def recognize(request: RecognitionRequest) -> RecognitionResponse:
    started = time.perf_counter()
    languages = "+".join(request.languages) if request.languages else settings.ocr_languages
    dpi = request.dpi or settings.ocr_dpi
    try:
        image = render_pdf_page(load_object(request.bucket, request.storage_key), request.page_number, dpi)
        raw_text, blocks = recognize_image(image, languages, settings.ocr_timeout_seconds)
    except RuntimeError as exc:
        if isinstance(exc.__cause__, subprocess.TimeoutExpired):
            raise HTTPException(status.HTTP_504_GATEWAY_TIMEOUT, "OCR timed out") from exc
        raise
    return RecognitionResponse(
        page_number=request.page_number,
        raw_text=raw_text,
        blocks=blocks,
        processing_ms=int((time.perf_counter() - started) * 1000),
        engine_version=tesseract_version(),
    )
