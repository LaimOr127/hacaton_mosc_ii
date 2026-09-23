from __future__ import annotations

import hashlib
import json
import zlib
from pathlib import Path
from typing import Optional

from PIL import Image, ImageDraw, ImageFont


ROOT = Path(__file__).resolve().parent
OUT = ROOT / "fixtures"


DOCUMENTS = [
    {
        "stage": "PROJECT",
        "filename": "project.pdf",
        "title": "Project design",
        "scan_like": False,
        "lines": [
            "Construction Inspection Demo",
            "Stage: PROJECT",
            "Door D-14: 900 x 2100 mm",
            "Window OK-2: 1200 x 1500 mm, quantity 2",
            "Wall S-1: aerated concrete, thickness 200 mm",
        ],
    },
    {
        "stage": "WORKING",
        "filename": "working.pdf",
        "title": "Working documentation",
        "scan_like": False,
        "lines": [
            "Construction Inspection Demo",
            "Stage: WORKING",
            "Door D-14: 900 x 2100 mm",
            "Window OK-2: 1200 x 1500 mm, quantity 2",
            "Wall S-1: aerated concrete, thickness 150 mm",
        ],
    },
    {
        "stage": "AS_BUILT",
        "filename": "as_built_scan_like.pdf",
        "title": "As-built scan-like document",
        "scan_like": True,
        "lines": [
            "Construction Inspection Demo",
            "Stage: AS_BUILT",
            "Door D-14: 800 x 2100 mm",
            "Window OK-2: quantity 1",
            "Wall S-1: aerated concrete, thickness 150 mm",
            "Extra door D-99",
        ],
    },
]


EXPECTED_FINDINGS = [
    {
        "finding_type": "VALUE_CHANGED",
        "entity_type": "WALL",
        "field_name": "thickness_mm",
        "expected_value": 200,
        "actual_value": 150,
        "expected_quote": "Wall S-1: aerated concrete, thickness 200 mm",
        "actual_quote": "Wall S-1: aerated concrete, thickness 150 mm",
    },
    {
        "finding_type": "VALUE_CHANGED",
        "entity_type": "DOOR",
        "field_name": "width_mm",
        "expected_value": 900,
        "actual_value": 800,
        "expected_quote": "Door D-14: 900 x 2100 mm",
        "actual_quote": "Door D-14: 800 x 2100 mm",
    },
    {
        "finding_type": "COUNT_CHANGED",
        "entity_type": "WINDOW",
        "field_name": "count",
        "expected_value": 2,
        "actual_value": 1,
        "expected_quote": "Window OK-2: 1200 x 1500 mm, quantity 2",
        "actual_quote": "Window OK-2: quantity 1",
    },
    {
        "finding_type": "EXTRA_ENTITY",
        "entity_type": "DOOR",
        "field_name": "entity",
        "expected_value": None,
        "actual_value": "D-99",
        "expected_quote": None,
        "actual_quote": "Extra door D-99",
    },
]


def pdf_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def write_pdf(path: Path, content: bytes, resources: str = "<< /Font << /F1 5 0 R >> >>", extra: Optional[list[bytes]] = None) -> None:
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Resources {resources} /Contents 4 0 R >>".encode(),
        b"<< /Length " + str(len(content)).encode() + b" >>\nstream\n" + content + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    if extra:
        objects.extend(extra)

    out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = [0]
    for index, obj in enumerate(objects, start=1):
        offsets.append(len(out))
        out.extend(f"{index} 0 obj\n".encode())
        out.extend(obj)
        out.extend(b"\nendobj\n")
    xref = len(out)
    out.extend(f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode())
    for offset in offsets[1:]:
        out.extend(f"{offset:010d} 00000 n \n".encode())
    out.extend(f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode())
    path.write_bytes(out)


def make_text_pdf(path: Path, title: str, lines: list[str]) -> None:
    commands = ["BT", "/F1 18 Tf", "72 770 Td", f"({pdf_escape(title)}) Tj", "/F1 12 Tf"]
    for line in lines:
        commands.extend(["0 -28 Td", f"({pdf_escape(line)}) Tj"])
    commands.append("ET")
    write_pdf(path, "\n".join(commands).encode("utf-8"))


def make_scan_like_pdf(path: Path) -> None:
    lines = next(item["lines"] for item in DOCUMENTS if item["stage"] == "AS_BUILT")
    width, height = 1600, 1000
    image = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(image)
    font = scan_font(54)
    y = 100
    for line in lines:
        draw.text((110, y), line, fill=(20, 20, 20), font=font)
        y += 110

    pixels = zlib.compress(image.tobytes(), level=9)
    xobject = (
        f"<< /Type /XObject /Subtype /Image /Width {width} /Height {height} "
        "/ColorSpace /DeviceRGB /BitsPerComponent 8 /Filter /FlateDecode /Length "
    ).encode() + str(len(pixels)).encode() + b" >>\nstream\n" + pixels + b"\nendstream"
    content = b"q\n520 0 0 325 38 440 cm\n/Im1 Do\nQ"
    resources = "<< /XObject << /Im1 6 0 R >> >>"
    write_pdf(path, content, resources, [xobject])


def scan_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for candidate in (
        "/System/Library/Fonts/Supplemental/Arial.ttf",
        "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf",
    ):
        if Path(candidate).exists():
            return ImageFont.truetype(candidate, size=size)
    return ImageFont.load_default()


def make_pdf(spec: dict) -> None:
    path = OUT / spec["filename"]
    if spec["scan_like"]:
        make_scan_like_pdf(path)
    else:
        make_text_pdf(path, spec["title"], spec["lines"])


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for spec in DOCUMENTS:
        make_pdf(spec)

    manifest = {
        "project_name": "Demo construction inspection",
        "documents": [
            {
                "stage": spec["stage"],
                "filename": spec["filename"],
                "path": f"fixtures/{spec['filename']}",
                "content_type": "application/pdf",
                "sha256": sha256(OUT / spec["filename"]),
                "scan_like": spec["scan_like"],
                "expected_pages": 1,
            }
            for spec in DOCUMENTS
        ],
        "expected_stats": {
            "documents": 3,
            "pages": 3,
            "findings_min": len(EXPECTED_FINDINGS),
        },
        "expected_findings": EXPECTED_FINDINGS,
    }
    (ROOT / "expected-manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
