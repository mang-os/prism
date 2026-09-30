from fastapi.testclient import TestClient

from prism.api import create_app
from prism.config import Settings


def test_local_http_lifecycle(tmp_path):
    with TestClient(
        create_app(
            Settings(
                data=str(tmp_path), synonyms_path=None, model_path=str(tmp_path / "absent-model")
            )
        )
    ) as client:
        assert client.get("/health/live").status_code == 200
        assert client.post("/v1/indexes", json={"name": "demo"}).status_code == 201
        assert client.post("/v1/indexes", json={"name": "../escape"}).status_code == 400
        response = client.post(
            "/v1/indexes/demo/documents:bulk",
            json={"documents": [{"id": "a", "title": "Bluetooth earbuds"}]},
        )
        revision = response.json()["accepted_revision"]
        assert client.post("/v1/indexes/demo/search", json={"q": "earbuds"}).json()["hits"] == []
        assert (
            client.post("/v1/indexes/demo/refresh", json={"through_revision": revision}).status_code
            == 200
        )
        assert (
            client.post("/v1/indexes/demo/search", json={"q": "bluetooh"}).json()["hits"][0]["id"]
            == "a"
        )
        assert (
            client.post(
                "/v1/indexes/demo/search", json={"q": "x AND", "query_mode": "dsl"}
            ).status_code
            == 400
        )
        assert (
            client.post(
                "/v1/indexes/demo/search",
                json={"q": "earbuds", "filters": [{"field": "rating", "value": "4"}]},
            ).status_code
            == 422
        )
        assert client.delete("/v1/indexes/demo/documents/a").status_code == 200
        client.post("/v1/indexes/demo/refresh", json={})
        assert not client.post("/v1/indexes/demo/search", json={"q": "earbuds"}).json()["hits"]
        metrics = client.get("/metrics").content
        assert b"prism_request_seconds" in metrics
        assert b"prism_index_bytes" in metrics
        assert b"prism_pending_revisions" in metrics
        assert (
            client.post("/v1/indexes/demo/search", content=b"x" * (8 * 1024 * 1024 + 1)).status_code
            == 413
        )
