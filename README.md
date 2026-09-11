# Spot Analysis MVP seam

Issue #37 provides a minimal WPF/.NET 8 shell and Python worker seam. Issue #38
adds read-only 8/16-bit grayscale PNG adaptation and a deterministic standard
analysis record.

## Worker protocol

The worker is `src/SpotAnalysis.Worker/worker.py`. It reads one JSON request per
stdin line and writes only NDJSON protocol messages to stdout. Diagnostics belong
on stderr. The canonical request is the versioned `analysis-request-v1` contract,
which carries an asset identity, complete analysis configuration, and output
strategy:

```json
{"schema":"analysis-request-v1","input":{"asset":{"path":"spot.png","expected_sha256":"..."},"confirm_relative_intensity":true},"configuration":{},"output_strategy":{"work_directory":"run-assets","derived_format":"npy"}}
```

The shipped entry point delegates this schema to `spot_analyzer.worker`, so the
real analysis core produces `AnalysisRecord` metrics, validity states, quality
reason codes, diagnostics, and derived asset identities. It emits an
`analysis-event-v1` `started` event followed by an `analysis-result-v1`
`completed` or `failed` result. The WPF client launches the packaged
`SpotAnalysis.Worker.exe` beside the application (or the path in
`SPOT_ANALYSIS_WORKER` during development); it never requires a system
`python` command. The older `protocol_version: 1` envelope remains available
for compatibility with the initial worker smoke tests; it is not the canonical
real-analysis contract.

The WPF result view renders the worker's returned metric domains, units, validity
states, and quality reason codes without reinterpreting the measurement. A current
result can be exported as a JSON report from a user-selected destination. Export
uses the complete worker result (including record ID, analysis fingerprint,
configuration, diagnostics, and metrics), reserves a collision-free filename, and
never writes to the input image.

## Checks

```text
python -m pytest tests/test_worker_protocol.py
 dotnet build src/SpotAnalysis.App/SpotAnalysis.App.csproj
```
