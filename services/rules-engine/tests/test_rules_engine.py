from fastapi.testclient import TestClient

from rules_engine import main


TOKEN = "test-token"


def block(text: str, order: int = 1) -> dict:
    return {
        "id": f"b{order}",
        "order": order,
        "text": text,
        "bbox": {"x0": 0.1, "y0": 0.2, "x1": 0.8, "y1": 0.25, "coordinate_system": "normalized_top_left"},
        "confidence": 0.98,
        "source": "TEXT_LAYER",
    }


def extract(client: TestClient, document_id: str, stage: str, *texts: str) -> list[dict]:
    response = client.post(
        "/v1/text/entities",
        headers={"X-Internal-Api-Token": TOKEN},
        json={
            "document_id": document_id,
            "stage": stage,
            "pages": [{"page_id": f"{document_id}-p1", "page_number": 1, "blocks": [block(text, i + 1) for i, text in enumerate(texts)]}],
        },
    )
    assert response.status_code == 200
    return response.json()["entities"]


def test_health_endpoints():
    client = TestClient(main.app)
    assert client.get("/health/live").json() == {"status": "ok"}
    assert client.get("/health/ready").json() == {"status": "ready"}


def test_token_is_required(monkeypatch):
    monkeypatch.setattr(main, "INTERNAL_API_TOKEN", TOKEN)
    client = TestClient(main.app)

    response = client.post("/v1/text/entities", json={})

    assert response.status_code == 401


def test_extracts_demo_entities_with_units_and_provenance(monkeypatch):
    monkeypatch.setattr(main, "INTERNAL_API_TOKEN", TOKEN)
    client = TestClient(main.app)
    entities = extract(
        client,
        "project-doc",
        "PROJECT",
        "Д 14: 0.9 × 2.1 м, материал сталь, этаж 2, помещение 201",
        "ОК-2: 1200х1500 мм, количество 2",
        "Стена С-1: газобетон, толщина 200 мм",
    )

    by_key = {entity["canonical_key"]: entity for entity in entities}
    assert by_key["DOOR:Д-14"]["attributes"]["width_mm"]["normalized"] == 900
    assert by_key["DOOR:Д-14"]["attributes"]["height_mm"]["normalized"] == 2100
    assert by_key["DOOR:Д-14"]["attributes"]["room"]["normalized"] == "201"
    assert by_key["WINDOW:ОК-2"]["attributes"]["count"]["normalized"] == 2
    assert by_key["WALL:С-1"]["attributes"]["material"]["normalized"] == "газобетон"
    assert by_key["WALL:С-1"]["attributes"]["thickness_mm"]["normalized"] == 200
    assert by_key["WALL:С-1"]["evidence"]["page_number"] == 1
    assert by_key["WALL:С-1"]["evidence"]["bbox"]["coordinate_system"] == "normalized_top_left"


def test_compare_returns_expected_demo_findings_deterministically(monkeypatch):
    monkeypatch.setattr(main, "INTERNAL_API_TOKEN", TOKEN)
    client = TestClient(main.app)
    expected = extract(
        client,
        "project-doc",
        "PROJECT",
        "Д-14: 900 × 2100 мм, материал сталь, этаж 2, помещение 201",
        "ОК-2: 1200 × 1500 мм, количество 2",
        "Стена С-1: газобетон, толщина 200 мм",
    )
    actual = extract(
        client,
        "asbuilt-doc",
        "AS_BUILT",
        "Д-14: 800 × 2100 мм",
        "ОК-2: количество 1",
        "Стена С-1: газобетон, толщина 150 мм",
        "Дополнительная дверь Д-99",
    )

    first = client.post(
        "/v1/compare/findings",
        headers={"X-Internal-Api-Token": TOKEN},
        json={"expected_entities": expected, "actual_entities": actual},
    )
    second = client.post(
        "/v1/compare/findings",
        headers={"X-Internal-Api-Token": TOKEN},
        json={"expected_entities": expected, "actual_entities": actual},
    )

    assert first.status_code == 200
    assert first.json() == second.json()

    findings = {finding["dedup_key"]: finding for finding in first.json()["findings"]}
    assert findings["VALUE_CHANGED|DOOR|Д-14|width_mm"]["severity"] == "HIGH"
    assert findings["VALUE_CHANGED|WALL|С-1|thickness_mm"]["actual_value"] == 150
    assert findings["COUNT_CHANGED|WINDOW|ОК-2|count"]["severity"] == "HIGH"
    assert findings["EXTRA_ENTITY|DOOR|Д-99"]["finding_type"] == "EXTRA_ENTITY"
    assert findings["EXTRA_ENTITY|DOOR|Д-99"]["field_name"] == "entity"
    assert findings["EXTRA_ENTITY|DOOR|Д-99"]["actual_value"] == "Д-99"
    assert findings["EXTRA_ENTITY|DOOR|Д-99"]["evidence"][0]["page_number"] == 1


def test_english_e2e_fixture_patterns(monkeypatch):
    monkeypatch.setattr(main, "INTERNAL_API_TOKEN", TOKEN)
    client = TestClient(main.app)
    expected = extract(
        client, "project-doc", "PROJECT",
        "Door D-14: 900 x 2100 mm",
        "Window OK-2: 1200 x 1500 mm, quantity 2",
        "Wall S-1: aerated concrete, thickness 200 mm",
    )
    actual = extract(
        client, "as-built-doc", "AS_BUILT",
        "Door D-14: 800 x 2100 mm",
        "Window OK-2: quantity 1",
        "Wall S-1: aerated concrete, thickness 150 mm",
        "Extra door D-99",
    )
    response = client.post(
        "/v1/compare/findings",
        headers={"X-Internal-Api-Token": TOKEN},
        json={"expected_entities": expected, "actual_entities": actual},
    )
    assert response.status_code == 200
    keys = {finding["dedup_key"] for finding in response.json()["findings"]}
    assert {
        "VALUE_CHANGED|DOOR|Д-14|width_mm",
        "COUNT_CHANGED|WINDOW|ОК-2|count",
        "VALUE_CHANGED|WALL|С-1|thickness_mm",
        "EXTRA_ENTITY|DOOR|Д-99",
    }.issubset(keys)
