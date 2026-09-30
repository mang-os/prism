"""Clearly synthetic 50k-row scaling probe; vectors are not semantic evidence."""

import argparse
import json
import os
import platform
import time
from pathlib import Path

import numpy as np
import psutil

from prism.search import SearchEngine
from prism.storage import Index


class SyntheticVectors:
    fingerprint = "SYNTHETIC-BENCHMARK-NOT-A-MODEL"
    dimension = 384

    def __init__(self):
        self.rng = np.random.default_rng(23)

    def encode_documents(self, texts):
        rows = self.rng.standard_normal((len(texts), self.dimension), dtype=np.float32)
        return rows / np.linalg.norm(rows, axis=1, keepdims=True)

    def encode_query(self, text):
        row = np.ones(self.dimension, dtype=np.float32)
        return row / np.linalg.norm(row)


def measure(engine, mode, count):
    timings = []
    for i in range(count + 20):
        start = time.perf_counter()
        engine.search(
            {
                "q": f"synthetic maintenance topic{i % 100}",
                "mode": mode,
                "top_k": 10,
                "candidate_k": 100,
                "timeout_ms": 5000,
                "typo_tolerance": False,
                "synonyms": False,
            }
        )
        if i >= 20:
            timings.append((time.perf_counter() - start) * 1000)
    return {
        "queries": count,
        "p50_ms": float(np.percentile(timings, 50)),
        "p95_ms": float(np.percentile(timings, 95)),
        "p99_ms": float(np.percentile(timings, 99)),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--index-root", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--documents", type=int, default=50000)
    parser.add_argument("--queries", type=int, default=100)
    args = parser.parse_args()
    root = Path(args.index_root)
    if root.exists():
        raise ValueError("Index output already exists; use a new path")
    provider = SyntheticVectors()
    start = time.perf_counter()
    with Index(
        root, create=True, dimension=provider.dimension, fingerprint=provider.fingerprint
    ) as index:
        for offset in range(0, args.documents, 500):
            rows = [
                {
                    "id": f"synthetic-{i:06d}",
                    "title": f"Synthetic maintenance topic{i % 100}",
                    "body": f"Synthetic document about repair workflow and topic{i % 100}.",
                }
                for i in range(offset, min(offset + 500, args.documents))
            ]
            index.ingest(rows, provider)
        accepted_ms = (time.perf_counter() - start) * 1000
        index.refresh()
        build_ms = (time.perf_counter() - start) * 1000
        engine = SearchEngine(index, provider)
        results = {
            mode: measure(engine, mode, args.queries) for mode in ("lexical", "semantic", "hybrid")
        }
        stats = index.stats()
    report = {
        "synthetic_only": True,
        "no_relevance_claim": True,
        "documents": args.documents,
        "dimension": provider.dimension,
        "accepted_ms": accepted_ms,
        "build_ms": build_ms,
        "results": results,
        "index_stats": stats,
        "index_directory_bytes": sum(p.stat().st_size for p in root.rglob("*") if p.is_file()),
        "process_rss_bytes": psutil.Process().memory_info().rss,
        "platform": platform.platform(),
        "python": platform.python_version(),
        "cpu_count": os.cpu_count(),
    }
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(output)


if __name__ == "__main__":
    main()
