from __future__ import annotations

import json
import hashlib
import os
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path
from typing import Any, Optional


ROOT = Path(__file__).resolve().parents[2]
MANIFEST = ROOT / "demo" / "expected-manifest.json"
API_BASE = os.getenv("E2E_API_BASE_URL", "http://localhost/api/v1").rstrip("/")
TIMEOUT = int(os.getenv("E2E_TIMEOUT_SECONDS", "180"))


def request(path_or_url: str, method: str = "GET", body: Any = None, headers: Optional[dict[str, str]] = None) -> Any:
    url = path_or_url if path_or_url.startswith(("http://", "https://")) else f"{API_BASE}{path_or_url}"
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={"Content-Type": "application/json", **(headers or {})},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            payload = resp.read()
            content_type = resp.headers.get("content-type", "")
            if payload and "json" not in content_type:
                excerpt = payload[:200].decode(errors="replace")
                raise AssertionError(f"{method} {url} -> non-JSON {content_type}: {excerpt}")
            return json.loads(payload or b"{}")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")
        raise AssertionError(f"{method} {url} -> HTTP {exc.code}: {detail}") from exc


def envelope(payload: Any) -> Any:
    if isinstance(payload, dict) and "error" in payload and payload["error"]:
        raise AssertionError(payload["error"])
    return payload.get("data") if isinstance(payload, dict) and "data" in payload else payload


