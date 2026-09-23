from types import SimpleNamespace

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


def test_ocr_words_are_grouped_into_lines(monkeypatch):
    monkeypatch.setattr(
        main.pytesseract,
        "image_to_data",
        lambda *args, **kwargs: {
            "text": ["Door", "D-14:", "800", "mm", "Wall", "S-1:", "150", "mm"],
            "conf": [90] * 8,
            "left": [10, 60, 130, 180, 10, 60, 130, 180],
            "top": [10] * 4 + [60] * 4,
            "width": [40] * 8,
            "height": [20] * 8,
            "block_num": [1] * 8,
            "par_num": [1] * 8,
            "line_num": [1] * 4 + [2] * 4,
        },
    )

    raw_text, blocks = main.recognize_image(Image.new("RGB", (300, 100)), "eng", 10)

    assert len(blocks) == 2
    assert blocks[0].text == "Door D-14: 800 mm"
    assert blocks[1].text == "Wall S-1: 150 mm"
    assert blocks[0].bbox.x0 == 10 / 300
    assert raw_text == "Door D-14: 800 mm\nWall S-1: 150 mm"


class _ObjectResponse:
    def __init__(self, data: bytes):
        self.data = data

    def read(self, amount: int) -> bytes:
        return self.data[:amount]

    def close(self) -> None:
        pass

    def release_conn(self) -> None:
        pass


class _MinioStub:
    def __init__(self, data: bytes, size: int | None = None):
        self.data = data
        self.size = len(data) if size is None else size
        self.calls: list[tuple[str, str, str]] = []

    def stat_object(self, bucket: str, storage_key: str):
        self.calls.append(("stat", bucket, storage_key))
        return SimpleNamespace(size=self.size)

    def get_object(self, bucket: str, storage_key: str):
        self.calls.append(("get", bucket, storage_key))
        return _ObjectResponse(self.data)


def test_load_object_reads_minio_with_limit(monkeypatch):
    store = _MinioStub(b"pdf")
    monkeypatch.setattr(main, "minio_client", lambda: store)
    monkeypatch.setattr(main.settings, "max_pdf_bytes", 10)

    assert main.load_object("documents", "a/doc.pdf") == b"pdf"
    assert store.calls == [("stat", "documents", "a/doc.pdf"), ("get", "documents", "a/doc.pdf")]


def test_load_object_rejects_oversize_before_download(monkeypatch):
    store = _MinioStub(b"too large", size=100)
    monkeypatch.setattr(main, "minio_client", lambda: store)
    monkeypatch.setattr(main.settings, "max_pdf_bytes", 10)

    try:
        main.load_object("documents", "big.pdf")
    except main.HTTPException as exc:
        assert exc.status_code == 413
    else:
        raise AssertionError("expected oversize PDF rejection")
    assert store.calls == [("stat", "documents", "big.pdf")]
