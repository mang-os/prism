# HTTP API

Serve locally with `prism --config examples/local.yaml serve`. FastAPI exposes the interactive schema at `/docs` and metrics at `/metrics`. The checked-in [OpenAPI JSON](openapi.json) is generated from the implementation.

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/health/live`, `/health/ready`, `/metrics` | Process, snapshot/provider status, telemetry |
| `POST` | `/v1/indexes` | Create a local index: `{"name":"demo","semantic":true}` |
| `GET` | `/v1/indexes/{name}/stats` | Accepted/visible revision, live documents, segment bytes |
| `POST` | `/v1/indexes/{name}/documents:bulk` | Atomic upsert batch: `{"documents":[...]}` |
| `DELETE` | `/v1/indexes/{name}/documents/{id}` | Accept a deletion |
| `POST` | `/v1/indexes/{name}/refresh` | Publish through `through_revision` if supplied |
| `POST` | `/v1/indexes/{name}/compact` | Bounded local merge |
| `POST` | `/v1/indexes/{name}/search` | Local or coordinator search |

PowerShell example:

```powershell
$body = @{q='my air conditioner is not cooling'; mode='hybrid'; filters=@(@{field='kind';op='eq';value='service'})} | ConvertTo-Json -Depth 5
Invoke-RestMethod -Uri 'http://127.0.0.1:8080/v1/indexes/demo/search' -Method Post -ContentType 'application/json' -Body $body
```

Search responses report requested/executed mode, latency, partial/degraded state, epoch, shard coverage/status, candidate depth, expansion limit, hits, and optional explanations. Hits contain original documents. Requests support plain/DSL parsing, lexical/semantic/hybrid retrieval, typed AND filters, top/candidate limits, typo/synonym toggles, deadline, fallback, and explanation. Explicit lexical fallback sets `degraded=true`. A successful replica retry retains full coverage.

Ingestion returns `accepted_revision`; search reads only a published `visible_revision`. Bulk allows at most 1,000 documents and 8 MiB; validation failures reject the whole batch. The same body limit applies before JSON decoding. Empty queries and unknown fields fail validation. DSL syntax/type errors are 400, unavailable providers/shards 503, deadlines 504, overload 429. Errors carry code/message without a stack trace. Exact schemas and limits are in OpenAPI and the spec.

The internal branch route is local cluster machinery, hidden from OpenAPI. Do not expose it to untrusted networks. Authentication and production tenancy are not implemented.

