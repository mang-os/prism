# Dataset provenance

`benchmarks/prepare_scifact.py` downloads BEIR SciFact from the upstream TU Darmstadt URL and checks MD5 `5f7d1de60b170fc8027bb7898e2efca1`, as published by the [BEIR dataset registry](https://github.com/beir-cellar/beir/wiki/Datasets-available). It preserves original IDs and qrels, writes a local Prism mapping, and records counts/checksum in ignored `datasets/scifact/provenance.json`. The prepared set here contains 5,183 documents and 300 judged test queries.

Source papers and dataset rights remain with their owners; do not redistribute the corpus from this repository. Verify upstream terms before rehosting. The original `examples/marketplace` fixture is part of Prism's Apache-2.0 code repository.

