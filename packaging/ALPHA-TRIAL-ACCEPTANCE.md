# Alpha clean-machine trial acceptance record

## Trial identity

- Client/package: `0.1.0-alpha.1`
- Target: Windows 11 x64
- Package artifact: `spot-analysis-0.1.0-alpha.1-win-x64.zip`
- Package SHA-256: `<record from the delivered ZIP>`
- Manifest: `<record the included manifest.json identity and hash>`
- Tester: `<name or identifier>`
- Trial date (UTC): `<YYYY-MM-DD>`
- Machine identifier: `<non-sensitive identifier>`
- Workflow contract: `workflow-contract-v1`
- Verification contract: `workflow-verification-contract-v1`

This document is the acceptance record for the bounded Alpha trial. The clean-machine
run is **NOT RUN** in the repository development environment used to prepare this
package. Do not change a row to PASS without recording the target-machine evidence.
A successful trial would demonstrate portable workflow behavior, not formal physical-accuracy validation or production readiness.

## Current execution status

The clean-machine trial is **NOT RUN**. The repository development environment is not
eligible evidence: it has no .NET 8 SDK, its default Python is 3.10 rather than the
required 3.12 build environment, and no separate Windows 11 x64 standard-user target
has been made available. The package therefore has no delivered ZIP or manifest hash
to record yet. Follow-up: provision the declared build environment, produce and
preserve the ZIP, then complete every matrix row on an eligible clean target before
closing the acceptance issue. Any failed or untested row remains a limitation and
must receive a linked follow-up ticket rather than being changed to PASS.

## Target-machine preconditions

Record the observed state before extracting the package:

- [ ] Windows 11 x64 confirmed: `<edition/build>`
- [ ] Python is not installed or is not available on `PATH`
- [ ] .NET SDK is not installed or is not available on `PATH`
- [ ] No compiler/development environment is required for startup
- [ ] Tester has standard-user rights and no administrator elevation is used
- [ ] Extraction directory is writable
- [ ] Independent report/diagnostic output directory is writable
- [ ] Input images used for the trial are copied/read-only experimental assets

The build environment is intentionally different: creating the package requires the
.NET 8 SDK, Python 3.12, and PyInstaller. Those developer prerequisites must not be
used as evidence that the extracted client is portable.

## Package evidence

Before starting the client, preserve the ZIP and record:

```powershell
Get-FileHash .\spot-analysis-0.1.0-alpha.1-win-x64.zip -Algorithm SHA256
Expand-Archive .\spot-analysis-0.1.0-alpha.1-win-x64.zip -DestinationPath .\alpha-trial
Get-FileHash .\alpha-trial\manifest.json -Algorithm SHA256
Get-Content .\alpha-trial\manifest.json
```

Verify that `manifest.json` identifies the client version, `win-x64` target,
self-contained client, PyInstaller onedir worker, algorithm/profile identities,
provisional profile status, and hashes for every staged file. Verify that the
extracted package does not contain source code, tests, cache directories, Git
metadata, or the HTML prototype.

## Acceptance matrix

Each row starts as **NOT RUN**. Record the observed result, evidence path, and notes.

