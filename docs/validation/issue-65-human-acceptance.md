# Issue #65 human focal-spot acceptance

This checklist is a preparation aid for the human-owned interactive acceptance. It does not claim that the client was observed or accepted. The tester must fill every row with observed behavior and evidence, then sign off the record.

## Trial identity

- Issue: #65
- Stage: development-machine interactive acceptance (repeat relevant rows for packaged-client smoke after #63)
- Workflow contract: `workflow-contract-v1`; verification contract: `workflow-verification-contract-v1`
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
| Supported input | Representative 8-bit/16-bit grayscale or strict equal-channel 8-bit RGB PNG opens and analyzes | NOT RUN | `<input hash, screenshot/report>` |
| Automatic preview | Opening an input produces an automatic preview and candidate ROI without stepwise confirmation | NOT RUN | `<screenshot/video>` |
| Calibration | Pixel-domain results remain available when calibration is missing; valid x/y calibration, units, source, and confirmed state are accepted when supplied | NOT RUN | `<screenshot>` |
| Invalid calibration | Missing/invalid values are explained or rejected with actionable feedback; no placeholder physical value is shown | NOT RUN | `<screenshot>` |
| Analysis region | Candidate ROI is in bounds; drag, resize, reposition, numeric edit, and keyboard movement remain in bounds | NOT RUN | `<screenshot/video>` |
| Final confirmation | One final action confirms ROI and relative-intensity semantics and creates a formal result | NOT RUN | `<screenshot/video/report>` |
| Real analysis | Processing, preview, formal, and completed states are distinct | NOT RUN | `<screenshot/video/report>` |
| Result identity | Record ID and analysis fingerprint are displayed | NOT RUN | `<screenshot/report>` |
| Measurements | Each metric shows value or N/A, unit, validity, and quality reason codes; formal does not imply valid | NOT RUN | `<screenshot/report>` |
| Invalid/failure path | At least one invalid-input or failure path is readable and actionable | NOT RUN | `<screenshot/log>` |
| Preview export gate | Preview and needs-recompute results cannot be exported as the current measurement report | NOT RUN | `<screenshot>` |
| Multi-image isolation | Two or more images retain independent ROI, preview/formal state, warnings, and export status; one failure does not stop another | NOT RUN | `<screenshot/video/report>` |
| Result export | Independent destination is used; repeated export gets a collision-safe name; each formal image gets its own report | NOT RUN | `<report paths>` |
| Diagnostics privacy | Default diagnostics contain required fields and exclude original image | NOT RUN | `<diagnostic ZIP/inspection>` |
| Explicit attachment | If tested, image appears only after deliberate attachment | NOT RUN | `<separate ZIP path>` |
| Input immutability | Input hash and timestamp are unchanged after analysis/export | NOT RUN | `<before/after identity record>` |
| Local logging | Log is written under `%LOCALAPPDATA%\SpotAnalysis\logs\app.log` | NOT RUN | `<log path>` |

## Human procedure

1. Open the bundled example and record its displayed identity.
2. Open representative supported grayscale and, if available, strict equal-channel RGB
   PNGs; record each hash and timestamp before analysis.
3. Observe that input loading automatically produces an input view and preview candidate
   without stepwise confirmation. Check the automatic background/ROI explanation and
   record any warning or fallback reason.
4. Exercise ROI drag, resize, reposition, numeric edit, and keyboard movement. Verify the
   rectangle remains in bounds and that the preview updates. Attempt invalid calibration
   and region values and record the actionable feedback.
5. If calibration is supplied, record x/y values, units, source, and confirmation state;
   otherwise record that pixel-domain results remain available and physical values are
   uncalibrated or unavailable.
6. Perform the single final action that confirms the ROI and relative-intensity semantics.
   Observe processing and completed states, then record preview/formal state, record ID,
   analysis fingerprint, every metric's value/N/A, unit, validity, and reason codes.
7. Exercise one actionable invalid-input or failure path and record the displayed result.
8. Select at least two images when available. Switch between them and verify ROI, warnings,
   preview/formal state, and export status do not leak across work items.
9. Export only a current formal result to an independent directory. Verify preview and
   needs-recompute results are blocked, then repeat an export to verify collision-safe
   naming and per-image output.
10. Export diagnostics with image attachment disabled and inspect the archive. If policy
    permits, separately test explicit attachment and record that intentional action.
11. Re-hash the input and compare timestamps. Collect the local log path.
12. If the portable package is available, repeat relevant steps from its fresh extraction
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
