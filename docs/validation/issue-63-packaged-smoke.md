# Issue #63 local packaged-client smoke record

## Scope and evidence boundary

This record covers the local packaged-client stage of Alpha validation. It uses the
exact ZIP produced by `packaging/build-alpha.ps1`, extracted into a fresh directory on
the development machine. It proves that the published WPF client and frozen worker are
staged together and operate outside the source tree; it is **not** clean-machine
acceptance and does not prove operation without developer prerequisites.

- Issue: #63
- Parent: #61 Define staged Alpha validation before clean-machine acceptance
- Stage: local packaged-client smoke
- Evidence root: `artifacts/validation/issue-63-packaged/` (create locally; do not commit user data)
- Package: `artifacts/spot-analysis-0.1.0-alpha.1-win-x64.zip`
- ZIP SHA-256: `<record before extraction>`
- Manifest SHA-256: `<record from extracted manifest>`
- Validation date: `<YYYY-MM-DD>`
- Machine/environment: `<record OS, architecture, and build-tool versions>`
- Workflow contract under test: `workflow-contract-v1`
- Verification contract: `workflow-verification-contract-v1`

Evidence from issue #62 development-machine validation and issue #64 clean-machine
acceptance must remain in separate directories and must not be copied into this record.
This local smoke record is **not clean-machine acceptance**.

## Build and identity capture

Build from a supported Windows 11 x64 development environment as documented in
`packaging/BUILD.md`, then capture the artifact identities before extraction or launch:

```powershell
$evidence = "artifacts/validation/issue-63-packaged"
New-Item -ItemType Directory -Force $evidence | Out-Null
.\packaging\build-alpha.ps1 *> "$evidence\build.txt"
Get-FileHash .\artifacts\spot-analysis-0.1.0-alpha.1-win-x64.zip -Algorithm SHA256 `
  | Out-File "$evidence\zip-sha256.txt"
Get-FileHash .\artifacts\spot-analysis-0.1.0-alpha.1-win-x64\manifest.json -Algorithm SHA256 `
  | Out-File "$evidence\manifest-sha256.txt"
Copy-Item .\artifacts\spot-analysis-0.1.0-alpha.1-win-x64\manifest.json "$evidence\manifest.json"
```

The build preflight must pass before output cleanup. If it fails, preserve `build.txt`
and the error; do not call the script repeatedly with an unverified prerequisite.

## Fresh extraction and isolation checks

Extract into a new writable directory that is not the repository and launch only from
that extraction. Do not set a development worker override, use source files, or run the
client from an IDE. Record the commands and output under the evidence root:

```powershell
$package = Resolve-Path .\artifacts\spot-analysis-0.1.0-alpha.1-win-x64.zip
$extract = Join-Path $PWD "artifacts/validation/issue-63-packaged/extracted"
if (Test-Path $extract) { Remove-Item $extract -Recurse -Force }
Expand-Archive $package -DestinationPath $extract
Get-ChildItem $extract -Recurse -File | Select-Object FullName,Length |
  Out-File "$evidence\extracted-files.txt"
Get-FileHash (Join-Path $extract "manifest.json") -Algorithm SHA256 |
  Out-File "$evidence\extracted-manifest-sha256.txt"
```

Verify `manifest.json` identifies the client, `win-x64` self-contained target, frozen
PyInstaller onedir worker, analysis core, both provisional profiles, dependencies, and
a SHA-256/byte count for every staged file. Verify the extraction contains no source
or test trees, Python/C#/XAML source, caches, Git metadata, or HTML prototype.

Start `SpotAnalysis.App.exe` from `$extract`. Confirm the client finds the adjacent
`SpotAnalysis.Worker.exe` without Python, a source-tree path, or a development override.
Normal logs must be under `%LOCALAPPDATA%\SpotAnalysis\logs\app.log`, not under the
extracted package directory.

## Packaged-client smoke matrix

