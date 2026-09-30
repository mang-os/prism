import asyncio
import json
import os
import socket
import subprocess
import sys
import time

import httpx
import pytest

from prism.distributed import Coordinator, export_cluster, shard_for
from prism.errors import PrismError
from prism.models import SearchRequest
from prism.search import SearchEngine
from prism.storage import Index


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_real_process_cluster_rankings_replicas_and_partial(tmp_path):
    path = tmp_path / "demo"
    children, logs = {}, []
    with Index(path, create=True) as index:
        index.ingest(
            [
                {
                    "id": f"doc-{i}",
                    "title": "database storage" if i % 2 else "storage systems",
                    "body": "reliability " * (1 + i % 3),
                }
                for i in range(24)
            ]
        )
        index.refresh()
        engine = SearchEngine(index)
        expected = engine.search(
            {
                "q": "database storage",
                "mode": "lexical",
                "timeout_ms": 5000,
                "top_k": 10,
                "typo_tolerance": False,
            }
        )["hits"]
        cluster_path = tmp_path / "cluster"
        topology = export_cluster(index, cluster_path)
        try:
            for replicas in topology["replicas"].values():
                for replica in replicas:
                    config_path = cluster_path / (replica["name"] + ".json")
                    cfg = json.loads(config_path.read_text())
                    cfg["port"] = free_port()
                    cfg["synonyms_path"] = None
                    config_path.write_text(json.dumps(cfg))
                    replica["url"] = f"http://127.0.0.1:{cfg['port']}"
                    log = (tmp_path / (replica["name"] + ".log")).open("w")
                    logs.append(log)
                    children[replica["name"]] = subprocess.Popen(
                        [sys.executable, "-m", "prism", "--config", str(config_path), "serve"],
                        stdout=log,
                        stderr=log,
                        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
                    )
            topology_path = cluster_path / "topology.json"
            topology_path.write_text(json.dumps(topology))
            with httpx.Client(timeout=0.5) as client:
                for replicas in topology["replicas"].values():
                    for replica in replicas:
                        deadline = time.monotonic() + 40
                        while True:
                            try:
                                if client.get(replica["url"] + "/health/ready").status_code == 200:
                                    break
                            except httpx.HTTPError:
                                pass
                            if time.monotonic() > deadline:
                                pytest.fail("Shard failed to start: " + replica["name"])
                            time.sleep(0.1)

            async def exercise():
                coordinator = Coordinator(topology_path)
                try:
                    assert await coordinator.probe()
                    request = SearchRequest(
                        q="database storage", mode="lexical", timeout_ms=5000, typo_tolerance=False
                    )
                    response = await coordinator.search(request)
                    assert not response["partial"]
                    assert [(h["id"], h["score"]) for h in response["hits"]] == [
                        (h["id"], h["score"]) for h in expected
                    ]
                    primary = children["shard-0-a"]
                    primary.terminate()
                    primary.wait(timeout=5)
                    response = await coordinator.search(request)
                    assert not response["partial"]
                    assert response["shards"][0]["replica"] == "shard-0-b"
                    for name in ("shard-1-a", "shard-1-b"):
                        children[name].terminate()
                        children[name].wait(timeout=5)
                    response = await coordinator.search(request)
                    assert response["partial"]
                    assert response["coverage"] == {"successful": 2, "total": 3}
                    with pytest.raises(PrismError):
                        await coordinator.search(
                            request.model_copy(update={"allow_partial": False})
                        )
                    for child in children.values():
                        if child.poll() is None:
                            child.terminate()
                            child.wait(timeout=5)
                    with pytest.raises(PrismError):
                        await coordinator.search(request)
                finally:
                    await coordinator.close()

            asyncio.run(exercise())
        finally:
            for child in children.values():
                if child.poll() is None:
                    child.terminate()
                    try:
                        child.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        child.kill()
                        child.wait()
            for log in logs:
                log.close()


def test_routing_is_stable():
    assert (
        shard_for("hello", 3)
        == int.from_bytes(__import__("hashlib").sha256(b"hello").digest()[:8], "big") % 3
    )
