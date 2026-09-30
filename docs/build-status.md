# Build status

As of 2026-09-30, Prism is a runnable local and static distributed search engine. The original public relevance, OpenSearch comparison, indexing, HTTP load smoke, three later single-client repeats, and synthetic scale probe are recorded. The `v0.1.0` release candidate is being checked against the final CI and repository gates; a tag is not yet claimed here.

| Phase | Status | Evidence and remaining work |
| --- | --- | --- |
| 0 — Scaffold | Implemented | Locked `uv.lock`, Python 3.11 pin, CLI, config, decision record, and Ubuntu/Windows CI. Both CI jobs passed at `4cbea42`; the final documentation commit must also pass. |
| 1 — Analyzer and journal | Implemented | Atomic mutation, validation, restart, and writer-lock tests passed. |
| 2 — Segments and refresh | Implemented | Visibility, pinned reader, checksums, and publication fault tests passed. |
| 3 — Compression and merge | Implemented | Codec, skip, compaction and restart tests passed. Marketplace plain/compressed size comparison measured and recorded. |
| 4 — Parser and BM25 | Implemented | DSL, filters, phrase, ranking, and deadline tests passed. |
| 5 — Fuzzy and synonyms | Implemented with a recorded deviation | Bounded BK-tree for small vocabularies; bounded bigram candidates with exact edit-distance validation for larger vocabularies. The 300-query SciFact run had zero deadline errors; the approximate candidate path can still miss rare typos. |
| 6 — Dense and hybrid | Implemented | Real MiniLM integration passed; marketplace semantic cases and index persistence measured. |
| 7 — HTTP and telemetry | Implemented and measured | API, metrics, refresh, and limits covered by integration tests. The 1,000-request-per-setting HTTP smoke and three later 500-request single-client repeats record p50/p95/p99, throughput, and errors. Reference latency targets remain unmet. |
| 8 — Distributed snapshots | Implemented | Three shards and replicas served in six real subprocesses; failover and partial-response tests passed. |
| 9 — Relevance and comparisons | Measured with stated limits | Marketplace lab, six SciFact ablations, 300-query OpenSearch lexical comparison, first HTTP load smoke, three repeated single-client checks, fresh indexing duration, and 50,000-row synthetic scale are recorded. Optimized rerun matched every top-100 ranking. The aspirational 10,000-request distributions and bounded-arrival test remain open. |
| 10 — GitHub preparation | Release candidate | Public `mang-os/prism` repository, license, security/contributing guidance, README, technical docs, CI, manually triggered heavy workflow, measured summaries, and focused `/demo` page are present. A fresh Git clone passed full setup, 46 tests, wheel build, lexical/typo/semantic/hybrid search, HTTP serving, full-coverage replica failover, and partial coverage. |

Local `scripts/verify.ps1` passed: locked environment setup, Ruff lint/format, mypy, 46 tests including real-model and real-process suites, wheel build, and HTTP search smoke. The fresh clone at `4cbea42` independently installed from `uv.lock`, downloaded the pinned model, passed 46 tests and wheel build, indexed 180 fixture documents, and exercised the documented demo and distributed failover. Local Markdown links resolved. Tracked files contain no model weights, SciFact archive, generated index, build directory, `.env`, or common credential pattern. The isolated wheel build could not reach PyPI from this managed environment; installed build dependencies produced a wheel successfully. The model revision is `1110a243fdf4706b3f48f1d95db1a4f5529b4d41`; the OpenSearch image is `3.3.0` at digest `sha256:d96afaf6cbd2a6a3695aeb2f1d48c9a16ad5c8918eb849e5cbf43475f0f8e146`.

Remaining release action: pass Ubuntu and Windows CI on the final documentation commit, inspect Git hygiene one last time, then create and push `v0.1.0`. The release reports the measured latency/deadline misses. The longer 10,000-request and bounded-arrival performance protocol is a documented limitation, not a measured claim in this release.

