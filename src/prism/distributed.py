import asyncio
import hashlib
import json
import shutil
import time
import uuid
from pathlib import Path

import httpx

from prism.deadline import Deadline
from prism.errors import PrismError
from prism.models import SearchRequest
from prism.query import Parser, positive_clauses
from prism.search import fuse, ranked
from prism.storage import Index, encode_json
from prism.telemetry import RETRIES


def shard_for(external, count):
    return int.from_bytes(hashlib.sha256(external.encode()).digest()[:8], "big") % count


def export_cluster(index, output, shards=3, replicas=1):
    output = Path(output)
    if not 1 <= shards <= 32 or not 0 <= replicas <= 3:
        raise PrismError("INVALID_TOPOLOGY", "Unsupported shard or replica count")
    if output.exists():
        raise PrismError("EXPORT_EXISTS", "Choose a new empty export directory", 409)
    output.mkdir(parents=True)
    snap = index.snapshot
    stats = snap.stats
    epoch = {
        "epoch": uuid.uuid4().hex,
        "shards": shards,
        "routing": "sha256-first8-mod-v1",
        "stats": stats,
        "stats_fingerprint": hashlib.sha256(encode_json(stats)).hexdigest(),
        "config": snap.config,
        "index": index.path.name,
    }
    topology = {"epoch": epoch, "replicas": {}}
    for sid in range(shards):
        path = output / f"shard-{sid}-a"
        with Index(
            path,
            create=True,
            dimension=snap.config["dimension"],
            fingerprint=snap.config["fingerprint"],
            codec=snap.config["codec"],
        ) as dest:

            class ExistingVectors:
                fingerprint = snap.config["fingerprint"]

                def __init__(self, identities):
                    self.identities = identities

                def encode_documents(self, texts):
                    import numpy as np

                    return np.stack([snap.vectors[d] for d in self.identities])

            selected = [d for d in sorted(snap.docs) if shard_for(d, shards) == sid]
            for i in range(0, len(selected), 500):
                ids = selected[i : i + 500]
                dest.ingest(
                    [snap.docs[d] for d in ids],
                    ExistingVectors(ids) if snap.config["dimension"] else None,
                )
            dest.refresh()
            shard_generation = dest.snapshot.generation
        topology["replicas"][str(sid)] = []
        for replica in range(replicas + 1):
            letter = chr(97 + replica)
            replica_path = output / f"shard-{sid}-{letter}"
            if replica:
                shutil.copytree(path, replica_path)
            port = 9100 + sid * 10 + replica
            topology["replicas"][str(sid)].append(
                {
                    "url": f"http://127.0.0.1:{port}",
                    "name": f"shard-{sid}-{letter}",
                    "generation": shard_generation,
                }
            )
            settings = {
                "role": "shard",
                "shard_id": sid,
                "snapshot_path": str(replica_path.resolve()),
                "epoch_path": str((output / "epoch.json").resolve()),
                "index_name": index.path.name,
                "port": port,
                "synonyms_path": "examples/synonyms.json",
            }
            (output / f"shard-{sid}-{letter}.json").write_text(json.dumps(settings, indent=2))
    (output / "epoch.json").write_bytes(encode_json(epoch))
    (output / "topology.json").write_text(json.dumps(topology, indent=2))
    return topology


