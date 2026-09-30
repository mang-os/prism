# Relevance and evaluation

Prism uses per-field BM25 (`k1=1.2`, `b=0.75`, title weight 2, body weight 1) with live corpus statistics. Plain queries add bounded fuzzy alternatives (weight 0.7) and curated synonyms (weight 0.8). An expansion group uses its strongest matching alternative to avoid inflated scores. Phrase constraints use positions. Filters do not add score. Small vocabularies use a BK-tree; vocabularies over 5,000 terms use bounded bigram candidates followed by exact Levenshtein checks. This can miss a neighbor when the candidate cap is reached, signaled by `expansion_limited`; see [decision 0002](decisions/0002-bounded-fuzzy-candidates.md).

The real provider is `sentence-transformers/all-MiniLM-L6-v2`, revision `1110a243fdf4706b3f48f1d95db1a4f5529b4d41` in the measured run. It encodes `title + newline + body` into a 384-dimensional unit vector. The model truncates long inputs; the provider counts those documents. A fingerprint and dimension are stored in vector indexes. Mismatched models require re-embedding.

Exact cosine search scans eligible live rows. Keyword and dense top candidate lists are fused through `1/(60+rank)` per branch, with global branch ranks in cluster mode. Scores are not probabilities. Candidate truncation makes fusion approximate relative to all-corpus fusion, even though the vector scan itself is exact over eligible rows.

The original marketplace fixture was authored before engine outputs were inspected: 180 documents, 90 queries, 30 intent groups, 10 held out. The 30 held-out queries cover exact, typo, and semantic paraphrases. Grades assign 2 to relevant variants and 0 to unlisted documents. Variations are templated, so this evaluates demonstration behavior rather than generalization to a new marketplace. SciFact uses original IDs/test qrels and a checked archive; it tests scientific-document retrieval.

Metrics: nDCG@10 uses `2^grade-1` gain; MRR, recall, and success treat positive grades as relevant. Unjudged documents count as nonrelevant; no-positive-judgment queries score zero and are counted. Intent groups stay disjoint across development and held-out splits. Runs, settings, and per-query ranks are saved under ignored `artifacts/`. See [measured results](benchmark-report.md).