| Scenario | Expected evidence | Result | Evidence/notes |
| --- | --- | --- | --- |
| Start client | `SpotAnalysis.App.exe` starts without Python, .NET SDK, compiler, network service, or elevation | NOT RUN | Not run: no eligible clean target or delivered package is available; follow-up required. |
| Identify build | About/diagnostics shows client, worker, analysis-core, profiles, and output identities | NOT RUN | Not run: no eligible clean target or delivered package is available; follow-up required. |
| Bundled example | `examples/alpha-example.png` opens and can be analyzed | NOT RUN | Not run: no eligible clean target or delivered package is available; follow-up required. |
| Supported input | At least one real 8-bit/16-bit grayscale or strict equal-channel 8-bit RGB PNG opens with dimensions, depth, channels, and SHA-256 | NOT RUN | Not run: no eligible clean target or delivered package is available; follow-up required. |
| Invalid input | Unsupported or malformed input is rejected with a readable structured error | NOT RUN | Not run: no eligible clean target or delivered package is available; follow-up required. |
| Automatic preview | Input loading produces an automatic preview and candidate ROI without stepwise confirmation | NOT RUN | Not run: no eligible clean target or delivered package is available; follow-up required. |
| Configuration | Pixel-domain analysis remains available without calibration; supplied x/y calibration and in-bounds ROI are explained and accepted; invalid values receive actionable feedback | NOT RUN | Not run: no eligible clean target or delivered package is available; follow-up required. |
| ROI interaction | ROI drag, resize, reposition, numeric edit, and keyboard movement remain in image bounds | NOT RUN | Not run: no eligible clean target or delivered package is available; follow-up required. |
| Final confirmation | One final action confirms ROI and relative-intensity semantics and creates a formal result | NOT RUN | Not run: no eligible clean target or delivered package is available; follow-up required. |
| Real analysis | Processing, preview, formal, and completed states are distinct; returned record ID and analysis fingerprint are shown | NOT RUN | Not run: no eligible clean target or delivered package is available; follow-up required. |
| Failure states | Failure, cancellation, and timeout remain distinguishable and actionable | NOT RUN | Not run: no eligible clean target or delivered package is available; follow-up required. |
| Measurement validity | Metrics show value or N/A, unit, validity state, and quality reason codes | NOT RUN | Not run: no eligible clean target or delivered package is available; follow-up required. |
| Result export | A user-selected independent output contains one record identity and does not overwrite an existing report | NOT RUN | Not run: no eligible clean target or delivered package is available; follow-up required. |
| Input immutability | Input file hash and timestamp are unchanged after analysis and export | NOT RUN | Not run: no eligible clean target or delivered package is available; follow-up required. |
| Second image | A second supported image can be processed without restarting; each work item keeps its own state and the old result becomes stale after input/configuration changes | NOT RUN | Not run: no eligible clean target or delivered package is available; follow-up required. |
| Batch export | Each image with a current formal result exports independently; missing formal results are skipped and summarized | NOT RUN | Not run: no eligible clean target or delivered package is available; follow-up required. |
| Diagnostics | JSON diagnostic package contains configuration, input identity, statuses, validity, reason codes, record/fingerprint, and failure details when present | NOT RUN | Not run: no eligible clean target or delivered package is available; follow-up required. |
| Image privacy | Original image is absent from default diagnostics and present only after explicit attachment | NOT RUN | Not run: no eligible clean target or delivered package is available; follow-up required. |
| Local logs | `%LOCALAPPDATA%\SpotAnalysis\logs\app.log` is created without writing to the extracted application directory | NOT RUN | Not run: no eligible clean target or delivered package is available; follow-up required. |
| Package exclusions | No source/tests/cache/Git metadata/HTML prototype is present in the delivered ZIP | NOT RUN | Not run: no eligible clean target or delivered package is available; follow-up required. |

## Manual first-analysis procedure

1. Extract the ZIP to a writable directory without elevation.
2. Start `SpotAnalysis.App.exe` and record the version/build identity.
3. Open `examples/alpha-example.png`; preserve its displayed input identity.
4. Open representative supported 8-bit/16-bit grayscale and, if available, strict
   equal-channel 8-bit RGB PNGs copied from the trial dataset. Do not modify originals.
5. Observe automatic input preparation, background/primary-spot candidate, ROI and
   preview without stepwise confirmation. Exercise ROI movement and resizing, and
   record any warning or fallback reason.
6. If calibration is supplied, record x/y values, units, source and confirmation. If it
   is absent, verify pixel-domain results remain available and physical values are marked
   uncalibrated or unavailable. Attempt invalid calibration and ROI values and record the
   actionable feedback.
7. Perform the single final action that confirms the ROI and relative-intensity semantics.
   Record processing, formal/completed states, record ID, analysis fingerprint, metrics,
   validity states, units and reason codes.
8. Select a second image without restarting. Verify each work item keeps its own ROI,
   preview/formal state, warnings and export status; verify one failure does not stop the
   other.
9. Export only a current formal result to an independent output directory. Verify preview
   and needs-recompute results are blocked, then repeat with an existing name and record
   the collision-safe per-image output paths.
10. Export diagnostics with the image attachment checkbox clear. Inspect the JSON and
    verify that the original image is not included.
11. If policy permits, repeat diagnostics with **Attach original image explicitly**
    selected and record that the ZIP contains the image only by explicit action.
12. Collect the local log path, package manifest, exported artifacts, and this record.

## Feedback template

Copy this block into every Alpha feedback item:

```text
Client version/build:
Scenario ID and input type (bundled example, 8-bit PNG, 16-bit PNG, invalid input):
Target environment (Windows edition/build, x64, standard user):
Package SHA-256:
Steps performed:
Expected behavior:
Observed behavior:
Flow status and measurement validity:
Record ID / analysis fingerprint (if available):
Severity (blocker, high, medium, low):
Diagnostic package path (image attached: yes/no):
Relevant log path:
Known limitation or suspected regression:
```

## Evidence boundaries and limitations

- This record does not itself prove that the clean-machine trial passed. Rows remain
  **NOT RUN** until evidence is collected on the target machine.
- Provisional profiles remain provisional. Alpha use is not formal algorithm
  validation, physical ground-truth validation, or a production-release approval.
- The repository environment may lack the .NET SDK, PyInstaller, a clean Windows
  machine, or administrator-rights coverage; those missing checks must be reported,
  not silently converted to PASS.
