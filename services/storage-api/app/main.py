from __future__ import annotations

import hashlib
import os
from datetime import timedelta
from typing import Annotated
from urllib.parse import urlparse, urlunparse

from fastapi import Depends, FastAPI, Header, HTTPException, status
from minio import Minio
from minio.error import S3Error
from pydantic import BaseModel, Field, field_validator


PDF_MAGIC = b"%PDF-"
TOKEN_PREFIX = "bearer "


def env_str(name: str, default: str) -> str:
    return os.getenv(name) or default


def env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    return int(value) if value else default


class Settings(BaseModel):
    internal_api_token: str = Field(default_factory=lambda: env_str("INTERNAL_API_TOKEN", ""))
    minio_endpoint: str = Field(default_factory=lambda: env_str("MINIO_ENDPOINT", "minio:9000"))
    minio_access_key: str = Field(default_factory=lambda: os.getenv("MINIO_ACCESS_KEY") or os.getenv("MINIO_ROOT_USER") or "")
    minio_secret_key: str = Field(default_factory=lambda: os.getenv("MINIO_SECRET_KEY") or os.getenv("MINIO_ROOT_PASSWORD") or "")
    minio_secure: bool = Field(default_factory=lambda: env_str("MINIO_SECURE", "false").lower() == "true")
    minio_region: str = Field(default_factory=lambda: env_str("MINIO_REGION", "us-east-1"))
    public_storage_base_url: str = Field(default_factory=lambda: env_str("PUBLIC_STORAGE_BASE_URL", "http://localhost/storage"))
    presigned_url_ttl_seconds: int = Field(default_factory=lambda: env_int("PRESIGNED_URL_TTL_SECONDS", 900))
    max_presigned_url_ttl_seconds: int = Field(default_factory=lambda: env_int("MAX_PRESIGNED_URL_TTL_SECONDS", 3600))


settings = Settings()
app = FastAPI(title="storage-api", version="0.1.0")


class ObjectRef(BaseModel):
    bucket: str = Field(min_length=3, max_length=63)
    key: str = Field(min_length=1, max_length=1024)

    @field_validator("bucket")
    @classmethod
    def valid_bucket(cls, value: str) -> str:
        allowed = set("abcdefghijklmnopqrstuvwxyz0123456789.-")
        if value[0] in ".-" or value[-1] in ".-" or any(char not in allowed for char in value):
            raise ValueError("invalid bucket name")
        return value

    @field_validator("key")
    @classmethod
    def valid_key(cls, value: str) -> str:
        if value.startswith("/") or "/../" in f"/{value}/" or value.endswith("/"):
            raise ValueError("invalid object key")
        return value


class PresignPutRequest(ObjectRef):
    content_type: str = Field(default="application/pdf", min_length=1, max_length=128)
    expires_seconds: int | None = Field(default=None, ge=1)


class PresignGetRequest(ObjectRef):
    expires_seconds: int | None = Field(default=None, ge=1)


class ConfirmRequest(ObjectRef):
    expected_sha256: str | None = Field(default=None, min_length=64, max_length=64)


class PresignedUrlResponse(BaseModel):
    bucket: str
    key: str
    method: str
    url: str
    expires_seconds: int
    headers: dict[str, str] = Field(default_factory=dict)


class ConfirmResponse(BaseModel):
    bucket: str
    key: str
    exists: bool
    size_bytes: int
    content_type: str | None
    sha256: str
    is_pdf: bool


def require_bearer_token(authorization: Annotated[str | None, Header()] = None) -> None:
    if not settings.internal_api_token:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "INTERNAL_API_TOKEN is not configured")
    if not authorization or not authorization.lower().startswith(TOKEN_PREFIX):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "missing bearer token")
    if authorization[len(TOKEN_PREFIX) :] != settings.internal_api_token:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid bearer token")


