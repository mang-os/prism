"""Offline qrel evaluation and an escaped, standalone relevance report."""

import hashlib
import html
import json
import math
import platform
import subprocess
import time
from pathlib import Path

import numpy as np
import yaml

from prism.embeddings import LocalEmbeddings
from prism.errors import PrismError
from prism.search import SearchEngine
from prism.storage import Index


def metrics(ranking, judgments):
    ranking = list(dict.fromkeys(ranking))
    relevant = {d for d, grade in judgments.items() if grade > 0}
    gains = [2 ** judgments.get(d, 0) - 1 for d in ranking[:10]]
    dcg = sum(g / math.log2(i + 2) for i, g in enumerate(gains))
    ideal = sorted((2**g - 1 for g in judgments.values()), reverse=True)[:10]
    idcg = sum(g / math.log2(i + 2) for i, g in enumerate(ideal))
    return {
        "ndcg@10": dcg / idcg if idcg else 0,
        "mrr@10": next((1 / (i + 1) for i, d in enumerate(ranking[:10]) if d in relevant), 0),
        "recall@10": len(set(ranking[:10]) & relevant) / len(relevant) if relevant else 0,
        "recall@100": len(set(ranking[:100]) & relevant) / len(relevant) if relevant else 0,
        "success@5": float(bool(set(ranking[:5]) & relevant)),
    }


def load_qrels(path):
    qrels: dict[str, dict[str, int]] = {}
    for line in Path(path).read_text().splitlines():
        cells = line.split()
        if not cells or cells[0] in ("query-id", "query_id"):
            continue
        if len(cells) == 4:
            qid, _, docid, grade = cells
        else:
            qid, docid, grade = cells
        if docid in qrels.setdefault(qid, {}):
            raise ValueError("Duplicate judgment")
        qrels[qid][docid] = int(grade)
    return qrels


