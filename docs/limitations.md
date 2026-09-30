# Current limits

- The reference workload is roughly 5,000 short English documents. Exact vector retrieval scans all eligible live rows with linear CPU work; 50,000 synthetic documents are only a scale experiment.
- The pretrained model truncates long inputs. Prism lexically indexes all accepted text but embeds only the model-visible prefix. In the SciFact build, 3,681 of 5,183 documents exceeded the model's 256-token limit. No chunking or reranking is implemented.
- Embeddings and synonyms can misrank. Plain-language negation, ambiguous abbreviations, short misspellings, model numbers, and out-of-domain requests remain difficult. Similarity scores are not calibrated confidence.
- Hybrid fusion ranks only top candidates from each branch, so it approximates full-corpus fusion.
- The cluster is static and read-only after export. It has been tested on one host with real processes; live replication, election, reassignment, zero-downtime mixed-epoch rollout, and multi-host SLAs are not claimed.
- Each manifest embeds the live map and corpus statistics. Startup loads segment data and vectors into memory. Large indexes need a different memory/dictionary layout and more sophisticated segment management.
- The source journal retains revisions for rebuild. Pending unrefreshed mutations are limited to 100,000; a production retention policy is future work.
- The authored marketplace fixture contains templated variations. Its quality numbers demonstrate behavior, not performance for a new business domain. SciFact concerns scientific documents; OpenSearch is a lexical comparison and analyzer differences can affect rankings.
- The API binds to loopback. Authentication, authorization, TLS termination, and multi-tenant isolation are outside the release.
- Hardware-dependent targets in the spec are not guarantees. On the 5,183-document SciFact snapshot, the 1,000-request local HTTP smoke missed the single-client lexical p95 target (396 ms measured versus 75 ms target) and hybrid p95 target (374 ms versus 250 ms); at four and sixteen clients, deadline and overload errors were frequent. See the [report](benchmark-report.md) for the full counts, request settings, and interpretation.

