import json
import math
import random

import numpy as np
import pytest
from hypothesis import given
from hypothesis import strategies as st
from pydantic import ValidationError

from prism.codecs import (
    Posting,
    PostingIterator,
    dictionary_decode,
    dictionary_encode,
    read_varint,
    varint,
    write_postings,
)
from prism.deadline import Deadline
from prism.errors import PrismError
from prism.expansion import BKTree, GramIndex, distance
from prism.query import Executor, Parser
from prism.search import SearchEngine, fuse
from prism.storage import Index

DOCS = [
    {
        "id": "a",
        "title": "Bluetooth earbuds",
        "body": "Wireless music listening",
        "kind": "product",
        "rating": 4.5,
    },
    {
        "id": "b",
        "title": "AC repair",
        "body": "Air conditioner not cooling repair",
        "kind": "service",
        "rating": 4.8,
    },
    {
        "id": "c",
        "title": "Distributed systems",
        "body": "reliability database storage",
        "rating": 4.2,
    },
    {"id": "d", "title": "Beginner storage", "body": "distributed new systems", "rating": 2},
]


@given(st.integers(min_value=0, max_value=2**64 - 1))
def test_varint(value):
    raw = varint(value)
    assert read_varint(raw) == (value, len(raw))


@pytest.mark.parametrize("raw", [b"\x80", b"\xff" * 10, b"\x80" * 20])
def test_varint_corruption(raw):
    with pytest.raises(PrismError):
        read_varint(raw)


@given(st.lists(st.integers(min_value=0, max_value=100000), unique=True, max_size=300))
def test_postings_roundtrip_and_skip(ids):
    values = [Posting(x, (0, 5, 20)) for x in sorted(ids)]
    for codec in ("plain", "compressed"):
        raw, pos, blocks = write_postings(values, codec)
        iterator = PostingIterator(raw, pos, blocks, codec)
        decoded = []
        while (row := iterator.next()) is not None:
            decoded.append(Posting(row[0], iterator.positions()))
        assert decoded == values
        iterator = PostingIterator(raw, pos, blocks, codec)
        for target in sorted(ids)[::7]:
            assert iterator.advance(target)[0] == target
            assert iterator.positions() == (0, 5, 20)


def test_dictionary_and_compression():
    records = [(f"title\0database-{i:04}", i) for i in range(100)]
    assert dictionary_decode(dictionary_encode(records)) == records
    postings = [Posting(i, (0, 2, 4)) for i in range(1000)]
    compressed = write_postings(postings)
    plain = write_postings(postings, "plain")
    assert len(compressed[0]) + len(compressed[1]) < len(plain[0]) + len(plain[1])
    it = PostingIterator(*compressed[:2], compressed[2])
    assert it.advance(950)[0] == 950
    assert it.blocks_skipped >= 7


@pytest.fixture
def index(tmp_path):
    with Index(tmp_path / "index", create=True) as index:
        index.ingest(DOCS)
        index.refresh()
        yield index


def ids(engine, q, **options):
    return [
        h["id"]
        for h in engine.search({"q": q, "mode": "lexical", "timeout_ms": 5000, **options})["hits"]
    ]


@pytest.mark.parametrize(
    "q,expected",
    [
        ('"distributed systems" AND reliability', {"c"}),
        ("title:(database OR storage)", {"d"}),
        ("rating:>=4 AND NOT kind:product", {"b", "c"}),
        ("NOT beginner", {"a", "b", "c"}),
        ("storage OR earbuds AND kind:product", {"a", "c", "d"}),
        ('title:"distributed systems"', {"c"}),
        ('"systems reliability"', set()),
        ("tags:missing", set()),
        ("rating:>=4 rating:<3", set()),
    ],
)
def test_dsl(index, q, expected):
    assert set(ids(SearchEngine(index), q, query_mode="dsl")) == expected


@pytest.mark.parametrize(
    "q",
    [
        '"unclosed',
        "x AND",
        "()",
        "foo:",
        "rating:abc",
        "missing:foo",
        "title:(body:foo)",
        "x )",
        "NOT",
        "(" * 20 + "x" + ")" * 20,
    ],
)
def test_invalid_queries(q):
    with pytest.raises(PrismError):
        Parser(q).parse()


