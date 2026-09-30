# Saved benchmark summaries

These small JSON files preserve measured metadata and summary metrics in the repository. The full per-query runs and static HTML reports are generated under ignored `artifacts/` because each public-corpus report includes hundreds of megabytes of stored result documents. Recreate them with the benchmark commands in the root README, then use `benchmarks/export_summary.py` to verify or regenerate a summary. The source report SHA-256 is recorded in each JSON file.

The SciFact summary with OpenSearch uses the first completed Prism run. The optimized Prism rerun used the same index, model, queries, and qrels and produced identical top-100 rankings for all six modes; its separate summary has no OpenSearch rerun. Neither summary implies that the expensive full-corpus embedding pass was repeated for every relevance run.

The other saved JSON files contain the fresh semantic and lexical indexing timings, six-setting HTTP load smoke, and clearly labeled 50,000-row synthetic scale probe. Hardware paths inside raw measurement metadata identify the Windows host used for this run and are not portable configuration values.
