# Build status

As of 2026-09-30, Prism is a runnable local and static distributed search engine. Public relevance and the 1,000-request HTTP smoke are measured; the longer final performance protocol and hosted CI remain open. This page records direct evidence, not a release claim.

| Phase | Status | Evidence and remaining work |
| --- | --- | --- |
| 0 — Scaffold | Implemented | Locked `uv.lock`, CLI, config, CI, and decision record; clean source-copy locked lexical install and wheel build succeeded. Hosted CI has not run. |
| 1 — Analyzer and journal | Implemented | Atomic mutation, validation, restart, and writer-lock tests passed. |
| 2 — Segments and refresh | Implemented | Visibility, pinned reader, checksums, and publication fault tests passed. |
| 3 — Compression and merge | Implemented | Codec, skip, compaction and restart tests passed. Marketplace plain/compressed size comparison measured and recorded. |
| 4 — Parser and BM25 | Implemented | DSL, filters, phrase, ranking, and deadline tests passed. |
| 5 — Fuzzy and synonyms | Implemented with a recorded deviation | Bounded BK-tree for small vocabularies; bounded bigram candidates with exact edit-distance validation for larger vocabularies. The 300-query SciFact run had zero deadline errors; the approximate candidate path can still miss rare typos. |
| 6 — Dense and hybrid | Implemented | Real MiniLM integration passed; marketplace semantic cases and index persistence measured. |
| 7 — HTTP and telemetry | Implemented and measured | API, metrics, refresh, and limits covered by integration tests. The 1,000-request-per-setting HTTP smoke is recorded with p50/p95/p99 and errors; single-client latency targets were missed. |
| 8 — Distributed snapshots | Implemented | Three shards and replicas served in six real subprocesses; failover and partial-response tests passed. |
| 9 — Relevance and comparisons | Measured with remaining protocol gaps | Marketplace lab, six SciFact ablations, 300-query OpenSearch lexical comparison, HTTP load smoke, fresh indexing duration, and 50,000-row synthetic scale are recorded. Optimized rerun matched every top-100 ranking. Three repeated 10,000-request distributions and bounded-arrival tests remain. |
| 10 — GitHub preparation | Local preparation complete | README, API/storage/query/operations docs, license, CI, machine-readable summaries, clean source-copy demo, wheel, and final regression are present. Git is initialized on `main`; no commit, remote, tag, or hosted CI run exists. |

Local checks completed: 46 tests passed (including real-model and real-process suites); Ruff lint and format, mypy, and a non-isolated wheel build passed. A source-only copy installed from `uv.lock`, created a lexical index, accepted 180 fixture documents, refreshed revision 180, and found Bluetooth earbuds from a misspelled query. Thirty local Markdown links resolved. Git hygiene excluded model assets, downloaded corpus, run artifacts, and the clean test environment; a common credential-pattern scan found no matches. The isolated wheel build could not reach PyPI from this managed environment; the local installed build dependencies produced a wheel successfully. The model is pinned to revision `1110a243fdf4706b3f48f1d95db1a4f5529b4d41`; the OpenSearch image is `3.3.0` at digest `sha256:d96afaf6cbd2a6a3695aeb2f1d48c9a16ad5c8918eb849e5cbf43475f0f8e146`.

Next release gates: run hosted CI after a GitHub remote and initial commit, run the repeated 10,000-request and bounded-arrival performance protocol if a formal `v0.1.0` performance release is required, and address or explicitly accept the documented latency/deadline misses. The local project is ready for owner review, but the specification's full definition of done is not yet met.

