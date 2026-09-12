# Issue #62 development-machine validation record

## Scope and evidence boundary

This record covers the development-machine stage of the Alpha validation workflow. It
proves Python analysis behavior and the WPF/worker seam only. It is **not** clean-machine
acceptance and must not be used as evidence that the portable package runs without
Python, the .NET SDK, a compiler, network services, or elevation.

- Issue: #62
- Stage: development-machine UI and focal-spot workflow
- Evidence root: `artifacts/validation/issue-62-development/` (create locally; do not commit user data)
- Client/profile status: Alpha `0.1.0-alpha.1`; profiles remain provisional
- Validation date: `<YYYY-MM-DD>`
- Machine/environment: `<record OS, architecture, Python, .NET SDK, and user identity>`

## Repeatable commands

Run from the repository root in the locked development environment:

```powershell
New-Item -ItemType Directory -Force artifacts/validation/issue-62-development | Out-Null
python -m pytest -q *> artifacts/validation/issue-62-development/pytest.txt
python --version *> artifacts/validation/issue-62-development/environment.txt
python -m pip freeze *> artifacts/validation/issue-62-development/pip-freeze.txt
```

When the .NET 8 SDK is installed, run the declared client check and capture its output:

```powershell
dotnet --info *> artifacts/validation/issue-62-development/dotnet-info.txt
dotnet build src/SpotAnalysis.App/SpotAnalysis.App.csproj --configuration Release `
  *> artifacts/validation/issue-62-development/dotnet-build.txt
```

Launch the development client from the built output (or from the IDE) with the worker
override pointing at the development worker when required. Record the observed UI
behavior in the matrix below; automated protocol/core tests do not substitute for
observing the WPF client.

## Automated and build status

Each result needs a command output path. A missing SDK or unavailable UI runtime is an
explicit limitation, not a pass.

| Check | Expected | Result | Evidence / limitation |
| --- | --- | --- | --- |
| Python test suite | All automated tests pass | PASS | `pytest.txt` (run from the development environment) |
| Python version/dependencies | Declared development environment is recorded | NOT RUN | Fill `environment.txt` and `pip-freeze.txt` for the current run |
| WPF Release build | .NET 8 build succeeds | NOT RUN | Requires .NET 8 SDK; record `dotnet-build.txt` or the SDK limitation |
| WPF client launch | Client starts and reaches the input workflow | NOT RUN | Requires a Windows WPF-capable development environment |

## Interactive development-machine matrix

Copy each observed result and evidence path into this record. `NOT RUN` is intentional
until someone observes the client; it must not be changed to PASS from source inspection
or Python tests alone.

| Scenario | Expected observation | Result | Evidence path / notes |
| --- | --- | --- | --- |
| Build identity | About/diagnostics identifies client, worker, analysis core, profiles, and output | NOT RUN | `<screenshot or log>` |
| Bundled example | `examples/alpha-example.png` opens and completes real analysis | NOT RUN | `<screenshot/report>` |
| 8-bit input | Representative supported grayscale PNG opens and analyzes | NOT RUN | `<input identity and report>` |
| 16-bit input | Representative supported 16-bit grayscale PNG opens and analyzes | NOT RUN | `<input identity and report>` |
| Invalid input | Malformed/unsupported input is rejected with readable actionable feedback | NOT RUN | `<screenshot/log>` |
| Calibration | Valid spatial calibration is confirmed before analysis | NOT RUN | `<screenshot>` |
| Invalid calibration | Invalid or missing calibration is rejected before analysis | NOT RUN | `<screenshot>` |
| Analysis region | In-bounds region is accepted; out-of-bounds/invalid region is rejected | NOT RUN | `<screenshot>` |
| Real analysis | Processing and completed states are distinct; record ID and fingerprint are shown | NOT RUN | `<screenshot/report>` |
| Metrics | Every metric has value or N/A, unit, validity, and quality reason codes | NOT RUN | `<screenshot/report>` |
| Failure paths | Failure, cancellation, and timeout remain distinct and actionable | NOT RUN | `<screenshot/log>` |
| Result export | Independent destination is used and repeated export is collision-safe | NOT RUN | `<report paths>` |
| Diagnostics | Required fields are present and original image is excluded by default | NOT RUN | `<diagnostic path/inspection>` |
| Explicit image attachment | Image enters diagnostics only after explicit attachment | NOT RUN | `<ZIP path, if policy permits>` |
| Immutability | Input hash and timestamp are unchanged after analysis/export | NOT RUN | `<before/after identity record>` |
| Local logs | Logs are written under `%LOCALAPPDATA%\\SpotAnalysis\\logs\\app.log` | NOT RUN | `<log path>` |

## Procedure

1. Run the automated suite and record the exact output before opening the client.
2. Build and launch the WPF client when the declared .NET SDK is available; record the
   client/build identity and worker path.
3. Open the bundled example, then representative 8-bit and 16-bit grayscale inputs.
   Preserve each input hash and timestamp before and after the run.
4. Exercise valid and invalid calibration and analysis-region values. Confirm invalid
   configuration is rejected before work starts.
5. Run real analysis and observe processing, completed, and at least one actionable
   failure/invalid-input path. Record IDs, fingerprint, metrics, units, validity, and
   quality reason codes.
6. Export a report to a directory independent from the input. Repeat the export to
   verify collision-safe naming. Export diagnostics with image attachment disabled and
   inspect the archive; if attachment is tested, record it as an explicit separate step.
7. Collect logs under the user-local application-data directory and place screenshots,
   reports, diagnostics, and command output below the evidence root.

## Limitations

- Development-machine results do not establish clean-machine portability.
- Provisional profiles are not physically validated and are not production approved.
- Automated tests cover core/protocol behavior; interactive WPF rows require human
  observation and sign-off.
- If the .NET SDK or a Windows WPF runtime is unavailable, those rows remain NOT RUN
  with the environment error preserved as evidence.
