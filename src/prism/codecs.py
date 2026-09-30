"""Bounded, lossless posting codecs and block-aware iterators."""

import bisect
import struct
from dataclasses import dataclass

from prism.errors import PrismError

MAX_U64 = (1 << 64) - 1


def corrupt(message):
    raise PrismError("INDEX_CORRUPT", message, 503)


def varint(value: int) -> bytes:
    if not 0 <= value <= MAX_U64:
        raise ValueError("unsigned 64-bit integer required")
    out = bytearray()
    while value >= 128:
        out.append((value & 127) | 128)
        value >>= 7
    out.append(value)
    return bytes(out)


def read_varint(data: bytes, offset: int = 0) -> tuple[int, int]:
    value = 0
    for shift in range(0, 70, 7):
        if offset >= len(data):
            corrupt("Truncated variable integer")
        b = data[offset]
        offset += 1
        if shift == 63 and b > 1:
            corrupt("Variable integer overflow")
        value |= (b & 127) << shift
        if b < 128:
            return value, offset
    corrupt("Oversized variable integer")
    raise AssertionError


def dictionary_encode(records: list[tuple[str, int]]) -> bytes:
    out = bytearray(b"PRD1")
    out.extend(varint(len(records)))
    previous = b""
    for i, (term, location) in enumerate(records):
        raw = term.encode()
        prefix = 0
        if i % 32:
            while prefix < min(len(raw), len(previous)) and raw[prefix] == previous[prefix]:
                prefix += 1
        suffix = raw[prefix:]
        out.extend(varint(prefix) + varint(len(suffix)) + suffix + varint(location))
        previous = raw
    return bytes(out)


def dictionary_decode(data: bytes) -> list[tuple[str, int]]:
    if data[:4] != b"PRD1":
        corrupt("Unsupported dictionary format")
    count, off = read_varint(data, 4)
    if count > len(data):
        corrupt("Invalid dictionary count")
    previous = b""
    records: list[tuple[str, int]] = []
    for i in range(count):
        prefix, off = read_varint(data, off)
        size, off = read_varint(data, off)
        if prefix > len(previous) or off + size > len(data) or (i % 32 == 0 and prefix):
            corrupt("Invalid dictionary prefix or suffix")
        raw = previous[:prefix] + data[off : off + size]
        off += size
        location, off = read_varint(data, off)
        try:
            term = raw.decode()
        except UnicodeDecodeError:
            corrupt("Invalid UTF-8 dictionary")
        if records and term <= records[-1][0]:
            corrupt("Dictionary out of order")
        records.append((term, location))
        previous = raw
    if off != len(data):
        corrupt("Trailing dictionary bytes")
    return records


@dataclass(frozen=True)
class Posting:
    doc_id: int
    positions: tuple[int, ...]


def write_postings(postings: list[Posting], codec: str = "compressed"):
    out, positions = bytearray(), bytearray()
    blocks = []
    for start in range(0, len(postings), 128):
        chunk = postings[start : start + 128]
        blocks.append(
            {
                "first": chunk[0].doc_id,
                "last": chunk[-1].doc_id,
                "offset": len(out),
                "count": len(chunk),
            }
        )
        previous = 0
        for p in chunk:
            if not p.positions or p.doc_id < previous:
                raise ValueError("Invalid posting")
            pos_off = len(positions)
            prev_pos = 0
            for pos in p.positions:
                if pos < prev_pos:
                    raise ValueError("Positions must be ordered")
                positions.extend(
                    varint(pos - prev_pos) if codec == "compressed" else struct.pack("<Q", pos)
                )
                prev_pos = pos
            if codec == "compressed":
                out.extend(varint(p.doc_id - previous) + varint(len(p.positions)) + varint(pos_off))
            else:
                out.extend(struct.pack("<QQQ", p.doc_id, len(p.positions), pos_off))
            previous = p.doc_id
    return bytes(out), bytes(positions), blocks


class PostingIterator:
    def __init__(self, data: bytes, positions: bytes, blocks: list[dict], codec="compressed"):
        self.data, self.position_data, self.blocks, self.codec = data, positions, blocks, codec
        self.block = -1
        self.rows: list[tuple[int, int, int]] = []
        self.row = -1
        self.blocks_decoded = 0
        self.blocks_skipped = 0

    def _load(self, i):
        self.block, self.rows, self.row = i, [], -1
        if i >= len(self.blocks):
            return
        b = self.blocks[i]
        if not 1 <= b["count"] <= 128:
            corrupt("Invalid posting block count")
        off, prev = b["offset"], 0
        for _ in range(b["count"]):
            if self.codec == "compressed":
                gap, off = read_varint(self.data, off)
                tf, off = read_varint(self.data, off)
                pos, off = read_varint(self.data, off)
                doc = prev + gap
            else:
                if off + 24 > len(self.data):
                    corrupt("Truncated plain posting")
                doc, tf, pos = struct.unpack_from("<QQQ", self.data, off)
                off += 24
            if (
                not tf
                or (self.rows and doc <= prev)
                or tf > len(self.position_data)
                or pos >= len(self.position_data)
            ):
                corrupt("Invalid posting record")
            self.rows.append((doc, tf, pos))
            prev = doc
        if self.rows[0][0] != b["first"] or self.rows[-1][0] != b["last"]:
            corrupt("Invalid block bounds")
        self.blocks_decoded += 1

    @property
    def current(self):
        return self.rows[self.row] if 0 <= self.row < len(self.rows) else None

    def next(self):
        self.row += 1
        if self.row >= len(self.rows):
            self._load(self.block + 1)
            self.row = 0
        return self.current

    def advance(self, target):
        if self.current is not None and self.current[0] >= target:
            return self.current
        i = max(0, self.block)
        while i < len(self.blocks) and self.blocks[i]["last"] < target:
            self.blocks_skipped += 1
            i += 1
        if i != self.block:
            self._load(i)
        self.row = bisect.bisect_left([r[0] for r in self.rows], target)
        return self.current

    def positions(self):
        if self.current is None:
            return ()
        _, tf, off = self.current
        out: list[int] = []
        previous = 0
        for _ in range(tf):
            if self.codec == "compressed":
                gap, off = read_varint(self.position_data, off)
                value = previous + gap
            else:
                if off + 8 > len(self.position_data):
                    corrupt("Truncated plain position")
                value = struct.unpack_from("<Q", self.position_data, off)[0]
                off += 8
            if out and value <= previous:
                corrupt("Positions out of order")
            out.append(value)
            previous = value
        return tuple(out)
