# Prism

Prism is an educational search engine for products, services, and documents. It combines a custom persistent keyword index with local embeddings, so a search for “my air conditioner is not cooling” can retrieve an AC repair service. It searches only documents you provide.

**Status:** Local search, real MiniLM retrieval, and a static snapshot cluster have been implemented and tested. See [build status](docs/build-status.md) and [measured results](docs/benchmark-report.md). The [build specification](PRISM_BUILD_SPEC.md) remains the release contract. Production readiness is not claimed.

| Capability | Implementation | Evidence |
| --- | --- | --- |
| Segmented index, updates/deletes, crash-safe publication, compression | Custom binary files plus SQLite mutation journal | Codec and fault-boundary tests |
| Phrases, Boolean DSL, filters, BM25, bounded fuzzy terms and synonyms | Local engine | Reference tests and judged fixture |
| Semantic retrieval and hybrid RRF | Local MiniLM model, exact vector scan | Real-model test and marketplace report |
| Distributed queries | Three static shards, one snapshot replica each | Real-process failover test |
| External baseline | OpenSearch BM25 on SciFact | [Benchmark status](docs/benchmark-report.md) |

## How it works

```text
Your documents -> journal -> immutable compressed segments -> live snapshot
                                                           | keyword/BM25
Query -> parser and filters -> keyword + vector candidates -> global RRF -> results
```

`plain` queries can find semantically similar items without matching words. `dsl` queries enforce exact phrases, Boolean expressions, and field/metadata conditions. Hybrid ranking never bypasses a hard constraint. Scores express rank, not confidence.

## Install

Use Python 3.11 and [uv](https://docs.astral.sh/uv/). The model needs about 2 GiB of available memory. Docker is needed only for the OpenSearch comparison.

```bash
uv sync --locked --extra dev --extra semantic
uv run prism model download
```

On Windows hosts that block temporary build helpers, this two-step install was verified:

```powershell
uv sync --locked --extra dev --extra semantic --no-install-project
uv sync --locked --extra dev --extra semantic --no-build-isolation
```

Omit `--extra semantic` for a lexical-only install. Model weights stay under ignored `.cache/model`. During this development run, Windows application control blocked one newer native dependency, so `scikit-learn==1.6.1` is pinned in the lockfile. Real-model checks pass with that pin.

## First search

```bash
uv run prism --config examples/local.yaml index create demo --semantic
uv run prism --config examples/local.yaml ingest demo --file examples/marketplace/documents.jsonl
uv run prism --config examples/local.yaml refresh demo
uv run prism --config examples/local.yaml search demo --query "quiet audio accessory that isolates me from busy surroundings" --mode hybrid
uv run prism --config examples/local.yaml search demo --query 'kind:service AND rating:>=4' --mode lexical --dsl
uv run prism --config examples/local.yaml serve
```

Omit `--semantic` at creation for the lexical-only path. On the managed Windows host, `.venv\Scripts\prism.exe` also runs the installed CLI directly. Accepted mutations are durable and become searchable after `refresh` or the server's scheduled refresh. `index stats` reports accepted and visible revisions.

With the server on `127.0.0.1:8080`, send an HTTP search:

```bash
curl -X POST http://127.0.0.1:8080/v1/indexes/demo/search \
  -H 'Content-Type: application/json' \
  -d '{"q":"my air conditioner is not cooling","mode":"hybrid","filters":[{"field":"kind","op":"eq","value":"service"}]}'
```

PowerShell users can use `Invoke-RestMethod` as shown in [API documentation](docs/api.md). The server also exposes `/docs` and `/metrics`.

## Distributed demo

```bash
uv run prism --config examples/local.yaml cluster build demo --shards 3 --replicas 1 --output data/cluster
uv run prism --config examples/local.yaml cluster serve --topology data/cluster/topology.json
```

The second command starts six real shard processes and a coordinator on port `8080`. The shard ports are `9100/9101`, `9110/9111`, and `9120/9121`. Search through the same HTTP endpoint. Stop `shard-0-a` and search again: the response should identify `shard-0-b` with full coverage. The cluster serves exported snapshots; distributed writes are outside this release. See [operations](docs/operations.md).

## Relevance and benchmarks

```bash
uv run prism lab --run-config benchmarks/configs/marketplace.yaml
uv run python benchmarks/prepare_scifact.py
docker compose -f docker/compose.benchmark.yaml up -d
uv run prism benchmark --run-config benchmarks/configs/scifact.yaml
```

Run JSON, a static HTML relevance report, and input checksums go under ignored `artifacts/`. Open `artifacts/marketplace/report.html` or `artifacts/scifact/report.html` locally. The lexical-only lab needs no model: `uv run prism lab --run-config benchmarks/configs/marketplace-lexical.yaml`. The [benchmark report](docs/benchmark-report.md) gives current measured outcomes and pending checks.
Small, machine-readable [measured summaries](benchmarks/results/README.md) are included in the repository; the large per-query output stays under ignored `artifacts/`.

## Check the code

```bash
uv run pytest -q
uv run ruff check src tests scripts benchmarks
uv run ruff format --check src tests scripts benchmarks
uv run mypy src/prism
```

Deterministic tests need no network. The real semantic test runs when the model has been provisioned. The cluster test starts localhost subprocesses. In the Windows managed environment used here, use a unique project-local `--basetemp work/checks-<id> -o cache_dir=work/pytest-cache-<id>` to avoid ACL conflicts with old temporary directories.

Read [architecture](docs/architecture.md), [storage format](docs/storage-format.md), [query language](docs/query-language.md), [relevance](docs/relevance.md), and [limitations](docs/limitations.md). Future improvements are listed as stretch work in the spec.

Prism code is Apache-2.0 licensed. Downloaded model and benchmark assets retain their own upstream rights and are not committed; see [NOTICE](NOTICE) and [dataset provenance](datasets/README.md). Report vulnerabilities through a private GitHub security advisory as described in [SECURITY.md](SECURITY.md).

