"""Durable source journal and immutable custom binary lexical segments."""

import hashlib
import json
import os
import shutil
import sqlite3
import struct
import threading
import uuid
from collections import defaultdict
from pathlib import Path
from typing import Any

import numpy as np

from prism.analysis import TEXT_FIELDS, VERSION, analyze
from prism.codecs import (
    Posting,
    PostingIterator,
    dictionary_decode,
    dictionary_encode,
    write_postings,
)
from prism.errors import PrismError
from prism.models import Document


def digest(path: Path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def durable_write(path: Path, data: bytes):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())


def encode_json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def checked_json(path):
    try:
        return json.loads(path.read_bytes())
    except (ValueError, OSError) as e:
        raise PrismError("INDEX_CORRUPT", f"Cannot read {path.name}", 503) from e


class WriterLock:
    def __init__(self, path):
        self.file = path.open("a+b")
        self.file.seek(0, 2)
        if self.file.tell() == 0:
            self.file.write(b"0")
            self.file.flush()
        self.file.seek(0)
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(self.file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(self.file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)  # type: ignore[attr-defined]
        except OSError as e:
            self.file.close()
            raise PrismError("WRITER_CONFLICT", "Another writer owns this index", 409) from e

    def close(self):
        if not self.file.closed:
            self.file.close()


def build_segment(root, rows, config):
    sid = "seg-" + uuid.uuid4().hex
    staging = root / "staging" / sid
    staging.mkdir(parents=True)
    records: list[dict] = []
    inverted = defaultdict(list)
    documents, offsets, lengths, vector_ids, vectors = bytearray(), [], [], [], []
    mutations = []
    for row in sorted(rows, key=lambda r: r["id"]):
        mutations.append(
            {"id": row["id"], "revision": row["revision"], "deleted": row["document"] is None}
        )
        if row["document"] is None:
            continue
        local = len(records)
        records.append(row)
        offsets.append(len(documents))
        documents.extend(
            encode_json({"document": row["document"], "revision": row["revision"]}) + b"\n"
        )
        field_lengths = []
        for field in TEXT_FIELDS:
            tokens = analyze(row["document"][field])
            field_lengths.append(len(tokens))
            per_term = defaultdict(list)
            for token in tokens:
                per_term[token.term].append(token.position)
            for term, term_positions in per_term.items():
                inverted[field + "\0" + term].append(Posting(local, tuple(term_positions)))
        lengths.append(field_lengths)
        if config["dimension"]:
            vector = np.asarray(row["vector"], dtype="<f4")
            if (
                vector.shape != (config["dimension"],)
                or not np.isfinite(vector).all()
                or not np.linalg.norm(vector)
            ):
                raise PrismError("INVALID_VECTOR", "Invalid persisted embedding", 422)
            vectors.append(vector)
            vector_ids.append(local)
    postings, positions = bytearray(), bytearray()
    term_meta: list[dict] = []
    dictionary = []
    for key, values in sorted(inverted.items()):
        p, pos, blocks = write_postings(values, config["codec"])
        dictionary.append((key, len(term_meta)))
        term_meta.append(
            {
                "posting_offset": len(postings),
                "posting_size": len(p),
                "position_offset": len(positions),
                "position_size": len(pos),
                "blocks": blocks,
            }
        )
        postings.extend(p)
        positions.extend(pos)
    files = {
        "documents.jsonl": bytes(documents),
        "doc-offsets.bin": b"".join(struct.pack("<Q", x) for x in offsets),
        "field-lengths.bin": b"".join(struct.pack("<II", *x) for x in lengths),
        "terms.dict": dictionary_encode(dictionary),
        "postings.bin": bytes(postings),
        "positions.bin": bytes(positions),
        "skips.json": encode_json(term_meta),
        "mutations.jsonl": b"".join(encode_json(x) + b"\n" for x in mutations),
        "vectors.f32": np.asarray(vectors, dtype="<f4").tobytes(),
        "vector-docids.bin": b"".join(struct.pack("<Q", x) for x in vector_ids),
    }
    for name, data in files.items():
        durable_write(staging / name, data)
    meta = {
        "format": 1,
        "analyzer": VERSION,
        "config": config,
        "checksums": {name: digest(staging / name) for name in files},
    }
    durable_write(staging / "segment.meta.json", encode_json(meta))
    destination = root / "segments" / sid
    destination.parent.mkdir(exist_ok=True)
    staging.rename(destination)
    return sid, records


class SegmentReader:
    def __init__(self, root: Path):
        self.root = root
        self.meta = checked_json(root / "segment.meta.json")
        if self.meta.get("format") != 1 or self.meta.get("analyzer") != VERSION:
            raise PrismError("FORMAT_MISMATCH", "Unsupported segment format or analyzer", 409)
        try:
            for name, expected in self.meta["checksums"].items():
                if Path(name).name != name or digest(root / name) != expected:
                    raise PrismError("INDEX_CORRUPT", "Segment checksum mismatch", 503)
            self.documents = [
                json.loads(x) for x in (root / "documents.jsonl").read_bytes().splitlines()
            ]
            self.dictionary = dict(dictionary_decode((root / "terms.dict").read_bytes()))
            self.postings = (root / "postings.bin").read_bytes()
            self.positions = (root / "positions.bin").read_bytes()
            self.skips = checked_json(root / "skips.json")
            self.lengths = [
                x for x in struct.iter_unpack("<II", (root / "field-lengths.bin").read_bytes())
            ]
            ids = [
                x[0] for x in struct.iter_unpack("<Q", (root / "vector-docids.bin").read_bytes())
            ]
            dim = self.meta["config"]["dimension"]
            raw = (root / "vectors.f32").read_bytes()
            if dim and len(raw) != len(ids) * dim * 4:
                raise ValueError("Invalid vector file length")
            rows: Any = np.frombuffer(raw, dtype="<f4").reshape((-1, dim)) if dim else []
            self.vectors = dict(zip(ids, rows, strict=True))
            if len(self.documents) != len(self.lengths):
                raise ValueError("Field length count mismatch")
        except (OSError, ValueError, KeyError, struct.error) as e:
            raise PrismError("INDEX_CORRUPT", "Invalid segment files", 503) from e

    def iterator(self, field, term):
        index = self.dictionary.get(field + "\0" + term)
        if index is None:
            return PostingIterator(b"", b"", [])
        m = self.skips[index]
        return PostingIterator(
            self.postings[m["posting_offset"] : m["posting_offset"] + m["posting_size"]],
            self.positions[m["position_offset"] : m["position_offset"] + m["position_size"]],
            m["blocks"],
            self.meta["config"]["codec"],
        )


class SearchSnapshot:
    def __init__(self, root: Path):
        pointer = checked_json(root / "CURRENT")
        filename = pointer["manifest"]
        if Path(filename).name != filename:
            raise PrismError("INDEX_CORRUPT", "Invalid manifest path", 503)
        manifest_path = root / "manifests" / filename
        if digest(manifest_path) != pointer["checksum"]:
            raise PrismError("INDEX_CORRUPT", "Manifest checksum mismatch", 503)
        self.manifest = checked_json(manifest_path)
        if self.manifest["format"] != 1 or self.manifest["config"]["analyzer"] != VERSION:
            raise PrismError("FORMAT_MISMATCH", "Unsupported manifest", 409)
        self.config = self.manifest["config"]
        self.generation = self.manifest["generation"]
        self.revision = self.manifest["revision"]
        self.segments = {}
        for sid, checksum in self.manifest["segments"].items():
            if (
                not sid.startswith("seg-")
                or Path(sid).name != sid
                or digest(root / "segments" / sid / "segment.meta.json") != checksum
            ):
                raise PrismError("INDEX_CORRUPT", "Invalid segment reference", 503)
            self.segments[sid] = SegmentReader(root / "segments" / sid)
        self.live = self.manifest["live"]
        self.stats = self.manifest["stats"]
        self.docs, self.refs, self.vectors = {}, {}, {}
        self.field_lengths = {}
        for external, ref in self.live.items():
            sid, local = ref["segment"], ref["local"]
            record = self.segments[sid].documents[local]
            if record["document"]["id"] != external or record["revision"] != ref["revision"]:
                raise PrismError("INDEX_CORRUPT", "Live document identity mismatch", 503)
            self.docs[external] = record["document"]
            self.field_lengths[external] = dict(
                zip(TEXT_FIELDS, self.segments[sid].lengths[local], strict=True)
            )
            self.refs[(sid, local)] = external
            if self.config["dimension"]:
                self.vectors[external] = self.segments[sid].vectors[local]

    def matches(self, field, term, deadline, positions=False, eligible=None):
        out = {}
        for sid, segment in self.segments.items():
            it = segment.iterator(field, term)
            if eligible is not None and len(eligible) < len(self.docs) // 2:
                targets = sorted(
                    (ref["local"], external)
                    for external in eligible
                    if (ref := self.live.get(external)) and ref["segment"] == sid
                )
                for target, external in targets:
                    deadline.check()
                    row = it.advance(target)
                    if row is not None and row[0] == target:
                        out[external] = it.positions() if positions else row[1]
            else:
                while (row := it.next()) is not None:
                    deadline.check()
                    external = self.refs.get((sid, row[0]))
                    if external is not None and (eligible is None or external in eligible):
                        out[external] = it.positions() if positions else row[1]
        return out


class Index:
    def __init__(
        self,
        path: str | Path,
        *,
        create=False,
        dimension=0,
        fingerprint=None,
        codec="compressed",
        read_only=False,
    ):
        self.path = Path(path)
        self.mutex = threading.RLock()
        self.writer = None
        self.db = None
        self.hook = lambda point: None
        if create:
            if (self.path / "CURRENT").exists():
                raise PrismError("INDEX_EXISTS", "Index already exists", 409)
            self.path.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            raise PrismError("INDEX_NOT_FOUND", "Index not found", 404)
        try:
            if not read_only:
                self.writer = WriterLock(self.path / "writer.lock")
                self.db = sqlite3.connect(self.path / "source.sqlite", check_same_thread=False)
                self.db.execute("PRAGMA journal_mode=WAL")
                self.db.execute("PRAGMA synchronous=FULL")
                self.db.execute(
                    "CREATE TABLE IF NOT EXISTS mutations(revision INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT NOT NULL, document TEXT, vector BLOB)"
                )
                self.db.commit()
            if create:
                if codec not in ("compressed", "plain"):
                    raise ValueError("Unknown codec")
                self.config = {
                    "analyzer": VERSION,
                    "dimension": dimension,
                    "fingerprint": fingerprint,
                    "codec": codec,
                }
                self._publish([], {}, 0)
            self.snapshot = SearchSnapshot(self.path)
            self.config = self.snapshot.config
        except Exception:
            self.close()
            raise

    def _require_writer(self):
        if self.db is None:
            raise PrismError("READ_ONLY", "Snapshot is read-only", 409)

    def ingest(self, documents, provider=None):
        self._require_writer()
        assert self.db is not None
        docs = [d if isinstance(d, Document) else Document.model_validate(d) for d in documents]
        if len(docs) > 1000 or len(encode_json([d.model_dump() for d in docs])) > 8 * 1024 * 1024:
            raise PrismError("BULK_TOO_LARGE", "Batch exceeds document or byte limit", 413)
        if len({d.id for d in docs}) != len(docs):
            raise PrismError("DUPLICATE_ID", "Duplicate IDs within batch", 422)
        vectors = [None] * len(docs)
        if self.config["dimension"]:
            if provider is None or provider.fingerprint != self.config["fingerprint"]:
                raise PrismError("MODEL_MISMATCH", "Matching embedding provider required", 409)
            matrix = provider.encode_documents([d.title + "\n" + d.body for d in docs])
            if (
                matrix.shape != (len(docs), self.config["dimension"])
                or not np.isfinite(matrix).all()
                or np.any(np.linalg.norm(matrix, axis=1) < 1e-6)
            ):
                raise PrismError("INVALID_VECTOR", "Embedding batch rejected", 422)
            vectors = [v.astype("<f4").tobytes() for v in matrix]
        with self.mutex, self.db:
            if self.durable_revision() - self.snapshot.revision + len(docs) > 100000:
                raise PrismError(
                    "OVERLOADED", "Refresh pending journal before accepting more documents", 429
                )
            self.db.executemany(
                "INSERT INTO mutations(id,document,vector) VALUES(?,?,?)",
                [(d.id, json.dumps(d.model_dump()), v) for d, v in zip(docs, vectors, strict=True)],
            )
        self.hook("accepted")
        return {
            "accepted_revision": self.durable_revision(),
            "visible_revision": self.snapshot.revision,
        }

    def delete(self, external):
        self._require_writer()
        assert self.db is not None
        if not external or len(external.encode()) > 128:
            raise PrismError("INVALID_ID", "Invalid document ID", 422)
        with self.mutex, self.db:
            self.db.execute(
                "INSERT INTO mutations(id,document,vector) VALUES(?,NULL,NULL)", (external,)
            )
        return {
            "accepted_revision": self.durable_revision(),
            "visible_revision": self.snapshot.revision,
        }

    def durable_revision(self):
        return (
            self.db.execute("SELECT coalesce(max(revision),0) FROM mutations").fetchone()[0]
            if self.db
            else self.snapshot.revision
        )

    def refresh(self, through_revision=None):
        self._require_writer()
        assert self.db is not None
        with self.mutex:
            high = self.durable_revision()
            if through_revision is not None and through_revision > high:
                raise PrismError("INVALID_REVISION", "Revision has not been accepted", 400)
            if high > self.snapshot.revision:
                coalesced = {}
                for revision, external, doc, vector in self.db.execute(
                    "SELECT revision,id,document,vector FROM mutations WHERE revision>? AND revision<=? ORDER BY revision",
                    (self.snapshot.revision, high),
                ):
                    coalesced[external] = {
                        "id": external,
                        "revision": revision,
                        "document": json.loads(doc) if doc else None,
                        "vector": np.frombuffer(vector, dtype="<f4") if vector else None,
                    }
                sid, rows = build_segment(self.path, list(coalesced.values()), self.config)
                self.hook("segment_written")
                live = dict(self.snapshot.live)
                for external in coalesced:
                    live.pop(external, None)
                for local, row in enumerate(rows):
                    live[row["id"]] = {"segment": sid, "local": local, "revision": row["revision"]}
                self._publish([*self.snapshot.segments, sid], live, high)
                self.snapshot = SearchSnapshot(self.path)
            if len(self.snapshot.segments) > 8:
                self.compact()
        return {"visible_revision": self.snapshot.revision, "generation": self.snapshot.generation}

    def _publish(self, segments, live, revision):
        readers = {s: SegmentReader(self.path / "segments" / s) for s in segments}
        stats: dict[str, dict[str, Any]] = {
            f: {"N": 0, "total_length": 0, "df": {}} for f in TEXT_FIELDS
        }
        for ref in live.values():
            doc = readers[ref["segment"]].documents[ref["local"]]["document"]
            for f in TEXT_FIELDS:
                tokens = analyze(doc[f])
                if tokens:
                    stats[f]["N"] += 1
                    stats[f]["total_length"] += len(tokens)
                for term in {t.term for t in tokens}:
                    stats[f]["df"][term] = stats[f]["df"].get(term, 0) + 1
        generation = uuid.uuid4().hex
        manifest = {
            "format": 1,
            "generation": generation,
            "revision": revision,
            "config": self.config,
            "segments": {
                s: digest(self.path / "segments" / s / "segment.meta.json") for s in segments
            },
            "live": live,
            "stats": stats,
        }
        filename = "manifest-" + generation + ".json"
        manifest_path = self.path / "manifests" / filename
        durable_write(manifest_path, encode_json(manifest))
        self.hook("manifest_written")
        pointer = self.path / "CURRENT.next"
        durable_write(
            pointer, encode_json({"manifest": filename, "checksum": digest(manifest_path)})
        )
        self.hook("before_publish")
        os.replace(pointer, self.path / "CURRENT")
        self.hook("after_publish")

    def compact(self):
        self._require_writer()
        with self.mutex:
            snapshot = self.snapshot
            if len(snapshot.segments) <= 1:
                return {"segments": len(snapshot.segments)}
            selected = sorted(
                snapshot.segments,
                key=lambda s: sum(p.stat().st_size for p in (self.path / "segments" / s).iterdir()),
            )[:4]
            rows = [
                {
                    "id": external,
                    "revision": ref["revision"],
                    "document": snapshot.docs[external],
                    "vector": snapshot.vectors.get(external),
                }
                for external, ref in snapshot.live.items()
                if ref["segment"] in selected
            ]
            sid, records = build_segment(self.path, rows, self.config)
            live = dict(snapshot.live)
            for local, row in enumerate(records):
                live[row["id"]] = {"segment": sid, "local": local, "revision": row["revision"]}
            self._publish(
                [s for s in snapshot.segments if s not in selected] + [sid], live, snapshot.revision
            )
            self.snapshot = SearchSnapshot(self.path)
            # Old files are intentionally retained; explicit offline GC verifies manifests.
            return {"segments": len(self.snapshot.segments)}

    def stats(self):
        snap = self.snapshot
        sizes: dict[str, int] = defaultdict(int)
        for sid in snap.segments:
            for p in (self.path / "segments" / sid).iterdir():
                sizes[p.name] += p.stat().st_size
        return {
            "visible_revision": snap.revision,
            "accepted_revision": self.durable_revision(),
            "generation": snap.generation,
            "live_documents": len(snap.live),
            "segments": len(snap.segments),
            "bytes_by_file": dict(sizes),
            "dimension": self.config["dimension"],
        }

    def garbage_collect(self):
        """Explicit writer-serialized cleanup; loaded snapshots retain all bytes in memory."""
        self._require_writer()
        with self.mutex:
            root = self.path.resolve()
            manifests = sorted(
                (root / "manifests").glob("manifest-*.json"),
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            )
            current = checked_json(root / "CURRENT")["manifest"]
            retained = {current, *(p.name for p in manifests[:2])}
            segments = set()
            for name in retained:
                segments.update(checked_json(root / "manifests" / name)["segments"])
            removed = []
            for folder in (root / "segments", root / "staging"):
                if not folder.exists():
                    continue
                for target in folder.iterdir():
                    if target.name in segments:
                        continue
                    resolved = target.resolve()
                    if (
                        not resolved.is_relative_to(root)
                        or target.is_symlink()
                        or not target.name.startswith("seg-")
                    ):
                        raise PrismError(
                            "UNSAFE_GC_PATH", "Refusing unexpected index cleanup path", 409
                        )
                    shutil.rmtree(resolved)
                    removed.append(str(target.relative_to(root)))
            for target in manifests:
                if target.name not in retained:
                    target.unlink()
            return {"removed": removed, "retained_manifests": sorted(retained)}

    def close(self):
        if self.db is not None:
            self.db.close()
            self.db = None
        if self.writer is not None:
            self.writer.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()
