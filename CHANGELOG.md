# Changelog

## 0.1.0 — 2026-09-30

- Segmented lexical index, compressed postings, durable journal and refresh, merge, DSL, BM25, fuzzy search, synonyms.
- Local MiniLM embeddings, exact vector retrieval, hybrid fusion, HTTP and CLI interfaces.
- Static three-shard export with snapshot replicas, global scoring, failover, and partial results.
- Judged marketplace fixture and SciFact/OpenSearch benchmark workflow. See [measured status](docs/benchmark-report.md).
- Local search demo, canonical Windows verification script, Ubuntu/Windows CI, and manually triggered heavy benchmark workflow.
- SciFact exact lexical nDCG@10 0.5857 and hybrid nDCG@10 0.6758 on 300 test queries; OpenSearch lexical baseline 0.5810. Repeated HTTP latency, error rates, and known performance misses are recorded in the benchmark report.

This initial release is an educational reference. Static cluster snapshots, exact vector scan, high-concurrency deadline/overload errors, and the absence of public-facing authentication are documented in [limitations](docs/limitations.md).

