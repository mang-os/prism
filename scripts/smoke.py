"""Minimal HTTP lifecycle and typo-search smoke for CI and local verification."""

import tempfile
from pathlib import Path

from fastapi.testclient import TestClient

from prism.api import create_app
from prism.config import Settings


def main():
    work = Path("work")
    work.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="prism-smoke-", dir=work) as directory:
        settings = Settings(
            data=directory,
            model_path=str(Path(directory) / "missing-model"),
            synonyms_path=None,
        )
        with TestClient(create_app(settings)) as client:
            assert client.get("/health/live").status_code == 200
            assert client.post("/v1/indexes", json={"name": "smoke"}).status_code == 201
            documents = [
                {
                    "id": "headphones",
                    "title": "Bluetooth headphones",
                    "body": "Wireless earphones that block noise while travelling.",
                },
                {"id": "repair", "title": "Air conditioner repair service"},
            ]
            accepted = client.post(
                "/v1/indexes/smoke/documents:bulk", json={"documents": documents}
            )
            accepted.raise_for_status()
            revision = accepted.json()["accepted_revision"]
            refreshed = client.post(
                "/v1/indexes/smoke/refresh", json={"through_revision": revision}
            )
            refreshed.raise_for_status()
            response = client.post(
                "/v1/indexes/smoke/search",
                json={"q": "bluetooh hedphones", "mode": "lexical"},
            )
            response.raise_for_status()
            assert response.json()["hits"][0]["id"] == "headphones"
            assert response.json()["coverage"] == {"successful": 1, "total": 1}
    print("Prism HTTP ingest, refresh, and typo-search smoke passed")


if __name__ == "__main__":
    main()