def minio_client() -> Minio:
    if not settings.minio_access_key or not settings.minio_secret_key:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "MinIO credentials are not configured")
    return Minio(
        settings.minio_endpoint,
        access_key=settings.minio_access_key,
        secret_key=settings.minio_secret_key,
        secure=settings.minio_secure,
        region=settings.minio_region,
    )


def presign_client() -> Minio:
    base = urlparse(settings.public_storage_base_url.rstrip("/"))
    if not base.scheme or not base.netloc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "PUBLIC_STORAGE_BASE_URL is invalid")
    if not settings.minio_access_key or not settings.minio_secret_key:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "MinIO credentials are not configured")
    return Minio(
        base.netloc,
        access_key=settings.minio_access_key,
        secret_key=settings.minio_secret_key,
        secure=base.scheme == "https",
        region=settings.minio_region,
    )


def expires_delta(seconds: int | None) -> tuple[timedelta, int]:
    value = seconds or settings.presigned_url_ttl_seconds
    value = min(value, settings.max_presigned_url_ttl_seconds)
    return timedelta(seconds=value), value


def public_url(url: str) -> str:
    base = urlparse(settings.public_storage_base_url.rstrip("/"))
    signed = urlparse(url)
    path = f"{base.path.rstrip('/')}/{signed.path.lstrip('/')}"
    return urlunparse((base.scheme, base.netloc, path, "", signed.query, ""))


def not_found(exc: S3Error) -> bool:
    return exc.code in {"NoSuchKey", "NoSuchBucket", "NoSuchObject", "NotFound"}


@app.get("/health/live")
def live() -> dict[str, str]:
    return {"status": "live"}


@app.get("/health/ready")
def ready() -> dict[str, str]:
    minio_client()
    return {"status": "ready"}


@app.post("/v1/objects/presign-put", response_model=PresignedUrlResponse, dependencies=[Depends(require_bearer_token)])
def presign_put(request: PresignPutRequest) -> PresignedUrlResponse:
    expires, expires_seconds = expires_delta(request.expires_seconds)
    url = presign_client().presigned_put_object(request.bucket, request.key, expires=expires)
    return PresignedUrlResponse(
        bucket=request.bucket,
        key=request.key,
        method="PUT",
        url=public_url(url),
        expires_seconds=expires_seconds,
        headers={"Content-Type": request.content_type},
    )


@app.post("/v1/objects/presign-get", response_model=PresignedUrlResponse, dependencies=[Depends(require_bearer_token)])
def presign_get(request: PresignGetRequest) -> PresignedUrlResponse:
    expires, expires_seconds = expires_delta(request.expires_seconds)
    url = presign_client().presigned_get_object(request.bucket, request.key, expires=expires)
    return PresignedUrlResponse(
        bucket=request.bucket,
        key=request.key,
        method="GET",
        url=public_url(url),
        expires_seconds=expires_seconds,
    )


@app.post("/v1/objects/confirm", response_model=ConfirmResponse, dependencies=[Depends(require_bearer_token)])
def confirm(request: ConfirmRequest) -> ConfirmResponse:
    client = minio_client()
    try:
        stat = client.stat_object(request.bucket, request.key)
        response = client.get_object(request.bucket, request.key)
    except S3Error as exc:
        if not_found(exc):
            raise HTTPException(status.HTTP_404_NOT_FOUND, "object not found") from exc
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "MinIO request failed") from exc

    digest = hashlib.sha256()
    first = b""
    try:
        for chunk in response.stream(1024 * 1024):
            if len(first) < len(PDF_MAGIC):
                first = (first + chunk)[: len(PDF_MAGIC)]
            digest.update(chunk)
    finally:
        response.close()
        response.release_conn()

    sha256 = digest.hexdigest()
    if request.expected_sha256 and request.expected_sha256.lower() != sha256:
        raise HTTPException(status.HTTP_409_CONFLICT, "sha256 mismatch")

    return ConfirmResponse(
        bucket=request.bucket,
        key=request.key,
        exists=True,
        size_bytes=stat.size,
        content_type=stat.content_type,
        sha256=sha256,
        is_pdf=first == PDF_MAGIC,
    )
