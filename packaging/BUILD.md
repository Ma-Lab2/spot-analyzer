# Building the portable Alpha package

The supported build environment is Windows 11 x64 with the .NET 8 SDK, Python
3.12, and the locked dependencies from `pyproject.toml`. PyInstaller is also
required for the frozen worker:

Use the supported 64-bit Python 3.12 environment and install the locked worker
requirements plus the pinned freezer:

```powershell
python --version                         # 3.12.x
python -c "import struct; print(struct.calcsize('P') * 8)"  # 64
python -m pip install -e .
python -m pip install PyInstaller==6.14.2
dotnet --list-sdks                        # includes an 8.x SDK
```

From the repository root, run:

```powershell
.\packaging\build-alpha.ps1
```

The script restores and publishes the WPF application as a self-contained
`win-x64` one-folder output, freezes `src/SpotAnalysis.Worker/worker.py` with
`packaging/worker.spec`, stages the client and worker side by side, adds the
profiles, example, quick-start, acceptance procedure, and notices, generates a
SHA-256 manifest, and writes
`artifacts/spot-analysis-0.1.0-alpha.1-win-x64.zip`.

Use `-OutputDirectory` to choose another artifact directory. `-Version` is only
accepted when it matches the authoritative version in `build-identity.json`; it
does not create an independently identified build. `-SkipRestore` is available
when the runtime restore was already completed in the same environment. The
package stage is assembled from publish outputs only; source, tests, Git metadata,
caches, and the HTML prototype are explicitly excluded and checked before the ZIP
is created.

A clean-machine smoke run remains a separate acceptance step. The build itself
must run with developer tooling, while the resulting ZIP is intended to start
without Python, the .NET SDK, a compiler, or administrator rights. Follow
`packaging/ALPHA-TRIAL-ACCEPTANCE.md` and preserve the ZIP hash, manifest, logs,
exports, and target-machine outcomes. A development-machine build or test must
not be recorded as clean-machine acceptance evidence. Preserve both hashes before
running the trial:

```powershell
Get-FileHash .\artifacts\spot-analysis-0.1.0-alpha.1-win-x64.zip -Algorithm SHA256
Get-FileHash .\artifacts\spot-analysis-0.1.0-alpha.1-win-x64\manifest.json -Algorithm SHA256
```
