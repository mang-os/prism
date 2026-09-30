import hashlib
import json
import zipfile
from pathlib import Path

import httpx

URL = "https://public.ukp.informatik.tu-darmstadt.de/thakur/BEIR/datasets/scifact.zip"
EXPECTED_MD5 = "5f7d1de60b170fc8027bb7898e2efca1"


def main():
    destination = Path("datasets")
    destination.mkdir(exist_ok=True)
    archive = destination / "scifact.zip"
    with httpx.stream("GET", URL, follow_redirects=True, timeout=60) as response:
        response.raise_for_status()
        with archive.open("wb") as f:
            for chunk in response.iter_bytes():
                f.write(chunk)
    if hashlib.md5(archive.read_bytes()).hexdigest() != EXPECTED_MD5:
        raise ValueError("BEIR SciFact checksum mismatch")
    with zipfile.ZipFile(archive) as z:
        for name in z.namelist():
            target = (destination / name).resolve()
            if not target.is_relative_to(destination.resolve()):
                raise ValueError("Unsafe archive path")
        z.extractall(destination)
    root = destination / "scifact"
    documents = [
        {"id": d["_id"], "title": d.get("title") or d["_id"], "body": d["text"], "kind": "document"}
        for d in map(json.loads, (root / "corpus.jsonl").read_text(encoding="utf-8").splitlines())
    ]
    judged = {line.split()[0] for line in (root / "qrels/test.tsv").read_text().splitlines()[1:]}
    queries = [
        {"id": q["_id"], "text": q["text"], "split": "test", "subset": "scifact"}
        for q in map(json.loads, (root / "queries.jsonl").read_text(encoding="utf-8").splitlines())
        if q["_id"] in judged
    ]
    (root / "documents.jsonl").write_text(
        "\n".join(json.dumps(d) for d in documents) + "\n", encoding="utf-8"
    )
    (root / "prism-queries.jsonl").write_text(
        "\n".join(json.dumps(q) for q in queries) + "\n", encoding="utf-8"
    )
    (root / "provenance.json").write_text(
        json.dumps(
            {
                "url": URL,
                "archive_md5": EXPECTED_MD5,
                "documents": len(documents),
                "test_queries": len(queries),
            },
            indent=2,
        )
    )
    print(f"Prepared {len(documents)} documents and {len(queries)} test queries")


if __name__ == "__main__":
    main()
