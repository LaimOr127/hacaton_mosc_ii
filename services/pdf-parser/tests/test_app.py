from pathlib import Path

import fitz
from fastapi.testclient import TestClient

from app.main import app


TOKEN = "test-token"


def _make_pdf(path: Path, text: str = "D-14 900x2100") -> None:
    doc = fitz.open()
    page = doc.new_page(width=200, height=100)
    page.insert_text((20, 40), text)
    doc.save(path)
    doc.close()


def test_health_endpoints(monkeypatch):
    monkeypatch.setenv("INTERNAL_API_TOKEN", TOKEN)
    client = TestClient(app)

    assert client.get("/health/live").json() == {"status": "ok"}
    assert client.get("/health/ready").json() == {"status": "ready"}


def test_rejects_missing_token(tmp_path, monkeypatch):
    monkeypatch.setattr("app.main.INTERNAL_API_TOKEN", TOKEN)
    pdf = tmp_path / "doc.pdf"
    _make_pdf(pdf)
    client = TestClient(app)

    response = client.post(
        "/v1/documents/inspect",
        json={"document_id": "doc-1", "correlation_id": "corr-1", "local_path": str(pdf)},
    )

    assert response.status_code == 401


def test_inspects_pdf(tmp_path, monkeypatch):
    monkeypatch.setattr("app.main.INTERNAL_API_TOKEN", TOKEN)
    pdf = tmp_path / "doc.pdf"
    _make_pdf(pdf)
    client = TestClient(app)

    response = client.post(
        "/v1/documents/inspect",
        headers={"X-Internal-Api-Token": TOKEN},
        json={"document_id": "doc-1", "correlation_id": "corr-1", "file_url": pdf.as_uri()},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["page_count"] == 1
    assert body["encrypted"] is False
    assert body["pages"][0]["page_number"] == 1
    assert body["pages"][0]["needs_ocr"] is True


def test_extracts_page_blocks(tmp_path, monkeypatch):
    monkeypatch.setattr("app.main.INTERNAL_API_TOKEN", TOKEN)
    pdf = tmp_path / "doc.pdf"
    _make_pdf(pdf, "Door D-14")
    client = TestClient(app)

    response = client.post(
        "/v1/pages/extract",
        headers={"Authorization": f"Bearer {TOKEN}"},
        json={"document_id": "doc-1", "correlation_id": "corr-1", "local_path": str(pdf), "page_number": 1},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["page_number"] == 1
    assert "Door D-14" in body["raw_text"]
    assert body["blocks"][0]["source"] == "TEXT_LAYER"
    assert body["blocks"][0]["bbox"]["coordinate_system"] == "normalized_top_left"


def test_rejects_corrupt_pdf(tmp_path, monkeypatch):
    monkeypatch.setattr("app.main.INTERNAL_API_TOKEN", TOKEN)
    pdf = tmp_path / "bad.pdf"
    pdf.write_text("not a pdf")
    client = TestClient(app)

    response = client.post(
        "/v1/documents/inspect",
        headers={"X-Internal-Api-Token": TOKEN},
        json={"document_id": "doc-1", "correlation_id": "corr-1", "local_path": str(pdf)},
    )

    assert response.status_code == 422
    assert response.json()["detail"]["error"] == "INVALID_PDF"
