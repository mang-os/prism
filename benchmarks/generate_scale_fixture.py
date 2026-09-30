import argparse
import json
import random
from pathlib import Path


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--count", type=int, default=50000)
    p.add_argument("--seed", type=int, default=17)
    p.add_argument("--output", default="artifacts/scale/documents.jsonl")
    args = p.parse_args()
    rng = random.Random(args.seed)
    vocabulary = [
        "database",
        "repair",
        "headphones",
        "storage",
        "music",
        "cooling",
        "service",
        "catalog",
    ] + [f"token{i}" for i in range(2000)]
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as f:
        for i in range(args.count):
            length = rng.randint(20, 120)
            body = " ".join(
                vocabulary[int((rng.random() ** 3) * len(vocabulary))] for _ in range(length)
            )
            f.write(
                json.dumps(
                    {
                        "id": f"synthetic-{i:06}",
                        "title": " ".join(rng.choices(vocabulary, k=6)),
                        "body": body,
                        "kind": rng.choice(["product", "service", "document"]),
                        "category": f"cat-{rng.randrange(20)}",
                    }
                )
                + "\n"
            )
    print(f"Generated {args.count} synthetic records with seed {args.seed}")


if __name__ == "__main__":
    main()
