import logging
import time
from contextlib import contextmanager

from prometheus_client import Counter, Gauge, Histogram

REQUESTS = Counter("prism_requests_total", "Search requests", ["mode", "outcome"])
LATENCY = Histogram(
    "prism_request_seconds",
    "Search latency",
    ["mode"],
    buckets=(0.001, 0.005, 0.01, 0.02, 0.05, 0.1, 0.2, 0.5, 1, 2, 5),
)
STAGES = Histogram(
    "prism_stage_seconds",
    "Search stage latency",
    ["stage"],
    buckets=(0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 5),
)
ACTIVE = Gauge("prism_active_requests", "Requests holding a worker slot")
RETRIES = Counter("prism_replica_retries_total", "Replica retries")
OVERLOAD = Counter("prism_overload_total", "Rejected requests due to full worker slots")
INDEX_BYTES = Gauge("prism_index_bytes", "Bytes in currently loaded index files")
LIVE_DOCS = Gauge("prism_live_documents", "Documents in loaded visible snapshots")
PENDING_REVISIONS = Gauge("prism_pending_revisions", "Accepted but invisible revisions")
logger = logging.getLogger("prism")


@contextmanager
def stage(name, timings):
    start = time.monotonic()
    try:
        yield
    finally:
        elapsed = time.monotonic() - start
        timings[name] = timings.get(name, 0) + elapsed * 1000
        STAGES.labels(name).observe(elapsed)
