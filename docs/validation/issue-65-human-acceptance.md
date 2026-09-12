# Issue #65 human focal-spot acceptance

This checklist is a preparation aid for the human-owned interactive acceptance. It does not claim that the client was observed or accepted. The tester must fill every row with observed behavior and evidence, then sign off the record.

## Trial identity

- Issue: #65
- Stage: development-machine interactive acceptance (repeat relevant rows for packaged-client smoke after #63)
- Tester: `<SphericalChicken or delegate>`
- Date/time (local and UTC): `<record both>`
- Client/build identity: `<version, commit/package identity>`
- Machine: `<Windows edition/build, x64, standard-user status>`
- Evidence root: `artifacts/validation/issue-65-human/` (do not commit user data)
- Boundary: development or packaged-client evidence only; this is not clean-machine acceptance.

## Before opening the client

Record command output and preserve the original input assets. Use copies for testing.

```powershell
New-Item -ItemType Directory -Force artifacts/validation/issue-65-human | Out-Null
python --version *> artifacts/validation/issue-65-human/environment.txt
Get-Date -Format o *> artifacts/validation/issue-65-human/start-time.txt
Get-FileHash .\examples\alpha-example.png -Algorithm SHA256 *> artifacts/validation/issue-65-human/example-before-hash.txt
```

Record the WPF client/build identity and worker path from the client or diagnostics. Do
not substitute Python tests or source inspection for observing the UI.

## Interactive checklist

For each row, record the observed result and an evidence path. Leave `NOT RUN` when the
scenario was not observed; never convert it to PASS from automated tests.

| Scenario | Expected observation | Result | Evidence path / notes |
| --- | --- | --- | --- |
| Build identity | Client, worker, analysis core, profile, and provisional status are identifiable | NOT RUN | `<screenshot/diagnostic>` |
| Bundled example | `examples/alpha-example.png` opens and real analysis can be started | NOT RUN | `<screenshot/report>` |
| Supported input | Representative 8-bit or 16-bit grayscale PNG opens and analyzes | NOT RUN | `<input hash, screenshot/report>` |
| Calibration | Valid x/y calibration, units, source, and confirmed state are accepted | NOT RUN | `<screenshot>` |
| Invalid calibration | Missing/invalid values are rejected before analysis with actionable feedback | NOT RUN | `<screenshot>` |
| Analysis region | In-bounds region is accepted; invalid/out-of-bounds region is rejected | NOT RUN | `<screenshot>` |
| Real analysis | Processing and completed states are distinct | NOT RUN | `<screenshot/video/report>` |
| Result identity | Record ID and analysis fingerprint are displayed | NOT RUN | `<screenshot/report>` |
| Measurements | Each metric shows value or N/A, unit, validity, and quality reason codes | NOT RUN | `<screenshot/report>` |
| Invalid/failure path | At least one invalid-input or failure path is readable and actionable | NOT RUN | `<screenshot/log>` |
| Result export | Independent destination is used; repeated export gets a collision-safe name | NOT RUN | `<report paths>` |
| Diagnostics privacy | Default diagnostics contain required fields and exclude original image | NOT RUN | `<diagnostic ZIP/inspection>` |
| Explicit attachment | If tested, image appears only after deliberate attachment | NOT RUN | `<separate ZIP path>` |
| Input immutability | Input hash and timestamp are unchanged after analysis/export | NOT RUN | `<before/after identity record>` |
| Local logging | Log is written under `%LOCALAPPDATA%\SpotAnalysis\logs\app.log` | NOT RUN | `<log path>` |

## Human procedure

1. Open the bundled example and record its displayed identity.
2. Open a representative supported grayscale PNG (8-bit or 16-bit) and record its hash
   and timestamp before analysis.
3. Enter valid spatial calibration and confirm it. Select an in-bounds region and
   confirm configuration. Attempt invalid calibration and region values and record the
   rejection before analysis starts.
4. Run real analysis. Observe processing and completed states, then record record ID,
   analysis fingerprint, every metric's value/N/A, unit, validity, and reason codes.
5. Exercise one actionable invalid-input or failure path and record the displayed result.
6. Export to an independent directory. Repeat an export to verify collision-safe naming.
7. Export diagnostics with image attachment disabled and inspect the archive. If policy
   permits, separately test explicit attachment and record that intentional action.
8. Re-hash the input and compare timestamps. Collect the local log path.
9. If the portable package is available, repeat relevant steps from its fresh extraction
   and label those artifacts `packaged-client`, not `development-machine`.

## Evidence index

| Artifact | Path | Notes |
| --- | --- | --- |
| Screenshots/video | `<path>` | `<scenario IDs>` |
| Reports | `<path>` | `<collision-safe names>` |
| Diagnostics, image unattached | `<path>` | `<archive listing>` |
| Diagnostics, explicit attachment (if used) | `<path or not tested>` | `<intent recorded>` |
| Logs | `<path>` | `<under %LOCALAPPDATA%>` |
| Input identity before/after | `<path>` | `<hash and timestamp comparison>` |

## Human sign-off

I observed the scenarios above and reviewed the evidence paths. This sign-off applies only
to the stated stage and does not establish clean-machine portability, physical-accuracy
validation, or production readiness.

- Tester: `<name>`
- Signature/confirmation: `<human confirmation>`
- Date: `<YYYY-MM-DD>`
- Limitations/follow-up issues: `<list issue numbers>`