def upload(ticket: dict[str, Any], file_path: Path, content_type: str) -> None:
    upload_url = ticket["upload_url"]
    if upload_url.startswith("/"):
        parsed = urllib.parse.urlparse(API_BASE)
        upload_url = f"{parsed.scheme}://{parsed.netloc}{upload_url}"
    req = urllib.request.Request(
        upload_url,
        data=file_path.read_bytes(),
        method=ticket.get("method", "PUT"),
        headers={"Content-Type": content_type, **ticket.get("required_headers", {})},
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        if not 200 <= resp.status < 300:
            raise AssertionError(f"upload {file_path.name} -> HTTP {resp.status}")


def items(payload: Any) -> list[dict[str, Any]]:
    data = envelope(payload)
    if isinstance(data, list):
        return data
    if isinstance(data, dict) and isinstance(data.get("items"), list):
        return data["items"]
    raise AssertionError(f"Expected list/items payload, got {data!r}")


def assert_stats(inspection: dict[str, Any], manifest: dict[str, Any]) -> None:
    stats = inspection.get("stats") or {}
    expected = manifest["expected_stats"]
    assert stats.get("documents", 0) >= expected["documents"], stats
    assert stats.get("pages", 0) >= expected["pages"], stats
    assert stats.get("findings", 0) >= expected["findings_min"], stats
    assert stats.get("ocr_pages", 0) >= 1, stats
    assert stats.get("entities", 0) >= 4, stats
    assert stats.get("matches", 0) >= 3, stats


def finding_text(finding: dict[str, Any]) -> str:
    return json.dumps(finding, ensure_ascii=False, sort_keys=True)


def walk(value: Any) -> list[Any]:
    values = [value]
    if isinstance(value, dict):
        for item in value.values():
            values.extend(walk(item))
    elif isinstance(value, list):
        for item in value:
            values.extend(walk(item))
    return values


def assert_evidence(detail: dict[str, Any]) -> None:
    evidence = detail.get("evidence")
    assert isinstance(evidence, list) and evidence, detail
    assert any("bbox" in item for item in walk(evidence) if isinstance(item, dict)), evidence
    assert any(("page_number" in item or "page" in item) for item in walk(evidence) if isinstance(item, dict)), evidence


def assert_expected_findings(findings: list[dict[str, Any]], manifest: dict[str, Any]) -> dict[str, Any]:
    for expected in manifest["expected_findings"]:
        matching = [item for item in findings if all(item.get(key) == expected[key] for key in
                    ("finding_type", "entity_type", "field_name", "expected_value", "actual_value"))]
        assert matching, f"Missing exact expected finding: {expected!r} in {findings!r}"
        detail = envelope(request(f"/findings/{matching[0]['id']}"))
        assert_evidence(detail)
        by_side = {item["side"]: item for item in detail["evidence"]}
        for side, quote_key in (("EXPECTED", "expected_quote"), ("ACTUAL", "actual_quote")):
            if expected[quote_key] is not None:
                assert by_side[side]["quote"] == expected[quote_key], detail
                assert by_side[side]["page_number"] > 0, detail
                assert by_side[side]["download_url"].startswith("/api/v1/documents/"), detail
    return findings[0]


def restart_stack() -> None:
    if os.getenv("E2E_SKIP_RESTART") == "1":
        return
    subprocess.run(["docker", "compose", "restart", "n8n-main", "n8n-worker"], cwd=ROOT, check=True)
    time.sleep(10)


def main() -> None:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    health = envelope(request("/health"))
    assert health.get("status") in {"ok", "ready"}, health

    project = envelope(
        request(
            "/projects",
            "POST",
            {"name": f"{manifest['project_name']} {uuid.uuid4()}"},
            {"Idempotency-Key": str(uuid.uuid4())},
        )
    )
    project_id = project["id"]
    assert envelope(request(f"/projects/{project_id}"))["name"] == project["name"]
    assert any(item["id"] == project_id for item in envelope(request("/projects")))

    for doc in manifest["documents"]:
        file_path = ROOT / "demo" / doc["path"]
        ticket = envelope(
            request(
                f"/projects/{project_id}/documents/uploads",
                "POST",
                {
                    "filename": doc["filename"],
                    "content_type": doc["content_type"],
                    "size_bytes": file_path.stat().st_size,
                    "stage": doc["stage"],
                },
            )
        )
        upload(ticket, file_path, doc["content_type"])
        envelope(request(f"/documents/{ticket['document_id']}/confirm", "POST", {}))
        signed = envelope(request(f"/documents/{ticket['document_id']}/download-url"))
        signed_url = signed["url"]
        if signed_url.startswith("/"):
            parsed = urllib.parse.urlparse(API_BASE)
            signed_url = f"{parsed.scheme}://{parsed.netloc}{signed_url}"
        with urllib.request.urlopen(signed_url, timeout=30) as response:
            assert hashlib.sha256(response.read()).hexdigest() == doc["sha256"]

    started = envelope(
        request(
            f"/projects/{project_id}/inspections",
            "POST",
            {},
            {"Idempotency-Key": str(uuid.uuid4())},
        )
    )
    inspection_id = started["inspection_id"]

    deadline = time.time() + TIMEOUT
    while True:
        inspection = envelope(request(f"/inspections/{inspection_id}"))
        if inspection["status"] in {"COMPLETED", "COMPLETED_WITH_WARNINGS"}:
            break
        assert inspection["status"] not in {"FAILED", "CANCELLED"}, inspection
        assert time.time() < deadline, inspection
        time.sleep(2)

    assert_stats(inspection, manifest)
    found = items(request(f"/inspections/{inspection_id}/findings"))
    finding = assert_expected_findings(found, manifest)
    report = envelope(request(f"/inspections/{inspection_id}/export"))
    assert report["inspection"]["id"] == inspection_id
    assert len(report["documents"]) == 3
    assert len(report["findings"]) >= manifest["expected_stats"]["findings_min"]

    detail = envelope(request(f"/findings/{finding['id']}"))
    assert_evidence(detail)

    reviewed = envelope(
        request(
            f"/findings/{finding['id']}/reviews",
            "POST",
            {"decision": "CONFIRMED", "comment": "E2E demo review"},
            {"Idempotency-Key": str(uuid.uuid4())},
        )
    )
    assert reviewed.get("status") == "CONFIRMED" or reviewed.get("decision") == "CONFIRMED", reviewed

    restart_stack()

    persisted = envelope(request(f"/findings/{finding['id']}"))
    assert "CONFIRMED" in finding_text(persisted), persisted
    print(json.dumps({"project_id": project_id, "inspection_id": inspection_id, "finding_id": finding["id"]}, indent=2))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"E2E failed: {exc}", file=sys.stderr)
        raise
