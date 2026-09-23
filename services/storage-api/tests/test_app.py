from __future__ import annotations

import hashlib

import pytest
from fastapi.testclient import TestClient
from minio.error import S3Error

from app import main


TOKEN = "test-token"


class FakeStat:
    def __init__(self, size: int, content_type: str = "application/pdf") -> None:
        self.size = size
        self.content_type = content_type


class FakeResponse:
    def __init__(self, data: bytes) -> None:
        self.data = data
        self.closed = False
        self.released = False

    def stream(self, _size: int):
        yield self.data[:3]
        yield self.data[3:]

    def close(self) -> None:
        self.closed = True

    def release_conn(self) -> None:
        self.released = True


class FakeErrorResponse:
    status = 404
    reason = "Not Found"


class FakeMinio:
    def __init__(self, data: bytes = b"%PDF-1.7\nbody") -> None:
        self.data = data

    def stat_object(self, _bucket: str, _key: str):
        return FakeStat(len(self.data))

    def get_object(self, _bucket: str, _key: str):
        return FakeResponse(self.data)


@pytest.fixture(autouse=True)
def configured(monkeypatch):
    monkeypatch.setattr(main.settings, "internal_api_token", TOKEN)
    monkeypatch.setattr(main.settings, "minio_access_key", "minioadmin")
    monkeypatch.setattr(main.settings, "minio_secret_key", "minioadmin")
    monkeypatch.setattr(main.settings, "minio_region", "us-east-1")
    monkeypatch.setattr(main.settings, "public_storage_base_url", "http://localhost/storage")
    monkeypatch.setattr(main.settings, "presigned_url_ttl_seconds", 900)
    monkeypatch.setattr(main.settings, "max_presigned_url_ttl_seconds", 3600)
    monkeypatch.setattr(main, "minio_client", lambda: FakeMinio())


def auth() -> dict[str, str]:
    return {"Authorization": f"Bearer {TOKEN}"}


def test_health_endpoints():
    client = TestClient(main.app)

    assert client.get("/health/live").json() == {"status": "live"}
    assert client.get("/health/ready").json() == {"status": "ready"}


def test_rejects_missing_bearer_token():
    client = TestClient(main.app)

    response = client.post("/v1/objects/presign-get", json={"bucket": "documents", "key": "a.pdf"})

    assert response.status_code == 401


def test_presigned_put_uses_public_storage_path():
    client = TestClient(main.app)

    response = client.post(
        "/v1/objects/presign-put",
        headers=auth(),
        json={"bucket": "documents", "key": "uploads/a.pdf", "expires_seconds": 60},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["method"] == "PUT"
    assert body["headers"] == {"Content-Type": "application/pdf"}
    assert body["url"].startswith("http://localhost/storage/documents/uploads/a.pdf?")
    assert "X-Amz-SignedHeaders=host" in body["url"]
    assert "X-Amz-Expires=60" in body["url"]


def test_presigned_get_caps_ttl():
    client = TestClient(main.app)

    response = client.post(
        "/v1/objects/presign-get",
        headers=auth(),
        json={"bucket": "documents", "key": "a.pdf", "expires_seconds": 9999},
    )

    assert response.status_code == 200
    assert response.json()["url"].startswith("http://localhost/storage/documents/a.pdf?")
    assert "X-Amz-Expires=3600" in response.json()["url"]


def test_confirm_streams_sha256_and_pdf_magic(monkeypatch):
    data = b"%PDF-1.7\nbody"
    monkeypatch.setattr(main, "minio_client", lambda: FakeMinio(data))
    client = TestClient(main.app)

    response = client.post("/v1/objects/confirm", headers=auth(), json={"bucket": "documents", "key": "a.pdf"})

    assert response.status_code == 200
    body = response.json()
    assert body["exists"] is True
    assert body["is_pdf"] is True
    assert body["size_bytes"] == len(data)
    assert body["sha256"] == hashlib.sha256(data).hexdigest()


def test_confirm_rejects_sha256_mismatch():
    client = TestClient(main.app)

    response = client.post(
        "/v1/objects/confirm",
        headers=auth(),
        json={"bucket": "documents", "key": "a.pdf", "expected_sha256": "0" * 64},
    )

    assert response.status_code == 409


def test_confirm_returns_404_for_missing_object(monkeypatch):
    class Missing(FakeMinio):
        def stat_object(self, _bucket: str, _key: str):
            raise S3Error("NoSuchKey", "missing", "resource", "request", "host", FakeErrorResponse())

    monkeypatch.setattr(main, "minio_client", lambda: Missing())
    client = TestClient(main.app)

    response = client.post("/v1/objects/confirm", headers=auth(), json={"bucket": "documents", "key": "missing.pdf"})

    assert response.status_code == 404
