"""End-to-end localhost latency smoke; reports successes and errors together."""

import argparse
import asyncio
import json
import os
import platform
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import numpy as np
import psutil


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


async def measure(base_url, index, queries, mode, samples, concurrency, timeout_ms):
    timings, errors = [], {}
    async with httpx.AsyncClient(
        base_url=base_url,
        limits=httpx.Limits(max_connections=concurrency + 4),
        timeout=timeout_ms / 1000 + 1,
    ) as client:

        async def send(sequence):
            query = queries[sequence % len(queries)]
            payload = {
                "q": query["text"],
                "mode": mode,
                "top_k": 10,
                "candidate_k": 100,
                "timeout_ms": timeout_ms,
                "typo_tolerance": False,
                "synonyms": False,
            }
            started = time.perf_counter()
            try:
                response = await client.post(f"/v1/indexes/{index}/search", json=payload)
                elapsed = (time.perf_counter() - started) * 1000
                if response.status_code != 200:
                    code = response.json().get("code", str(response.status_code))
                    errors[code] = errors.get(code, 0) + 1
                else:
                    timings.append(elapsed)
            except (httpx.HTTPError, ValueError) as exc:
                key = type(exc).__name__
                errors[key] = errors.get(key, 0) + 1

        for i in range(100):
            await send(i)
        timings.clear()
        errors.clear()
        started = time.perf_counter()
        await asyncio.gather(
            *(worker(slot, concurrency, samples, send) for slot in range(concurrency))
        )
        duration = time.perf_counter() - started
    return {
        "mode": mode,
        "concurrency": concurrency,
        "requests": samples,
        "successes": len(timings),
        "errors": errors,
        "throughput_per_s": samples / duration,
        "successful_throughput_per_s": len(timings) / duration,
        "duration_s": duration,
        "error_rate": (samples - len(timings)) / samples,
        "p50_ms": float(np.percentile(timings, 50)) if timings else None,
        "p95_ms": float(np.percentile(timings, 95)) if timings else None,
        "p99_ms": float(np.percentile(timings, 99)) if timings else None,
        "type": "closed_loop_local_http_smoke; percentiles_of_successful_responses_only",
        "warm_requests": 100,
    }


async def worker(slot, concurrency, samples, send):
    for sequence in range(slot, samples, concurrency):
        await send(sequence)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--index-root", required=True)
    p.add_argument("--queries", required=True)
    p.add_argument("--model-path", default=".cache/model")
    p.add_argument("--samples", type=int, default=1000)
    p.add_argument("--timeout-ms", type=int, default=500)
    p.add_argument("--output", default="artifacts/performance/report.json")
    args = p.parse_args()
    index_root = Path(args.index_root).resolve()
    queries = [json.loads(x) for x in Path(args.queries).read_text().splitlines()]
    if not queries or not (index_root / "CURRENT").exists():
        raise ValueError("Use an existing Prism snapshot and nonempty query file")
    port = free_port()
    config = {
        "data": str(index_root.parent),
        "index_name": index_root.name,
        "model_path": str(Path(args.model_path).resolve()),
        "synonyms_path": None,
        "port": port,
        "workers": 4,
    }
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    config_file = output.parent / "server-settings.json"
    config_file.write_text(json.dumps(config))
    log_file = (output.parent / "server.log").open("w")
    server = subprocess.Popen(
        [sys.executable, "-m", "prism", "--config", str(config_file), "serve"],
        stdout=log_file,
        stderr=log_file,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    base = f"http://127.0.0.1:{port}"
    try:
        start = time.monotonic()
        while True:
            if server.poll() is not None:
                raise RuntimeError("Server exited before readiness; inspect server.log")
            try:
                response = httpx.get(base + "/health/ready", timeout=0.3)
                if response.status_code == 200:
                    break
            except httpx.HTTPError:
                pass
            if time.monotonic() - start > 60:
                raise TimeoutError("Server did not become ready")
            time.sleep(0.2)
        results = []
        for mode in ("lexical", "hybrid"):
            for concurrency in (1, 4, 16):
                result = asyncio.run(
                    measure(
                        base,
                        index_root.name,
                        queries,
                        mode,
                        args.samples,
                        concurrency,
                        args.timeout_ms,
                    )
                )
                process = psutil.Process(server.pid)
                result["server_rss_bytes"] = process.memory_info().rss + sum(
                    child.memory_info().rss
                    for child in process.children(recursive=True)
                    if child.is_running()
                )
                results.append(result)
                print(mode, concurrency, result["p95_ms"], result["errors"], flush=True)
        metrics = httpx.get(base + "/metrics").text
        (output.parent / "server-metrics.prom").write_text(metrics)
        report = {
            "platform": platform.platform(),
            "python": platform.python_version(),
            "cpu_count": os.cpu_count(),
            "index_root": str(index_root),
            "model_path": str(Path(args.model_path).resolve()),
            "results": results,
            "limitations": "Closed-loop local HTTP smoke; not an open-arrival capacity test. Cold model/index load excluded; warmup and errors disclosed.",
        }
        output.write_text(json.dumps(report, indent=2))
    finally:
        server.terminate()
        try:
            server.wait(timeout=5)
        except subprocess.TimeoutExpired:
            server.kill()
            server.wait()
        log_file.close()


if __name__ == "__main__":
    main()
