import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

import uvicorn
from pydantic import ValidationError

from prism.config import index_path, load_settings
from prism.embeddings import LocalEmbeddings, download_model
from prism.errors import PrismError
from prism.search import SearchEngine
from prism.storage import Index


def output(value):
    print(json.dumps(value, indent=2, ensure_ascii=False))


def parser():
    p = argparse.ArgumentParser(prog="prism", description="Prism hybrid search infrastructure")
    p.add_argument("--config", default=None, help="YAML/JSON configuration")
    sub = p.add_subparsers(dest="command", required=True)
    sub.add_parser("config")
    model = sub.add_parser("model").add_subparsers(dest="action", required=True)
    download = model.add_parser("download")
    download.add_argument("--revision")
    index = sub.add_parser("index").add_subparsers(dest="action", required=True)
    create = index.add_parser("create")
    create.add_argument("name")
    create.add_argument("--semantic", action="store_true")
    create.add_argument("--codec", choices=["plain", "compressed"], default="compressed")
    for action in ("stats", "gc"):
        index.add_parser(action).add_argument("name")
    ingest = sub.add_parser("ingest")
    ingest.add_argument("name")
    ingest.add_argument("--file", required=True)
    for action in ("refresh", "compact"):
        sub.add_parser(action).add_argument("name")
    delete = sub.add_parser("delete")
    delete.add_argument("name")
    delete.add_argument("id")
    search = sub.add_parser("search")
    search.add_argument("name")
    search.add_argument("--query", required=True)
    search.add_argument("--mode", choices=["lexical", "semantic", "hybrid"])
    search.add_argument("--dsl", action="store_true")
    search.add_argument("--explain", action="store_true")
    search.add_argument("--timeout-ms", type=int, default=5000)
    sub.add_parser("serve")
    cluster = sub.add_parser("cluster").add_subparsers(dest="action", required=True)
    build = cluster.add_parser("build")
    build.add_argument("name")
    build.add_argument("--shards", type=int, default=3)
    build.add_argument("--replicas", type=int, default=1)
    build.add_argument("--output", required=True)
    serve = cluster.add_parser("serve")
    serve.add_argument("--topology", required=True)
    for action in ("lab", "benchmark"):
        command = sub.add_parser(action)
        command.add_argument("--run-config", required=True)
    return p


def provider_for(settings):
    return LocalEmbeddings(settings.model_path)


def run_cluster(args, settings):
    from prism.api import create_app
    from prism.config import Settings

    topology_path = Path(args.topology).resolve()
    children = []
    try:
        topology = json.loads(topology_path.read_text())
        for replicas in topology["replicas"].values():
            for replica in replicas:
                config = topology_path.parent / (replica["name"] + ".json")
                children.append(
                    subprocess.Popen(
                        [sys.executable, "-m", "prism", "--config", str(config), "serve"],
                        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
                    )
                )
        provider = provider_for(settings) if topology["epoch"]["config"]["dimension"] else None
        config = Settings(
            role="coordinator",
            topology=str(topology_path),
            host=settings.host,
            port=settings.port,
            model_path=settings.model_path,
        )
        uvicorn.run(
            create_app(config, provider), host=config.host, port=config.port, access_log=False
        )
    finally:
        for child in children:
            child.terminate()
        for child in children:
            try:
                child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait()


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        settings = load_settings(args.config)
        if args.command == "config":
            return output(settings.model_dump())
        if args.command == "model":
            return output(download_model(settings.model_path, args.revision))
        if args.command == "serve":
            from prism.api import create_app

            return uvicorn.run(
                create_app(settings), host=settings.host, port=settings.port, access_log=False
            )
        if args.command == "cluster" and args.action == "serve":
            return run_cluster(args, settings)
        if args.command in ("lab", "benchmark"):
            from prism.lab import run_lab

            return output(run_lab(args.run_config, external=args.command == "benchmark"))
        if args.command == "index" and args.action == "create":
            provider = provider_for(settings) if args.semantic else None
            with Index(
                index_path(settings, args.name),
                create=True,
                dimension=provider.dimension if provider else 0,
                fingerprint=provider.fingerprint if provider else None,
                codec=args.codec,
            ):
                return output({"created": args.name})
        with Index(index_path(settings, args.name)) as index:
            if args.command == "ingest":
                provider = provider_for(settings) if index.config["dimension"] else None
                documents = [
                    json.loads(line)
                    for line in Path(args.file).read_text(encoding="utf-8").splitlines()
                    if line.strip()
                ]
                result = None
                for i in range(0, len(documents), 500):
                    result = index.ingest(documents[i : i + 500], provider)
                return output(result or {"accepted_revision": index.durable_revision()})
            if args.command == "refresh":
                return output(index.refresh())
            if args.command == "compact":
                return output(index.compact())
            if args.command == "delete":
                return output(index.delete(args.id))
            if args.command == "search":
                provider = provider_for(settings) if index.config["dimension"] else None
                synonyms = (
                    Path(settings.synonyms_path)
                    if settings.synonyms_path and Path(settings.synonyms_path).exists()
                    else None
                )
                return output(
                    SearchEngine(index, provider, synonyms).search(
                        {
                            "q": args.query,
                            "mode": args.mode,
                            "query_mode": "dsl" if args.dsl else "plain",
                            "explain": args.explain,
                            "timeout_ms": args.timeout_ms,
                        }
                    )
                )
            if args.command == "cluster":
                from prism.distributed import export_cluster

                return output(export_cluster(index, args.output, args.shards, args.replicas))
            if args.command == "index" and args.action == "gc":
                return output(index.garbage_collect())
            return output(index.stats())
    except (PrismError, ValidationError, OSError, ValueError) as e:
        output(
            e.as_dict()
            if isinstance(e, PrismError)
            else {"code": "COMMAND_FAILED", "message": str(e)}
        )
        raise SystemExit(1) from e
