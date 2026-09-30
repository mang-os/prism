"""Measure a fresh, full-corpus semantic build without query timing interference."""

import argparse
import hashlib
import json
import os
import platform
import time
from pathlib import Path

import psutil

from prism.embeddings import LocalEmbeddings
from prism.storage import Index


class TimedProvider:
    def __init__(self, delegate):
        self.delegate = delegate
        self.dimension = delegate.dimension
        self.fingerprint = delegate.fingerprint
        self.embedding_ms = 0.0

    def encode_documents(self, texts):
        start = time.perf_counter()
        result = self.delegate.encode_documents(texts)
        self.embedding_ms += (time.perf_counter() - start) * 1000
        return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--documents", required=True)
    parser.add_argument("--model-path", default=".cache/model")
    parser.add_argument("--lexical-only", action="store_true")
    parser.add_argument("--index-root", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    source = Path(args.documents)
    rows = [json.loads(line) for line in source.read_text(encoding="utf-8").splitlines()]
    root = Path(args.index_root)
    if root.exists():
        raise ValueError("Index output already exists; use a new path")
    model = None
    provider = None
    model_load_ms = None
    if not args.lexical_only:
        model_start = time.perf_counter()
        model = LocalEmbeddings(args.model_path)
        model_load_ms = (time.perf_counter() - model_start) * 1000
        provider = TimedProvider(model)
    start = time.perf_counter()
    with Index(
        root,
        create=True,
        dimension=provider.dimension if provider else 0,
        fingerprint=provider.fingerprint if provider else None,
    ) as index:
        for offset in range(0, len(rows), 500):
            index.ingest(rows[offset : offset + 500], provider)
        accepted_ms = (time.perf_counter() - start) * 1000
        index.refresh()
        total_ms = (time.perf_counter() - start) * 1000
        stats = index.stats()
    result = {
        "documents": len(rows),
        "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "model_fingerprint": provider.fingerprint if provider else None,
        "model_load_ms": model_load_ms,
        "embedding_ms_in_ingest": provider.embedding_ms if provider else None,
        "accepted_ms_including_embedding": accepted_ms,
        "refresh_ms": total_ms - accepted_ms,
        "total_ms_including_embedding_and_refresh": total_ms,
        "truncated_documents": model.truncated_documents if model else None,
        "index_stats": stats,
        "index_directory_bytes": sum(p.stat().st_size for p in root.rglob("*") if p.is_file()),
        "process_rss_bytes": psutil.Process().memory_info().rss,
        "platform": platform.platform(),
        "cpu_count": os.cpu_count(),
        "python": platform.python_version(),
        "model_download_excluded": True,
    }
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(output)


if __name__ == "__main__":
    main()