def run_lab(config_path, external=False):
    cfg = yaml.safe_load(Path(config_path).read_text())
    documents = [
        json.loads(x) for x in Path(cfg["documents"]).read_text(encoding="utf-8").splitlines()
    ]
    queries = [json.loads(x) for x in Path(cfg["queries"]).read_text(encoding="utf-8").splitlines()]
    qrels = load_qrels(cfg["qrels"])
    corpus_ids = {d["id"] for d in documents}
    if any(d not in corpus_ids for judgments in qrels.values() for d in judgments):
        raise ValueError("Judgment references an unknown document")
    if len({q["id"] for q in queries}) != len(queries):
        raise ValueError("Duplicate query ID")
    output = Path(cfg.get("output", "artifacts/lab"))
    output.mkdir(parents=True, exist_ok=True)
    existing_index = cfg.get("existing_index")
    index_path = (
        Path(existing_index) if existing_index else output / ("index-" + str(time.time_ns()))
    )
    provider = LocalEmbeddings(cfg["model_path"]) if cfg.get("semantic") else None
    all_runs, summaries = {}, {}
    with Index(
        index_path,
        create=not bool(existing_index),
        dimension=provider.dimension if provider else 0,
        fingerprint=provider.fingerprint if provider else None,
    ) as index:
        if existing_index:
            if set(index.snapshot.docs) != corpus_ids:
                raise ValueError("Existing index IDs do not match benchmark corpus")
            indexing_ms = None
        else:
            start = time.perf_counter()
            for i in range(0, len(documents), 500):
                index.ingest(documents[i : i + 500], provider)
            index.refresh()
            indexing_ms = (time.perf_counter() - start) * 1000
        engine = SearchEngine(
            index, provider, Path(cfg["synonyms"]) if cfg.get("synonyms") else None
        )
        systems = {
            "exact_lexical": {"mode": "lexical", "typo_tolerance": False, "synonyms": False},
            "fuzzy": {"mode": "lexical", "typo_tolerance": True, "synonyms": False},
            "synonyms": {"mode": "lexical", "typo_tolerance": False, "synonyms": True},
            "expanded_lexical": {"mode": "lexical", "typo_tolerance": True, "synonyms": True},
        }
        if provider:
            systems.update(
                {
                    "semantic": {"mode": "semantic", "typo_tolerance": False, "synonyms": False},
                    "hybrid": {"mode": "hybrid"},
                }
            )
        for name, settings in systems.items():
            runs = []
            for q in queries:
                request = {
                    "q": q["text"],
                    "filters": q.get("filters", []),
                    "top_k": 100,
                    "candidate_k": cfg.get("candidate_k", 100),
                    "timeout_ms": 5000,
                    **settings,
                }
                start = time.perf_counter()
                error = None
                try:
                    result = engine.search(request)
                except PrismError as exc:
                    if exc.code != "DEADLINE_EXCEEDED":
                        raise RuntimeError(f"{name} query {q['id']} failed: {exc}") from exc
                    result = {"hits": []}
                    error = exc.code
                latency_ms = (time.perf_counter() - start) * 1000
                run = {
                    "query_id": q["id"],
                    "text": q["text"],
                    "subset": q.get("subset", "default"),
                    "split": q.get("split", "test"),
                    "latency_ms": latency_ms,
                    "error": error,
                    "hits": result["hits"],
                    "metrics": metrics([h["id"] for h in result["hits"]], qrels.get(q["id"], {})),
                    "settings": request,
                }
                runs.append(run)
            all_runs[name] = runs
            summaries[name] = summarize(runs)
            (output / (name + ".jsonl")).write_text(
                "\n".join(json.dumps(r) for r in runs), encoding="utf-8"
            )
            print(f"Completed {name}: {len(runs)} queries", flush=True)
        metadata = {
            "platform": platform.platform(),
            "python": platform.python_version(),
            "documents": len(documents),
            "queries": len(queries),
            "model_fingerprint": provider.fingerprint if provider else None,
            "indexing_including_embedding_ms": indexing_ms,
            "index_reused": bool(existing_index),
            "index_stats": index.stats(),
            "config": cfg,
            "input_checksums": {
                key: hashlib.sha256(Path(cfg[key]).read_bytes()).hexdigest()
                for key in ("documents", "queries", "qrels")
            },
        }
        try:
            metadata["commit"] = subprocess.check_output(
                ["git", "rev-parse", "HEAD"], stderr=subprocess.DEVNULL, text=True
            ).strip()
        except (OSError, subprocess.CalledProcessError):
            metadata["commit"] = "uncommitted"
        if external:
            from prism.baseline import run_opensearch

            try:
                runs = run_opensearch(
                    cfg.get("opensearch", "http://127.0.0.1:9200"), documents, queries, qrels
                )
                all_runs["opensearch_lexical"] = runs
                summaries["opensearch_lexical"] = summarize(runs)
                metadata["external_status"] = "measured"
            except Exception as e:
                metadata["external_status"] = "failed"
                metadata["external_error"] = str(e)
        else:
            metadata["external_status"] = "not_run"
    report = {"metadata": metadata, "summary": summaries, "runs": all_runs}
    (output / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    render_html(report, output / "report.html")
    for name, runs in all_runs.items():
        (output / (name + ".jsonl")).write_text(
            "\n".join(json.dumps(r) for r in runs), encoding="utf-8"
        )
    return {
        "report": str(output / "report.html"),
        "summary": summaries,
        "external_status": metadata["external_status"],
    }


def summarize(runs):
    groups = {"all": runs, "held_out": [r for r in runs if r["split"] == "test"]}
    groups.update(
        {
            subset: [r for r in runs if r["subset"] == subset and r["split"] == "test"]
            for subset in {r["subset"] for r in runs}
        }
    )
    result = {}
    for name, group in groups.items():
        if not group:
            continue
        result[name] = {
            "queries": len(group),
            "errors": sum(bool(r.get("error")) for r in group),
            **{k: float(np.mean([r["metrics"][k] for r in group])) for k in group[0]["metrics"]},
            **{
                f"p{percentile}_ms": float(
                    np.percentile([r["latency_ms"] for r in group], percentile)
                )
                for percentile in (50, 95, 99)
            },
        }
    return result


def render_html(report, path):
    sections = []
    for name, runs in report["runs"].items():
        rows = []
        for run in runs:
            hits = "".join(
                f"<li>{html.escape(h['id'])}: {html.escape(h['document']['title'])} ({h['score']:.5f})</li>"
                for h in run["hits"][:10]
            )
            rows.append(
                f"<details data-query='{html.escape(run['query_id'], quote=True)}'><summary>{html.escape(run['text'])} — nDCG {run['metrics']['ndcg@10']:.3f}</summary><ol>{hits}</ol></details>"
            )
        sections.append(f"<section><h2>{html.escape(name)}</h2>{''.join(rows)}</section>")
    content = (
        "<!doctype html><html><meta charset='utf-8'><title>Prism relevance lab</title><style>body{font:16px system-ui;margin:30px;background:#f5f6f8}main{display:flex;gap:20px;overflow:auto}section{min-width:350px;background:white;padding:18px}details{margin:14px 0}pre{white-space:pre-wrap}</style><h1>Prism relevance lab</h1><label>Filter queries <input id='filter'></label><main>"
        + "".join(sections)
        + "</main><h2>Method and measured summaries</h2><pre>"
        + html.escape(
            json.dumps({"metadata": report["metadata"], "summary": report["summary"]}, indent=2)
        )
        + "</pre><script>document.getElementById('filter').oninput=e=>document.querySelectorAll('details').forEach(d=>d.hidden=!d.textContent.toLowerCase().includes(e.target.value.toLowerCase()))</script></html>"
    )
    path.write_text(content, encoding="utf-8")
