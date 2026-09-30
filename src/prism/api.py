import asyncio
import json
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from pydantic import BaseModel, ConfigDict, Field

from prism.config import Settings, index_path
from prism.distributed import Coordinator
from prism.embeddings import LocalEmbeddings
from prism.errors import PrismError
from prism.models import Document, SearchRequest
from prism.search import SearchEngine
from prism.storage import Index
from prism.telemetry import ACTIVE, INDEX_BYTES, LIVE_DOCS, OVERLOAD, PENDING_REVISIONS


class CreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    semantic: bool = False
    codec: str = "compressed"


class BulkRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    documents: list[Document] = Field(max_length=1000)


class RefreshRequest(BaseModel):
    through_revision: int | None = Field(default=None, ge=0)


class BranchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request: SearchRequest
    query_vector: list[float] | None = Field(default=None, max_length=4096)
    epoch: str
    model_fingerprint: str | None


def create_app(settings=None, provider=None):
    settings = settings or Settings()
    indexes, engines, tasks = {}, {}, set()
    slots = asyncio.Semaphore(settings.workers)
    epoch: dict = json.loads(Path(settings.epoch_path).read_text()) if settings.epoch_path else {}
    provider_error = None
    if (
        provider is None
        and (Path(settings.model_path) / "prism-model.json").exists()
        and settings.role != "shard"
    ):
        try:
            provider = LocalEmbeddings(settings.model_path)
        except PrismError as exc:
            provider_error = exc.message
    coordinator: Coordinator | None = None

    async def work(fn, *args, timeout=None):
        if slots.locked():
            OVERLOAD.inc()
            raise PrismError("OVERLOADED", "Worker slots exhausted; retry later", 429)
        await slots.acquire()
        ACTIVE.inc()
        task = asyncio.create_task(asyncio.to_thread(fn, *args))
        tasks.add(task)

        def release(t):
            slots.release()
            ACTIVE.dec()
            tasks.discard(t)
            if not t.cancelled():
                t.exception()

        task.add_done_callback(release)
        try:
            return await asyncio.wait_for(asyncio.shield(task), timeout)
        except TimeoutError as e:
            raise PrismError("DEADLINE_EXCEEDED", "Request deadline expired", 504) from e

    def engine(name):
        if name not in engines:
            if settings.role == "coordinator":
                raise PrismError("DISTRIBUTED_WRITES_UNSUPPORTED", "Use prism cluster build", 409)
            if settings.role == "shard" and name != settings.index_name:
                raise PrismError("INDEX_NOT_FOUND", "Unknown shard index", 404)
            path = (
                Path(settings.snapshot_path)
                if settings.role == "shard"
                else index_path(settings, name)
            )
            indexes[name] = Index(path, read_only=settings.role == "shard")
            synonyms = (
                Path(settings.synonyms_path)
                if settings.synonyms_path and Path(settings.synonyms_path).exists()
                else None
            )
            engines[name] = SearchEngine(
                indexes[name], provider, synonyms, epoch["stats"] if epoch else None
            )
        return engines[name]

    async def health_loop():
        assert coordinator is not None
        while True:
            await coordinator.probe()
            await asyncio.sleep(2)

    async def refresh_loop():
        while True:
            await asyncio.sleep(1)
            for index in list(indexes.values()):
                if (
                    not slots.locked()
                    and index.db is not None
                    and index.durable_revision() > index.snapshot.revision
                ):
                    try:
                        await work(index.refresh)
                    except PrismError:
                        pass

    @asynccontextmanager
    async def lifespan(app):
        nonlocal coordinator
        probe_task = None
        refresh_task = None
        if settings.role == "coordinator":
            coordinator = Coordinator(settings.topology, provider)
            await coordinator.probe()
            probe_task = asyncio.create_task(health_loop())
        if settings.role == "shard":
            engine(settings.index_name)
        if settings.role == "local":
            refresh_task = asyncio.create_task(refresh_loop())
        yield
        if refresh_task:
            refresh_task.cancel()
            await asyncio.gather(refresh_task, return_exceptions=True)
        if probe_task:
            probe_task.cancel()
            await asyncio.gather(probe_task, return_exceptions=True)
        if tasks:
            await asyncio.gather(*list(tasks), return_exceptions=True)
        if coordinator:
            await coordinator.close()
        for index in indexes.values():
            index.close()

    app = FastAPI(title="Prism", version="0.1.0", lifespan=lifespan)

    @app.get("/demo", include_in_schema=False, response_class=HTMLResponse)
    async def demo():
        return HTMLResponse((Path(__file__).with_name("demo.html")).read_text(encoding="utf-8"))

    @app.middleware("http")
    async def body_limit(request, call_next):
        # Bound streamed bodies before JSON decoding, including chunked requests.
        if request.method in ("POST", "PUT", "PATCH"):
            body = bytearray()
            async for chunk in request.stream():
                body.extend(chunk)
                if len(body) > 8 * 1024 * 1024:
                    return JSONResponse(
                        {"code": "REQUEST_TOO_LARGE", "message": "Maximum body is 8 MiB"},
                        status_code=413,
                    )
            request._body = bytes(body)
        return await call_next(request)

    @app.exception_handler(PrismError)
    async def prism_error(request: Request, exc: PrismError):
        return JSONResponse(
            {"request_id": uuid.uuid4().hex, **exc.as_dict()}, status_code=exc.status
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError):
        return JSONResponse(
            {"request_id": uuid.uuid4().hex, "code": "VALIDATION_ERROR", "message": str(exc)},
            status_code=422,
        )

    @app.get("/health/live")
    async def live():
        return {"status": "alive"}

    @app.get("/health/ready")
    async def ready():
        if coordinator:
            okay = await coordinator.probe()
            return JSONResponse(
                {"status": "ready" if okay else "unavailable", "epoch": coordinator.epoch["epoch"]},
                status_code=200 if okay else 503,
            )
        if settings.role == "shard":
            e = engine(settings.index_name)
            return {
                "status": "ready",
                "epoch": epoch["epoch"],
                "shard_id": settings.shard_id,
                "stats_fingerprint": epoch["stats_fingerprint"],
                "generation": e.index.snapshot.generation,
                "model_fingerprint": e.index.config["fingerprint"],
            }
        if any(i.config["dimension"] and provider is None for i in indexes.values()):
            raise PrismError(
                "SEMANTIC_UNAVAILABLE", provider_error or "Required provider unavailable", 503
            )
        return {
            "status": "ready",
            "loaded_indexes": list(indexes),
            "semantic_available": provider is not None,
            "semantic_error": provider_error,
        }

    @app.get("/metrics")
    async def metrics():
        loaded = [index.stats() for index in indexes.values()]
        INDEX_BYTES.set(sum(sum(s["bytes_by_file"].values()) for s in loaded))
        LIVE_DOCS.set(sum(s["live_documents"] for s in loaded))
        PENDING_REVISIONS.set(sum(s["accepted_revision"] - s["visible_revision"] for s in loaded))
        return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

    @app.post("/v1/indexes", status_code=201)
    async def create(req: CreateRequest):
        if settings.role != "local":
            raise PrismError("DISTRIBUTED_WRITES_UNSUPPORTED", "Use prism cluster build", 409)
        if req.semantic and provider is None:
            raise PrismError("SEMANTIC_UNAVAILABLE", "Provision local model first", 503)
        with Index(
            index_path(settings, req.name),
            create=True,
            dimension=provider.dimension if req.semantic else 0,
            fingerprint=provider.fingerprint if req.semantic else None,
            codec=req.codec,
        ):
            pass
        return {"name": req.name}

    @app.get("/v1/indexes/{name}/stats")
    async def stats(name: str):
        return await work(engine(name).index.stats)

    @app.post("/v1/indexes/{name}/documents:bulk")
    async def bulk(name: str, req: BulkRequest):
        return await work(engine(name).index.ingest, req.documents, provider)

    @app.delete("/v1/indexes/{name}/documents/{external:path}")
    async def delete(name: str, external: str):
        return await work(engine(name).index.delete, external)

    @app.post("/v1/indexes/{name}/refresh")
    async def refresh(name: str, req: RefreshRequest):
        return await work(engine(name).index.refresh, req.through_revision)

    @app.post("/v1/indexes/{name}/compact")
    async def compact(name: str):
        return await work(engine(name).index.compact)

    @app.post("/v1/indexes/{name}/search")
    async def search(name: str, req: SearchRequest):
        if coordinator:
            if name != coordinator.epoch["index"]:
                raise PrismError("INDEX_NOT_FOUND", "Unknown coordinator index", 404)
            return await asyncio.wait_for(coordinator.search(req), req.timeout_ms / 1000 + 0.05)
        return await work(engine(name).search, req, timeout=req.timeout_ms / 1000 + 0.05)

    @app.post("/internal/{name}/branches", include_in_schema=False)
    async def branches(name: str, req: BranchRequest):
        if (
            settings.role != "shard"
            or req.epoch != epoch["epoch"]
            or req.model_fingerprint != epoch["config"]["fingerprint"]
        ):
            raise PrismError("EPOCH_MISMATCH", "Matching shard epoch required", 409)
        e = engine(name)
        data = await work(
            lambda: e.branches(req.request, query_vector=req.query_vector),
            timeout=req.request.timeout_ms / 1000 + 0.05,
        )
        return {
            **data,
            "epoch": epoch["epoch"],
            "shard_id": settings.shard_id,
            "stats_fingerprint": epoch["stats_fingerprint"],
            "model_fingerprint": e.index.config["fingerprint"],
        }

    return app
