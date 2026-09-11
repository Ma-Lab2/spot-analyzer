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

## Alpha diagnostics

The WPF client writes newline-delimited JSON logs under
`%LOCALAPPDATA%\\SpotAnalysis\\logs\\app.log`, so the extracted application
folder does not need to be writable. The Diagnostics and about section shows the
client, worker, analysis-core, profile, and output identities. **Export
diagnostics** writes a JSON package containing the input summary and SHA-256,
configuration, workflow status, metric validity, reason codes, record identity,
fingerprint, diagnostics, and failure details. The original image is excluded
unless **Attach original image explicitly** is selected; that explicit option
creates a ZIP containing the JSON package and image.

The package records that `standard-profile-v1` and `quality-profile-v1` remain
provisional and does not establish physical-accuracy validation.

## Portable Alpha package

`packaging/build-alpha.ps1` creates the reproducible Windows 11 x64 one-folder
portable ZIP. It publishes the WPF client self-contained, freezes the worker
with PyInstaller, adds the versioned profiles, bundled example, quick-start,
notices, and SHA-256 manifest, and rejects source/test/cache files in the final
stage. See `packaging/BUILD.md` for the build environment and commands.

## Clean-machine Alpha trial

`packaging/ALPHA-TRIAL-ACCEPTANCE.md` is the acceptance record and manual
procedure for the Windows 11 x64 clean-machine trial. It records the package
hash and manifest, preconditions, first-analysis steps, expected outcomes,
privacy checks, evidence paths, and the feedback template. Its rows intentionally
start as **NOT RUN**: a developer-machine test must not be presented as evidence
that the portable client works without Python, the .NET SDK, a compiler, or
administrator rights. The trial is bounded to this Alpha workflow and does not
promote provisional profiles or claim formal production validation.

## Checks

```text
python -m pytest tests/test_worker_protocol.py
 dotnet build src/SpotAnalysis.App/SpotAnalysis.App.csproj
```
