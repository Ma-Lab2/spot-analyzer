# Building the portable Alpha package

The supported build environment is Windows 11 x64 with the .NET 8 SDK, Python
3.12, and the locked dependencies from `pyproject.toml`. PyInstaller is also
required for the frozen worker:

```powershell
python -m pip install -e .
python -m pip install pyinstaller
```

From the repository root, run:

```powershell
.\packaging\build-alpha.ps1
```

The script restores and publishes the WPF application as a self-contained
`win-x64` one-folder output, freezes `src/SpotAnalysis.Worker/worker.py` with
`packaging/worker.spec`, stages the client and worker side by side, adds the
profiles, example, quick-start, and notices, generates a SHA-256 manifest, and
writes `artifacts/spot-analysis-0.1.0-alpha.1-win-x64.zip`.

Use `-OutputDirectory` to choose another artifact directory and `-Version` to
build a separately identified trial. `-SkipRestore` is available when the
runtime restore was already completed in the same environment. The package
stage is assembled from publish outputs only; source, tests, Git metadata,
caches, and the HTML prototype are explicitly excluded and checked before the
ZIP is created.

A clean-machine smoke run remains a separate acceptance step. The build itself
must run with developer tooling, while the resulting ZIP is intended to start
without Python, the .NET SDK, a compiler, or administrator rights.