Each row must have an observed result and evidence path. Automated contract tests are
supporting evidence only; they do not substitute for observing startup and workflow.

| Scenario | Expected observation | Result | Evidence path / notes |
| --- | --- | --- | --- |
| Package identity | ZIP and manifest hashes match the preserved values; manifest covers every staged file | NOT RUN | `<hashes and manifest>` |
| Package exclusions | No source, tests, caches, Git metadata, or HTML prototype is present | NOT RUN | `<extracted-files.txt>` |
| Client startup | Published executable starts from extraction without source tree or worker override | NOT RUN | `<screenshot/log>` |
| Bundled example | `examples/alpha-example.png` opens and completes real analysis | NOT RUN | `<screenshot/report>` |
| Supported input | Representative 8-bit/16-bit grayscale or strict equal-channel 8-bit RGB PNG opens and analyzes | NOT RUN | `<input identity/report>` |
| Automatic preview | Input opens into an automatic preview and candidate ROI without stepwise confirmation | NOT RUN | `<screenshot/report>` |
| Configuration | Pixel-domain results remain available without calibration; supplied confirmed calibration and in-bounds ROI are accepted; invalid values receive actionable feedback | NOT RUN | `<screenshot/log>` |
| ROI interaction | Drag, resize, reposition, numeric edit and keyboard movement stay in bounds | NOT RUN | `<screenshot/video>` |
| Final confirmation | One final action confirms ROI and relative-intensity semantics and creates a formal result | NOT RUN | `<screenshot/report>` |
| Processing/completed | Processing, preview, formal and completed states are distinct; record ID and analysis fingerprint shown | NOT RUN | `<screenshot/report>` |
| Measurement validity | Every metric shows value or N/A, unit, validity, and quality reason codes | NOT RUN | `<screenshot/report>` |
| Export and collision | Independent report destination is used; repeated export gets a collision-safe name | NOT RUN | `<report paths>` |
| Diagnostics/privacy | Default diagnostics contain required fields and exclude original image; explicit attachment is opt-in | NOT RUN | `<diagnostic inspection>` |
| Immutability | Input hash and timestamp remain unchanged after analysis and exports | NOT RUN | `<before/after identity>` |
| Local logging | App log is created below `%LOCALAPPDATA%\SpotAnalysis\logs` and extraction remains unmodified | NOT RUN | `<log path>` |
| Failure/invalid path | At least one actionable invalid-input or failure path is observable | NOT RUN | `<screenshot/log>` |

## Procedure

1. Build the package once and preserve ZIP and manifest hashes.
2. Extract to a fresh directory and inspect the complete file list and manifest.
3. Launch the published executable from the extraction, with no worker override.
4. Open the bundled example and representative grayscale/equal-channel RGB PNGs; preserve
   input identity and timestamps before and after analysis.
5. Observe automatic preview, background/primary-spot candidate and ROI without stepwise
   confirmation. Exercise ROI operations and record warnings or fallback reasons.
6. Record supplied calibration or verify the uncalibrated pixel-domain path. Reject invalid
   values with actionable feedback, perform the single final ROI/semantic confirmation,
   and observe preview, formal, processing and completed states.
7. Inspect record identity, analysis fingerprint, metrics, units, validity, and reason
   codes. Exercise one actionable invalid-input or failure path.
8. If multiple images are available, switch between work items and verify state isolation.
   Export only current formal results, repeat to verify collision safety, and export
   diagnostics with image attachment disabled by default.
9. Inspect diagnostics for required fields and verify the original image is absent unless
   explicit attachment was selected. Collect the local log path and all evidence paths.

## Limitations

- This stage runs on the development machine and therefore does not establish clean
  Windows portability; issue #64 is the only clean-machine acceptance stage.
- A missing .NET SDK, Python 3.12, PyInstaller, or WPF runtime is an explicit limitation;
  do not mark package smoke rows PASS when the package was not built and launched.
- Alpha profiles remain provisional and this workflow is not formal physical-accuracy
  validation or production approval.
