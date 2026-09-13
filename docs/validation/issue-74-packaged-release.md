# Issue #74 Prototype A packaged release validation

## Scope and evidence boundary

This record covers the highest-level validation observable in the Windows 11 development
session for Issue #74. It records automated regressions, a Release `win-x64` build, a
new portable ZIP, and a fresh extraction exercised with the executable shipped in that
ZIP. It does **not** claim human visual acceptance, clean-machine acceptance, or formal
physical-accuracy validation.

This is historical run evidence for the recorded Issue #74 head, not evidence that
`workflow-contract-v1` or `workflow-verification-contract-v1` was observed. The automatic
preview, final ROI confirmation, equal-channel RGB, multi-image and pixel-domain
acceptance rows introduced by Issue #75/#82 remain a separate procedure/evidence gate.
The package hashes, observed worker result, and NOT RUN boundaries below are preserved
as originally recorded.

- Issue: #74
- Source branch: `worktree-agent-ae7263b165679aef8`
- Source baseline: `f1611cc` (includes Issue #72 `37cf3f8` and Issue #73)
- Package identity: `0.1.0-alpha.1`, Windows `win-x64`
- Build output root: `artifacts/issue-74-20260913-final/`
- Package: `artifacts/issue-74-20260913-final/spot-analysis-0.1.0-alpha.1-win-x64.zip`
- Evidence root: `artifacts/validation/issue-74-20260913-final/`
- ZIP SHA-256: `41fcb09390ff9547d0ba75da787c8b50ef39534125335c5e189c66edecb06205`
- Extracted `manifest.json` SHA-256: `821e4835c98d3ed68a676ef071b9ef9d200f429227723acbd81d43e9322a6072`
- Profiles: `standard-profile-v1`, `quality-profile-v1` (both provisional)

The output root and evidence root are new, separate locations. No existing release output
or prior validation evidence was removed or overwritten.

## Prerequisites and commands

The supported build prerequisites were available after selecting the installed 64-bit
Python 3.12 interpreter and installing the pinned PyInstaller build dependency:

```text
Python 3.12.10, 64-bit
PyInstaller 6.14.2
numpy 2.2.6
scipy 1.15.3
Pillow 12.2.0
rfc8785 0.1.4
.NET SDK 8.0.425
```

Commands and captured output:

```powershell
python -m pytest -q
py -3.12 -m pytest -q *> artifacts/validation/issue-74-20260913-final/pytest-py312.txt
dotnet run --project tests/WorkspaceModelHarness/WorkspaceModelHarness.csproj --configuration Release
dotnet build src/SpotAnalysis.App/SpotAnalysis.App.csproj --configuration Release --runtime win-x64
.\packaging\build-alpha.ps1 -OutputDirectory artifacts/issue-74-20260913-final
```

The first pre-repair Python run exposed a missing `packaging/build-helpers.ps1`; the
helper was restored and its transient file-lock retry test then passed. The final Python
3.12 suite, workspace harness, and WPF Release build all passed with zero errors.

## Automated regression matrix

| Area | Result | Evidence |
| --- | --- | --- |
| Python analysis/core, worker protocol, input/adapters | PASS | `pytest-py312.txt` |
| Display projection and all seven record-bound layers | PASS | `pytest-py312.txt`, `tests/test_issue70_display.py` |
| X/Y actual/fitted profiles and cumulative energy curves | PASS | `pytest-py312.txt`, `tests/test_issue71_curves.py` |
| Report PNG/PDF naming, collision safety, gating | PASS | `pytest-py312.txt`, `tests/WorkspaceModelHarness/Program.cs` |
| Diagnostics default privacy and explicit attachment | PASS | `pytest-py312.txt`, `tests/WorkspaceModelHarness/Program.cs` |
| Prototype A layout/resource/keyboard structural checks | PASS | `pytest-py312.txt`, `tests/test_issue73_presentation.py` |
| Connected WPF presentation state transitions | PASS | `workspace-harness.txt` (`Workspace presentation behavior passed`) |
| Release WPF `win-x64` build | PASS | `wpf-release-build.txt` (0 warnings, 0 errors) |
| Package manifest and exclusion checks | PASS | `manifest-validation.txt` (657 files, zero missing/mismatch/extra/forbidden) |

## Fresh packaged-client evidence

The ZIP was extracted to `fresh-extraction/`, a new directory below the evidence root.
The packaged worker was invoked by absolute path from that extraction, with its working
directory set to the extraction and no source-tree worker override:

- Client executable: `fresh-extraction/SpotAnalysis.App.exe`
- Worker executable: `fresh-extraction/SpotAnalysis.Worker.exe`
- Bundled example: `fresh-extraction/examples/alpha-example.png`
- Worker protocol result: return code 0, `started` then `completed`, empty stderr
- Record ID (`record_id`): `record-2393f358cf8d48b59272aae9ea10a907`
- Analysis fingerprint (`analysis_fingerprint`): `sha256-0c59755fd519d10c686b1475bf90a0b292c1babbadc2d1baa5a1d21aa4d3cae0`
- Flow status: `computed`
- Input SHA-256: `06555b3933a65f1bb9dfbc558f452ec4131f6c10b95236d1300ddebf3d93e930`
- Metrics: 14; derived assets: 6

The packaged `SpotAnalysis.App.exe` was also launched from the fresh extraction. It
remained running after a five-second startup observation and was then stopped by the
probe. This proves process startup from the extracted published executable only; it is
not a visual assertion about the WPF window.

Manifest validation recorded:

- schema `spot-analysis-package-v1`
- self-contained client `True`, target `win-x64`
- worker `PyInstaller onedir`
- profile validation `provisional`
- 657 manifest entries; 0 missing, 0 mismatched, 0 extra, 0 forbidden files
- both `SpotAnalysis.App.exe` and `SpotAnalysis.Worker.exe` present

Relevant evidence files include `zip-sha256.txt`, `manifest-sha256.txt`,
`manifest.json`, `manifest-validation.txt`, `extracted-files.txt`,
`packaged-startup.txt`, `packaged-worker-execution.json`, and
`packaged-worker-record-summary.json`.

## Human-owned and unavailable checks

The following Issue #74 acceptance rows remain **NOT RUN** in this headless session and
must not be inferred from source inspection, automated tests, or process startup:

- human visual confirmation of the four-region WPF workspace, seven display layers,
  grayscale/pseudocolor/range/overlay presentation, and curves visibly referencing the
  current result;
- interactive packaged-client calibration, ROI confirmation, stale/recompute workflow,
  failure/cancellation presentation, PNG/PDF dialogs, diagnostics privacy UI, and input
  read-only observation;
- screenshots or equivalent human observation evidence;
- clean-machine startup/workflow without developer tools, and standard-user/elevation
  coverage.

The local log probe found `%LOCALAPPDATA%\SpotAnalysis\logs\app.log`; no claim is made
about clean-machine logging. Alpha profiles remain provisional, and this record does not
close Issue #66 or sign Issue #65.
