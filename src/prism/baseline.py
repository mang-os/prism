import time
import uuid

import httpx

from prism.lab import metrics


def run_opensearch(url, documents, queries, qrels):
    name = "prism-baseline-" + uuid.uuid4().hex[:12]
    with httpx.Client(base_url=url, timeout=60) as client:
        version = client.get("/").raise_for_status().json()["version"]["number"]
        settings = {
            "number_of_shards": 1,
            "number_of_replicas": 0,
            "similarity": {"prism": {"type": "BM25", "k1": 1.2, "b": 0.75}},
            "analysis": {
                "analyzer": {
                    "prism": {
                        "type": "custom",
                        "tokenizer": "letter_digit",
                        "filter": ["lowercase"],
                    }
                },
                "tokenizer": {"letter_digit": {"type": "pattern", "pattern": "[^\\p{L}\\p{N}]+"}},
            },
        }
        mappings = {
            "properties": {
                **{
                    f: {"type": "text", "analyzer": "prism", "similarity": "prism"}
                    for f in ("title", "body")
                },
                **{f: {"type": "keyword"} for f in ("kind", "category", "tags")},
                **{f: {"type": "float"} for f in ("rating", "price")},
            }
        }
        client.put("/" + name, json={"settings": settings, "mappings": mappings}).raise_for_status()
        try:
            for i in range(0, len(documents), 500):
                body = "".join(
                    __import__("json").dumps({"index": {"_index": name, "_id": d["id"]}})
                    + "\n"
                    + __import__("json").dumps(d)
                    + "\n"
                    for d in documents[i : i + 500]
                )
                response = (
                    client.post(
                        "/_bulk", content=body, headers={"Content-Type": "application/x-ndjson"}
                    )
                    .raise_for_status()
                    .json()
                )
                if response["errors"]:
                    raise ValueError("OpenSearch rejected bulk records")
            client.post(f"/{name}/_refresh").raise_for_status()
            runs = []
            for q in queries:
                filters = []
                for f in q.get("filters", []):
                    op = f.get("op", "eq")
                    filters.append(
                        {"term": {f["field"]: f["value"]}}
                        if op == "eq"
                        else {"range": {f["field"]: {op: f["value"]}}}
                    )
                # Separate field clauses sum per-field scores, as Prism does.
                lexical = {
                    "bool": {
                        "should": [
                            {"match": {"title": {"query": q["text"], "boost": 2}}},
                            {"match": {"body": q["text"]}},
                        ],
                        "minimum_should_match": 1,
                        "filter": filters,
                    }
                }
                query_body = {"query": lexical, "size": 100}
                start = time.perf_counter()
                response = (
                    client.post(f"/{name}/_search", json=query_body).raise_for_status().json()
                )
                latency = (time.perf_counter() - start) * 1000
                hits = [
                    {"id": h["_id"], "score": h["_score"], "document": h["_source"]}
                    for h in response["hits"]["hits"]
                ]
                runs.append(
                    {
                        "query_id": q["id"],
                        "text": q["text"],
                        "subset": q.get("subset", "default"),
                        "split": q.get("split", "test"),
                        "latency_ms": latency,
                        "hits": hits,
                        "metrics": metrics([h["id"] for h in hits], qrels.get(q["id"], {})),
                        "engine_version": version,
                    }
                )
            return runs
        finally:
            client.delete("/" + name)
