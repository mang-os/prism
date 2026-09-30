# Storage format 1

Numeric bytes use little-endian order. Checksums are SHA-256. `CURRENT` holds a manifest filename and hash. A manifest embeds segment metadata hashes, an external-ID live map, analyzer/model/codec configuration, field corpus statistics, and visible revision. Segment files are immutable.

| File | Format |
| --- | --- |
| `terms.dict` | `PRD1`, unsigned varint count, then prefix byte length, suffix byte length, UTF-8 suffix, metadata-array index. Every 32nd term restarts with prefix zero. |
| `postings.bin` | Field-term posting lists in <=128-document blocks. Compressed rows are document gap, term frequency, and position offset as varints; plain rows are three `<Q` values. |
| `positions.bin` | Position gaps as varints, reset per posting. Plain mode uses `<Q` absolute positions. |
| `skips.json` | Per-term byte ranges and per-block count, first/last document ID, and byte offset. `advance` skips blocks whose last ID is below its target. |
| `documents.jsonl` | Stored canonical document and mutation revision, one line per indexed version. |
| `doc-offsets.bin` | `<Q` offsets into stored documents. |
| `field-lengths.bin` | `<II` title/body token counts per local document. |
| `mutations.jsonl` | Final upsert/delete operation per external ID within the flush interval. |
| `vectors.f32` | Optional contiguous little-endian float32 normalized vectors. |
| `vector-docids.bin` | `<Q` segment-local document ID for each vector row. |
| `segment.meta.json` | Format/analyzer version, embedding identity, codec, and file checksums. |

Segment-local document IDs begin at zero. The first delta in each block uses zero as its base; later IDs increase. Positions begin at zero and increase within one field/document. Decoders bound varint length, offsets, sizes, counts, dictionary order, and checksums. Unknown major formats fail startup.

The SQLite source file uses WAL and full synchronous transactions. An accepted revision is durable; it becomes searchable after refresh. A flush writes under `staging`, renames the finished segment, writes and syncs a manifest, then atomically replaces `CURRENT`. A crash before pointer replacement leaves the older view searchable. After replacement the complete new view reopens. Unpublished staging is ignored and the journal tail remains available. Tests inject failures at these boundaries.

The live map and statistics are currently embedded in each manifest, and skip metadata is JSON. This favors inspection on the reference corpus but costs memory and manifest rewrite time at scale. Both codecs are retained for comparison. The source journal keeps accepted revisions for rebuild and has a bounded pending tail.