def test_plain_text_is_not_dsl(index):
    assert set(ids(SearchEngine(index), "storage OR earbuds", typo_tolerance=False)) == {
        "a",
        "c",
        "d",
    }


def test_typo_and_phrase_synonym(index, tmp_path):
    engine = SearchEngine(index)
    assert ids(engine, "bluetooh") == ["a"]
    assert ids(engine, "bluetooh", typo_tolerance=False) == []
    path = tmp_path / "synonyms.json"
    path.write_text(
        json.dumps(
            {
                "version": "test",
                "rules": [{"from": "chill room", "to": ["ac repair"], "direction": "equivalent"}],
            }
        )
    )
    engine = SearchEngine(index, synonyms=path)
    assert ids(engine, "chill room") == ["b"]
    assert ids(engine, '"chill room"') == []


def test_bm25_reference(tmp_path):
    with Index(tmp_path / "i", create=True) as index:
        index.ingest([{"id": "a", "title": "term term"}, {"id": "b", "title": "other"}])
        index.refresh()
        hit = SearchEngine(index).search(
            {"q": "term", "mode": "lexical", "typo_tolerance": False, "explain": True}
        )["hits"][0]
        expected = 2 * math.log(2) * 2 * 2.2 / (2 + 1.2 * (0.25 + 0.75 * 2 / 1.5))
        assert hit["score"] == pytest.approx(expected)
        assert hit["explanation"]["bm25"][0]["df"] == 1


def test_visibility_updates_merge_restart_and_writer_lock(tmp_path):
    path = tmp_path / "i"
    with Index(path, create=True) as index:
        index.ingest(DOCS)
        assert index.snapshot.docs == {}
        with pytest.raises(PrismError, match="Another writer"):
            Index(path)
        index.refresh()
        pinned = index.snapshot
        index.ingest([{"id": "a", "title": "Replacement"}])
        index.delete("b")
        index.refresh()
        assert "b" not in index.snapshot.docs
        assert pinned.docs["a"]["title"] == "Bluetooth earbuds"
        index.delete("a")
        index.refresh()
        index.ingest([{"id": "a", "title": "Reinserted"}])
        index.refresh()
        before = SearchEngine(index).search({"q": "storage", "mode": "lexical"})["hits"]
        index.compact()
        assert SearchEngine(index).search({"q": "storage", "mode": "lexical"})["hits"] == before
        assert index.snapshot.docs["a"]["title"] == "Reinserted"
        index.ingest([{"id": "tail", "title": "Durable tail"}])
    with Index(path) as restarted:
        assert "tail" not in restarted.snapshot.docs
        restarted.refresh()
        assert "tail" in restarted.snapshot.docs


@pytest.mark.parametrize(
    "point,published",
    [
        ("segment_written", False),
        ("manifest_written", False),
        ("before_publish", False),
        ("after_publish", True),
    ],
)
def test_publication_fault_boundary(tmp_path, point, published):
    path = tmp_path / "i"
    index = Index(path, create=True)
    index.ingest(DOCS)

    def fail(stage):
        if stage == point:
            raise RuntimeError("injected crash boundary")

    index.hook = fail
    with pytest.raises(RuntimeError):
        index.refresh()
    index.close()
    with Index(path) as restarted:
        assert bool(restarted.snapshot.docs) == published
        restarted.refresh()
        assert len(restarted.snapshot.docs) == 4


def test_corrupt_segment_refused(tmp_path):
    path = tmp_path / "i"
    with Index(path, create=True) as index:
        index.ingest(DOCS)
        index.refresh()
        file = next((path / "segments").glob("*/postings.bin"))
    file.write_bytes(file.read_bytes() + b"garbage")
    with pytest.raises(PrismError, match="checksum"):
        Index(path)


