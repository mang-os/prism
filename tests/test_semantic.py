import os
from pathlib import Path

import pytest

from prism.embeddings import LocalEmbeddings
from prism.search import SearchEngine
from prism.storage import Index


@pytest.mark.semantic
def test_real_local_embeddings_nonexact_discovery(tmp_path):
    model = Path(os.environ.get("PRISM_MODEL_PATH", ".cache/model"))
    if not (model / "prism-model.json").exists():
        pytest.skip("Explicitly provision the pinned MiniLM model to run semantic integration")
    provider = LocalEmbeddings(model)
    with Index(
        tmp_path / "semantic",
        create=True,
        dimension=provider.dimension,
        fingerprint=provider.fingerprint,
    ) as index:
        index.ingest(
            [
                {
                    "id": "headphones",
                    "title": "Noise cancelling headphones",
                    "body": "Active circuitry suppresses surrounding noise while playing music.",
                },
                {
                    "id": "repair",
                    "title": "Air conditioner repair",
                    "body": "Technicians diagnose broken cooling systems.",
                },
                {
                    "id": "chair",
                    "title": "Wooden dining chair",
                    "body": "Furniture for eating at a table.",
                },
            ],
            provider,
        )
        index.refresh()
        engine = SearchEngine(index, provider)
        assert (
            engine.search(
                {
                    "q": "quiet audio accessory that isolates me from busy surroundings",
                    "mode": "semantic",
                    "timeout_ms": 5000,
                }
            )["hits"][0]["id"]
            == "headphones"
        )
        assert (
            engine.search(
                {
                    "q": "my home is hot because the AC stopped working",
                    "mode": "hybrid",
                    "timeout_ms": 5000,
                }
            )["hits"][0]["id"]
            == "repair"
        )
