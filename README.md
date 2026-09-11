# Spot Analysis MVP seam

Issue #37 provides a minimal WPF/.NET 8 shell and Python worker seam. Issue #38
adds read-only 8/16-bit grayscale PNG adaptation and a deterministic standard
analysis record.

## Worker protocol

The worker is `src/SpotAnalysis.Worker/worker.py`. It reads one JSON request per
stdin line and writes only NDJSON protocol messages to stdout. Diagnostics belong
on stderr. Protocol version `1` requests use a synthetic input:

```json
{"protocol_version":1,"request_id":"smoke-1","command":"analyze","input":{"kind":"synthetic","width":16,"height":16}}
```

A valid request emits `started` with `status: processing`, followed by a
`terminal` message with `status: success`. Invalid input emits a structured
`terminal` failure. The worker intentionally does not implement measurement
metrics, report export, HTTP/RPC, or packaging.

## Checks

```text
python -m pytest tests/test_worker_protocol.py
 dotnet build src/SpotAnalysis.App/SpotAnalysis.App.csproj
```
