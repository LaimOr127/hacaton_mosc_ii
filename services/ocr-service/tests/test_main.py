from fastapi.testclient import TestClient
from PIL import Image

from ocr_service import main


def test_token_is_required(monkeypatch):
    monkeypatch.setattr(main.settings, "internal_api_token", "secret")
    response = TestClient(main.app).post("/v1/pages/recognize", json={})
    assert response.status_code == 401


def test_recognize_page(monkeypatch):
    monkeypatch.setattr(main.settings, "internal_api_token", "secret")
    monkeypatch.setattr(main, "load_object", lambda bucket, key: b"%PDF")
    monkeypatch.setattr(main, "render_pdf_page", lambda pdf, page, dpi: Image.new("RGB", (100, 100)))
    monkeypatch.setattr(main, "tesseract_version", lambda: "5.5.0")
    monkeypatch.setattr(
        main,
        "recognize_image",
        lambda image, languages, timeout: (
            "Д-14",
            [
                main.OcrBlock(
                    order=1,
                    text="Д-14",
                    bbox=main.BBox(x0=0.1, y0=0.2, x1=0.3, y1=0.4),
                    confidence=0.94,
                )
            ],
        ),
    )
    response = TestClient(main.app).post(
        "/v1/pages/recognize",
        headers={"X-Internal-Token": "secret"},
        json={
            "document_id": "00000000-0000-0000-0000-000000000000",
            "bucket": "documents",
            "storage_key": "projects/a/file.pdf",
            "page_number": 1,
            "languages": ["rus", "eng"],
            "dpi": 300,
            "correlation_id": "c1",
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["raw_text"] == "Д-14"
    assert body["blocks"][0]["source"] == "OCR"
    assert body["engine"] == "tesseract"
