# Architecture

The local engine owns one writer per data directory. SQLite WAL holds accepted mutations and optional embeddings. `refresh` coalesces the accepted tail into an immutable segment, builds a live ID map and live statistics, then atomically replaces `CURRENT`. Queries pin one fully loaded snapshot; merges rewrite live versions and publish a new generation without changing logical revision. Old segment files remain until explicit writer-serialized garbage collection.

The index is Prism's own postings, dictionary, and position format. SQLite supplies durable source state, never lexical search. Text analysis uses Unicode NFKC, casefold, and letter/digit tokens with positions. A compressed segment has field-term postings; dictionary restart blocks and posting skip blocks reduce decoding work.

A request is validated, parsed as DSL or plain text, restricted by hard filters, expanded through bounded fuzzy/synonym alternatives, and scored with live per-field BM25. Eligible vectors are scanned for exact cosine top candidates. Reciprocal rank fusion combines candidate ranks. Explanations show computation, not a generated claim about the model's reasoning.

The static cluster builder routes external IDs with the first eight bytes of SHA-256 modulo shard count. It exports three shard snapshots and copies each snapshot to a replica. The epoch records global live corpus statistics, schema/model identity, routing, and expected generations. Each shard uses global BM25 statistics and returns separate lexical/vector candidates. The coordinator merges branch lists globally before fusion, retries one matching replica within the budget, and reports missing coverage.

Python keeps the storage algorithms inspectable; a real local pretrained model supplies meaning-sensitive retrieval; exact vectors make the reference corpus verifiable; static snapshots bound the cluster design. A corpus, model, or routing change requires a new export. Automatic election, live distributed ingestion, and approximate nearest-neighbor graphs are stretch work.

