# Issue #61 staged Alpha validation workflow

This document defines the repeatable boundary between development checks, local packaged
smoke, and clean-machine Alpha acceptance. It is a procedure and evidence contract, not
an acceptance result. No stage may mark a clean-machine row `PASS` without observed
results from the eligible target machine.

## Stages and evidence boundaries

| Stage | Command/seam | What it proves | What it does not prove | Evidence root |
| --- | --- | --- | --- | --- |
| Development-machine | `packaging/invoke-alpha-validation.ps1 -Stage Development` plus interactive WPF run | Python tests, development build, and developer-machine UI observations | Portable ZIP behavior or clean-machine prerequisites | `artifacts/validation/issue-61-development/` |
| Local packaged-client smoke | `packaging/invoke-alpha-validation.ps1 -Stage PackagedSmoke -PackagePath <zip>` plus manual launch from extraction | ZIP/manifest identity, exclusions, and packaged client/worker observations outside the source tree | Operation without developer prerequisites or clean-machine acceptance | `artifacts/validation/issue-61-packagedsmoke/` |
| Clean-machine Alpha acceptance | `packaging/invoke-alpha-validation.ps1 -Stage CleanMachine`, then `packaging/ALPHA-TRIAL-ACCEPTANCE.md` on target | Observed operation of the preserved ZIP on Windows 11 x64 as a standard user | Formal physical-accuracy validation, production approval, or provisional-profile promotion | `artifacts/validation/issue-61-cleanmachine/` |

The script creates command output and a machine-readable `status.json`. It never runs
interactive WPF acceptance and never changes the acceptance record. Evidence from one
stage must not be copied into another stage's evidence root.

## Development-machine stage

From the repository root, in the declared development environment:

```powershell
.\packaging\invoke-alpha-validation.ps1 -Stage Development
```

The command records Python version and dependencies, the full pytest suite, `.NET`
environment information, and the WPF Release build. Missing tools or failed commands
remain visible in the corresponding output and status. They are not silently converted
to a pass. After the automated checks, launch the WPF client and record the interactive
matrix in `docs/validation/issue-62-development.md`; source inspection and protocol tests
do not substitute for observing the UI.

## Local packaged-client smoke stage

Build once using the supported environment in [`packaging/BUILD.md`](../../packaging/BUILD.md),
preserve the ZIP, and run:

```powershell
$zip = "artifacts/spot-analysis-0.1.0-alpha.1-win-x64.zip"
.\packaging\invoke-alpha-validation.ps1 -Stage PackagedSmoke -PackagePath $zip
```

The command runs `validate_alpha_package.py`, records ZIP and manifest hashes, extracts the
ZIP into the stage evidence directory, and records the complete extracted file list.
Then launch `SpotAnalysis.App.exe` **only from that fresh extraction**, without a source
path or `SPOT_ANALYSIS_WORKER` override. Complete the matrix in
`docs/validation/issue-63-packaged-smoke.md`. Record startup, bundled-example analysis,
supported and invalid input, preview/ROI/configuration, exports, diagnostics/privacy,
logging, immutability, and failure-path observations. Automated package validation is
supporting evidence; it is not a substitute for observing the client.

The build preflight in `packaging/build-alpha.ps1` must complete before any destructive
output cleanup. If it fails, preserve the error and fix the prerequisite rather than
repeating an unverified build. The package validator's `human_acceptance` field remains
`NOT RUN`.

## Clean-machine Alpha acceptance

Run the preparation command if an evidence directory is useful:

```powershell
.\packaging\invoke-alpha-validation.ps1 -Stage CleanMachine
```

This intentionally writes `NOT-RUN.txt` and does not launch the client or mark any row.
For the actual trial, transfer the exact preserved ZIP from the packaged stage to an
eligible Windows 11 x64 standard-user machine. Confirm no Python, .NET SDK, compiler,
network service, or elevation is needed. Record the ZIP and manifest SHA-256 before
launching, then follow `packaging/ALPHA-TRIAL-ACCEPTANCE.md` and fill every row with an
observed result and evidence path. A missing target, package, or human observation is
`NOT RUN`, not `PASS`.

## Retention and limitations

Keep command output, status JSON, hashes, screenshots, reports, diagnostic archives, and
logs below the stage-specific evidence root. Do not commit user data or generated ZIPs.
The Alpha profiles remain provisional. This workflow does not establish formal
physical-accuracy validation, production readiness, or a clean-machine result from local
success. Failed or untested rows remain limitations and should receive linked follow-up
issues.
