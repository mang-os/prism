"""Publish a small, path-independent summary of repeated HTTP load reports."""

import argparse
import hashlib
import json
from pathlib import Path
from statistics import median


def summarize(paths, timeout_ms):
    reports = [json.loads(path.read_text(encoding="utf-8")) for path in paths]
    first = reports[0]
    keys = {(r["mode"], r["concurrency"]) for r in first["results"]}
    if any(
        report["platform"] != first["platform"]
        or report["python"] != first["python"]
        or {(r["mode"], r["concurrency"]) for r in report["results"]} != keys
        for report in reports
    ):
        raise ValueError("Reports must use the same host environment and mode/concurrency settings")
    metrics = (
        "p50_ms",
        "p95_ms",
        "p99_ms",
        "throughput_per_s",
        "successful_throughput_per_s",
        "error_rate",
    )
    groups = []
    for mode, concurrency in sorted(keys):
        runs = [
            next(
                r for r in report["results"] if (r["mode"], r["concurrency"]) == (mode, concurrency)
            )
            for report in reports
        ]
        if len({r["requests"] for r in runs}) != 1:
            raise ValueError("All runs of a mode must have the same request count")
        groups.append(
            {
                "mode": mode,
                "concurrency": concurrency,
                "requests_per_run": runs[0]["requests"],
                "runs": [
                    {
                        "run": i,
                        "successes": r["successes"],
                        "errors": r["errors"],
                        **{name: r[name] for name in metrics},
                    }
                    for i, r in enumerate(runs, 1)
                ],
                "spread": {
                    name: {
                        "min": min(r[name] for r in runs),
                        "median": median(r[name] for r in runs),
                        "max": max(r[name] for r in runs),
                    }
                    for name in metrics
                },
            }
        )
    return {
        "method": "closed-loop localhost HTTP; percentiles of successful responses only",
        "warm_requests_per_run": first["results"][0]["warm_requests"],
        "server_deadline_ms": timeout_ms,
        "platform": first["platform"],
        "python": first["python"],
        "cpu_count": first["cpu_count"],
        "index_name": Path(first["index_root"]).name,
        "source_report_sha256": [hashlib.sha256(path.read_bytes()).hexdigest() for path in paths],
        "groups": groups,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--reports", type=Path, nargs="+", required=True)
    parser.add_argument("--timeout-ms", type=int, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.timeout_ms < 1:
        parser.error("timeout must be positive")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(summarize(args.reports, args.timeout_ms), indent=2), encoding="utf-8"
    )
    print(args.output)


if __name__ == "__main__":
    main()