class Coordinator:
    def __init__(self, topology, provider=None):
        self.topology = json.loads(Path(topology).read_text())
        self.epoch = self.topology["epoch"]
        self.provider = provider
        self.client = httpx.AsyncClient(
            limits=httpx.Limits(max_connections=32, max_keepalive_connections=16)
        )
        self.health = {}
        self.semaphore = asyncio.Semaphore(16)

    async def close(self):
        await self.client.aclose()

    def compatible(self, data, sid, replica):
        return (
            data.get("epoch") == self.epoch["epoch"]
            and data.get("shard_id") == sid
            and data.get("stats_fingerprint") == self.epoch["stats_fingerprint"]
            and data.get("generation") == replica["generation"]
            and data.get("model_fingerprint") == self.epoch["config"]["fingerprint"]
        )

    async def probe(self):
        async def check(sid, replica):
            try:
                response = await self.client.get(replica["url"] + "/health/ready", timeout=1)
                okay = response.status_code == 200 and self.compatible(
                    response.json(), sid, replica
                )
            except (httpx.HTTPError, ValueError):
                okay = False
            self.health[replica["name"]] = {
                "ready": okay,
                "checked": time.monotonic(),
                "cooldown": 0,
            }
            return sid, okay

        results = await asyncio.gather(
            *(
                check(int(sid), r)
                for sid, replicas in self.topology["replicas"].items()
                for r in replicas
            )
        )
        return all(any(ok for s, ok in results if s == sid) for sid in range(self.epoch["shards"]))

    async def _shard(self, sid, request, vector, deadline):
        replicas = self.topology["replicas"][str(sid)]
        replicas = sorted(
            replicas,
            key=lambda r: self.health.get(r["name"], {}).get("cooldown", 0) > time.monotonic(),
        )
        last_code = "SHARD_UNAVAILABLE"
        for attempt, replica in enumerate(replicas[:2], 1):
            if deadline.remaining_ms() < 5:
                break
            if attempt > 1:
                RETRIES.inc()
            try:
                async with self.semaphore:
                    remaining = deadline.remaining_ms()
                    if remaining < 10:
                        break
                    payload = {
                        "request": request.model_copy(
                            update={"timeout_ms": max(10, int(remaining - 2))}
                        ).model_dump(),
                        "query_vector": vector,
                        "epoch": self.epoch["epoch"],
                        "model_fingerprint": self.epoch["config"]["fingerprint"],
                    }
                    response = await self.client.post(
                        replica["url"] + f"/internal/{self.epoch['index']}/branches",
                        json=payload,
                        timeout=remaining / 1000,
                    )
                    response.raise_for_status()
                    data = response.json()
                    if not self.compatible(data, sid, replica):
                        raise PrismError("EPOCH_MISMATCH", "Replica snapshot mismatch", 409)
                    self.health[replica["name"]] = {
                        "ready": True,
                        "checked": time.monotonic(),
                        "cooldown": 0,
                    }
                    return {
                        "id": sid,
                        "status": "ok",
                        "replica": replica["name"],
                        "attempts": attempt,
                    }, data
            except (httpx.HTTPError, ValueError, PrismError) as e:
                last_code = (
                    "SHARD_TIMEOUT"
                    if isinstance(e, httpx.TimeoutException)
                    else "SHARD_UNAVAILABLE"
                )
                self.health[replica["name"]] = {
                    "ready": False,
                    "checked": time.monotonic(),
                    "cooldown": time.monotonic() + 5,
                }
        return {"id": sid, "status": last_code, "attempts": min(2, len(replicas))}, None

    async def search(self, request):
        request = (
            request if isinstance(request, SearchRequest) else SearchRequest.model_validate(request)
        )
        deadline = Deadline(request.timeout_ms)
        mode = request.mode or ("hybrid" if self.epoch["config"]["dimension"] else "lexical")
        requested_mode, degraded = mode, False
        vector = None
        if mode != "lexical":
            if (
                self.provider is None
                or self.provider.fingerprint != self.epoch["config"]["fingerprint"]
            ):
                if not request.allow_lexical_fallback:
                    raise PrismError(
                        "SEMANTIC_UNAVAILABLE", "Coordinator embedding provider required", 503
                    )
                mode, degraded = "lexical", True
            else:
                text = (
                    request.q
                    if request.query_mode == "plain"
                    else " ".join(c[0] for c in positive_clauses(Parser(request.q).parse()))
                )
                if not text.strip():
                    raise PrismError("QUERY_TYPE_ERROR", "Semantic DSL requires positive text")
                vector = (await asyncio.to_thread(self.provider.encode_query, text)).tolist()
                deadline.check()
        request = request.model_copy(update={"mode": mode})
        tasks = [
            asyncio.create_task(self._shard(sid, request, vector, deadline))
            for sid in range(self.epoch["shards"])
        ]
        done, pending = await asyncio.wait(tasks, timeout=deadline.remaining_ms() / 1000)
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
        results = []
        for sid, task in enumerate(tasks):
            results.append(
                task.result()
                if task in done and not task.cancelled()
                else ({"id": sid, "status": "SHARD_TIMEOUT", "attempts": 1}, None)
            )
        good = [data for _, data in results if data is not None]
        partial = len(good) != self.epoch["shards"]
        if not good or (partial and not request.allow_partial):
            status = 504 if any(s["status"] == "SHARD_TIMEOUT" for s, _ in results) else 503
            raise PrismError(
                "INCOMPLETE_COVERAGE",
                "Required shards did not complete",
                status,
                shards=[s for s, _ in results],
            )
        lexical = ranked({d: s for data in good for d, s in data["lexical"]}, request.candidate_k)
        semantic = ranked({d: s for data in good for d, s in data["semantic"]}, request.candidate_k)
        hits, ranks = (
            fuse(lexical, semantic, request.top_k)
            if mode == "hybrid"
            else ((lexical if mode == "lexical" else semantic)[: request.top_k], {})
        )
        documents = {d: doc for data in good for d, doc in data["documents"].items()}
        return {
            "request_id": uuid.uuid4().hex,
            "requested_mode": requested_mode,
            "executed_mode": mode,
            "took_ms": deadline.elapsed_ms(),
            "partial": partial,
            "degraded": degraded or any(d["degraded"] for d in good),
            "snapshot_epoch": self.epoch["epoch"],
            "coverage": {"successful": len(good), "total": self.epoch["shards"]},
            "shards": [s for s, _ in results],
            "candidate_k": request.candidate_k,
            "expansion_limited": any(d["expansion_limited"] for d in good),
            "hits": [
                {
                    "id": d,
                    "score": score,
                    "document": documents[d],
                    "explanation": {"branch_ranks": ranks.get(d, {}), "rrf_constant": 60}
                    if request.explain
                    else None,
                }
                for d, score in hits
            ],
            "warnings": ["missing_shards"]
            if partial
            else (["lexical_fallback"] if degraded else []),
        }