def test_bulk_atomicity(index):
    before = index.durable_revision()
    with pytest.raises(ValidationError):
        index.ingest([{"id": "new", "title": "Valid"}, {"id": "bad", "title": ""}])
    assert index.durable_revision() == before
    with pytest.raises(PrismError):
        index.ingest([DOCS[0], DOCS[0]])
    assert index.durable_revision() == before


def test_bk_tree_reference():
    vocabulary = ["earbuds", "earbud", "earful", "bluetooth", "blue", "music", "muse"]
    tree = BKTree(vocabulary)
    for query in vocabulary + ["bluetooh", "musci", "erbud"]:
        for radius in (0, 1, 2):
            matches, limited, _ = tree.search(query, radius)
            assert not limited
            assert set(matches) == {
                (t, distance(t, query)) for t in vocabulary if distance(t, query) <= radius
            }


def test_large_vocabulary_fuzzy_candidates_are_bounded():
    vocabulary = ["bluetooth", "earbuds", "headphones", "music"]
    vocabulary += [f"token{i:05d}" for i in range(6000)]
    lookup = GramIndex(vocabulary)
    matches, _, visited = lookup.search("bluetooh", 2)
    assert ("bluetooth", 1) in matches
    assert visited <= 2000


class TestOnlyProvider:
    """Explicit test vectors; these are never semantic evidence."""

    fingerprint = "TEST-ONLY-vectors"
    dimension = 2

    def encode_documents(self, texts):
        return np.array([[1, 0] if "Ear" in t else [0, 1] for t in texts], dtype=np.float32)

    def encode_query(self, text):
        return np.array([1, 0], dtype=np.float32)


def test_vectors_prefilter_hard_dsl_and_fusion(tmp_path):
    provider = TestOnlyProvider()
    with Index(tmp_path / "v", create=True, dimension=2, fingerprint=provider.fingerprint) as index:
        index.ingest(
            [
                {"id": "a", "title": "Ears", "kind": "product"},
                {"id": "b", "title": "Repair", "kind": "service"},
            ],
            provider,
        )
        index.refresh()
        engine = SearchEngine(index, provider)
        assert engine.search({"q": "unknown", "mode": "semantic"})["hits"][0]["id"] == "a"
        response = engine.search(
            {"q": "unknown", "mode": "semantic", "filters": [{"field": "kind", "value": "service"}]}
        )
        assert [h["id"] for h in response["hits"]] == ["b"]
        assert (
            engine.search({"q": "Repair", "query_mode": "dsl", "mode": "hybrid"})["hits"][0]["id"]
            == "b"
        )
        index.delete("a")
        index.refresh()
        assert engine.search({"q": "unknown", "mode": "semantic"})["hits"][0]["id"] == "b"
    hits, ranks = fuse([("a", 100), ("b", 90)], [("b", 0.9), ("c", 0.8)])
    assert hits[0][0] == "b"
    assert hits[0][1] == pytest.approx(1 / 62 + 1 / 61)
    assert ranks["b"] == {"lexical": 2, "semantic": 1}


def test_boolean_oracle(index):
    rng = random.Random(18)
    for _ in range(100):
        left, right = rng.sample(["storage", "earbuds", "repair", "distributed"], 2)
        op = rng.choice(["AND", "OR"])
        negative = rng.choice([False, True])
        q = f"{left} {op} {'NOT ' if negative else ''}{right}"
        expected = set()
        for d in index.snapshot.docs.values():
            tokens = (d["title"] + " " + d["body"]).casefold().split()
            a, b = left in tokens, right in tokens
            b = not b if negative else b
            if (a and b) if op == "AND" else (a or b):
                expected.add(d["id"])
        actual = Executor(index.snapshot, Deadline(5000)).evaluate(Parser(q).parse())
        assert actual == expected


def test_fallback_is_explicit(index):
    with pytest.raises(PrismError):
        SearchEngine(index).search({"q": "earbuds", "mode": "hybrid"})
    result = SearchEngine(index).search(
        {"q": "earbuds", "mode": "hybrid", "allow_lexical_fallback": True}
    )
    assert result["degraded"] and result["executed_mode"] == "lexical"
