"""Extract small, reviewable metadata and summaries from a full local lab report."""

import argparse
import hashlib
import json
from pathlib import Path


def extract(path):
    with path.open(encoding="utf-8") as report:
        prefix = report.read(1_000_000)
    decoder = json.JSONDecoder()
    position = prefix.index('"metadata":') + len('"metadata":')
    metadata, _ = decoder.raw_decode(prefix[position:].lstrip())
    position = prefix.index('"summary":') + len('"summary":')
    summary, _ = decoder.raw_decode(prefix[position:].lstrip())
    digest = hashlib.sha256()
    with path.open("rb") as report:
        for block in iter(lambda: report.read(1024 * 1024), b""):
            digest.update(block)
    return {
        "source_report_sha256": digest.hexdigest(),
        "raw_query_runs_included": False,
        "metadata": metadata,
        "summary": summary,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(extract(Path(args.report)), indent=2), encoding="utf-8")
    print(output)


if __name__ == "__main__":
    main()
