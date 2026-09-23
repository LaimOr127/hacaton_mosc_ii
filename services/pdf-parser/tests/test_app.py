from pathlib import Path
from types import SimpleNamespace

import fitz
from fastapi.testclient import TestClient

from app import main
from app.main import app


TOKEN = "test-token"


def _make_pdf(path: Path, text: str = "D-14 900x2100") -> None:
    doc = fitz.open()
    page = doc.new_page(width=200, height=100)
    page.insert_text((20, 40), text)
    doc.save(path)
    doc.close()


class _ObjectResponse:
    def __init__(self, data: bytes):
        self.data = data
        self.closed = False
        self.released = False

    def read(self, amount: int) -> bytes:
        return self.data[:amount]

    def close(self) -> None:
        self.closed = True

    def release_conn(self) -> None:
        self.released = True


class _MinioStub:
    def __init__(self, data: bytes):
        self.data = data
        self.calls: list[tuple[str, str, str]] = []
        self.response = _ObjectResponse(data)

    def stat_object(self, bucket: str, storage_key: str):
        self.calls.append(("stat", bucket, storage_key))
        return SimpleNamespace(size=len(self.data))

    def get_object(self, bucket: str, storage_key: str):
        self.calls.append(("get", bucket, storage_key))
        return self.response


def _pdf_bytes(tmp_path: Path, text: str = "D-14 900x2100") -> bytes:
    pdf = tmp_path / "doc.pdf"
    _make_pdf(pdf, text)
    return pdf.read_bytes()


def test_health_endpoints(monkeypatch):
    monkeypatch.setenv("INTERNAL_API_TOKEN", TOKEN)
    client = TestClient(app)

    assert client.get("/health/live").json() == {"status": "ok"}
    assert client.get("/health/ready").json() == {"status": "ready"}


def test_rejects_missing_token(tmp_path, monkeypatch):
    monkeypatch.setattr("app.main.INTERNAL_API_TOKEN", TOKEN)
    client = TestClient(app)

    response = client.post(
        "/v1/documents/inspect",
        json={"document_id": "doc-1", "correlation_id": "corr-1", "bucket": "documents", "storage_key": "a/doc.pdf"},
    )

    assert response.status_code == 401


def test_inspects_pdf(tmp_path, monkeypatch):
    monkeypatch.setattr("app.main.INTERNAL_API_TOKEN", TOKEN)
    store = _MinioStub(_pdf_bytes(tmp_path))
    monkeypatch.setattr(main, "_minio_client", lambda: store)
    client = TestClient(app)

    response = client.post(
        "/v1/documents/inspect",
        headers={"X-Internal-Api-Token": TOKEN},
        json={"document_id": "doc-1", "correlation_id": "corr-1", "bucket": "documents", "storage_key": "a/doc.pdf"},
    )

    assert response.status_code == 200
    assert store.calls == [("stat", "documents", "a/doc.pdf"), ("get", "documents", "a/doc.pdf")]
    assert store.response.closed is True
    assert store.response.released is True
    body = response.json()
    assert body["page_count"] == 1
    assert body["encrypted"] is False
    assert body["pages"][0]["page_number"] == 1
    assert body["pages"][0]["needs_ocr"] is True


def test_extracts_page_blocks(tmp_path, monkeypatch):
    monkeypatch.setattr("app.main.INTERNAL_API_TOKEN", TOKEN)
    monkeypatch.setattr(main, "_minio_client", lambda: _MinioStub(_pdf_bytes(tmp_path, "Door D-14")))
    client = TestClient(app)

    response = client.post(
        "/v1/pages/extract",
        headers={"Authorization": f"Bearer {TOKEN}"},
        json={
            "document_id": "doc-1",
            "correlation_id": "corr-1",
            "bucket": "documents",
            "storage_key": "a/doc.pdf",
            "page_number": 1,
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["page_number"] == 1
    assert "Door D-14" in body["raw_text"]
    assert body["blocks"][0]["source"] == "TEXT_LAYER"
    assert body["blocks"][0]["bbox"]["coordinate_system"] == "normalized_top_left"


def test_rejects_corrupt_pdf(tmp_path, monkeypatch):
    monkeypatch.setattr("app.main.INTERNAL_API_TOKEN", TOKEN)
    monkeypatch.setattr(main, "_minio_client", lambda: _MinioStub(b"not a pdf"))
    client = TestClient(app)

    response = client.post(
        "/v1/documents/inspect",
        headers={"X-Internal-Api-Token": TOKEN},
        json={"document_id": "doc-1", "correlation_id": "corr-1", "bucket": "documents", "storage_key": "bad.pdf"},
    )

    assert response.status_code == 422
    assert response.json()["detail"]["error"] == "INVALID_PDF"


def test_rejects_local_file_sources(monkeypatch):
    monkeypatch.setattr("app.main.INTERNAL_API_TOKEN", TOKEN)
    client = TestClient(app)

    response = client.post(
        "/v1/documents/inspect",
        headers={"X-Internal-Api-Token": TOKEN},
        json={"document_id": "doc-1", "correlation_id": "corr-1", "local_path": "/tmp/doc.pdf"},
    )

    assert response.status_code == 422


def test_rejects_oversize_pdf_before_download(monkeypatch):
    monkeypatch.setattr("app.main.INTERNAL_API_TOKEN", TOKEN)
    monkeypatch.setattr("app.main.MAX_PDF_BYTES", 3)
    store = _MinioStub(b"too large")
    monkeypatch.setattr(main, "_minio_client", lambda: store)
    client = TestClient(app)

    response = client.post(
        "/v1/documents/inspect",
        headers={"X-Internal-Api-Token": TOKEN},
        json={"document_id": "doc-1", "correlation_id": "corr-1", "bucket": "documents", "storage_key": "big.pdf"},
    )

    assert response.status_code == 413
    assert response.json()["detail"]["error"] == "PDF_TOO_LARGE"
    assert store.calls == [("stat", "documents", "big.pdf")]
