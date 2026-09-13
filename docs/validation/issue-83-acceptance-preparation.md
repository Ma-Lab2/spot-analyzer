# Issue #83 acceptance preparation

This is repeatable preparation for the Prototype A, performance, and portable-package
acceptance described by Issue #83. It is not an acceptance result, clean-machine test, or
human sign-off. The package validator checks only archive identity and contents; it does
not launch the WPF client or substitute for interactive observation.

## Package identity and evidence capture

Build the package using the supported environment in [`packaging/BUILD.md`](../../packaging/BUILD.md),
then preserve the ZIP and a machine-readable validator report in a new evidence directory.
Do not commit the ZIP, reports containing user data, screenshots, or diagnostic packages.

```powershell
$evidence = "artifacts/validation/issue-83/"
New-Item -ItemType Directory -Force $evidence | Out-Null
$package = "artifacts/spot-analysis-0.1.0-alpha.1-win-x64.zip"
python packaging/validate_alpha_package.py $package `
  --expected-version 0.1.0-alpha.1 `
  --output "$evidence/package-validation.json" `
  *> "$evidence/package-validation.txt"
```

A successful report records the ZIP SHA-256, extracted `manifest.json` SHA-256, package
version, manifest file count, and the checks performed. It verifies manifest byte counts
and SHA-256 values against every ZIP member, required client/worker/example presence, and
source/test/cache/Git/prototype exclusions. A nonzero exit is a package failure; preserve
its stderr rather than rerunning until it disappears.

The report's `human_acceptance` field intentionally remains `NOT RUN`. The validator must
not be used to mark clean-machine or interactive rows PASS.

## Preparation matrix

The following statuses describe what can be prepared or checked repeatably in the
repository. `NOT RUN` means a human or eligible target-machine observation is still
required; it is not a failed automated test.

| Area | Preparation/evidence | Status before actual run |
| --- | --- | --- |
| Package version, ZIP hash, manifest hash | `package-validation.json` from the validator | NOT RUN until a package is supplied; then automated PASS/FAIL only |
| Manifest file coverage and exclusions | Validator report | NOT RUN until a package is supplied; then automated PASS/FAIL only |
| Issue #79 multi-image regression coverage | Focused Python tests and `WorkspaceModelHarness` | NOT RUN as product acceptance |
| Three real PNGs and edge-case fixtures | Human tester supplies identities/hashes and records each scenario | NOT RUN |
| Automatic preview, ROI, stale/recompute, formal result | Screenshots/video plus record ID and analysis fingerprint | NOT RUN |
| Metrics, warnings, failure/cancellation states | Screenshots/reports/logs with validity and reason codes | NOT RUN |
| Independent and batch export, diagnostics privacy | Output paths and archive inspection | NOT RUN |
| 1920×1080, 1366×768, 100–200% DPI | Human screenshots and reachability notes | NOT RUN |
| Prototype A side-by-side visual review and approved wording | Human review record | NOT RUN |
| 1/2-second responsiveness targets | Human timing capture, including progress/non-blocking behavior | NOT RUN |
| Chinese, spaces, long paths | Human workflow evidence from those paths | NOT RUN |
| Fresh extraction without Python/.NET SDK/admin/network | Eligible clean-machine observation | NOT RUN |

## Human observation placeholders

The acceptance owner must fill these fields from actual observation; source inspection,
unit tests, process startup, and package validation are insufficient evidence:

- Tester and UTC date: `<name> / YYYY-MM-DD`
- Eligible target: `<Windows edition/build, x64, standard-user status>`
- Evidence root: `<path to screenshots, recordings, reports, logs>`
- Input set and hashes: `<three principal PNGs plus edge-case inputs>`
- Performance observations: `<dimensions, timings, progress behavior, UI responsiveness>`
- Visual/accessibility and wording observations: `<screenshots and notes>`
- Record IDs/fingerprints and quality states: `<per-image values>`
- Failures, limitations, and follow-up issue numbers: `<list>`
- Human acceptance decision/signature: `<human-only confirmation>`

Until those fields and evidence are supplied, retain the clean-machine and human rows in
`packaging/ALPHA-TRIAL-ACCEPTANCE.md` as `NOT RUN`. Do not close Issue #83 or its parent
based on this preparation.
