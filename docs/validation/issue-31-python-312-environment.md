# Issue #31 Python 3.12 formal validation environment

Date: 2026-09-11

This record establishes the isolated conda environment used to unblock later formal identity and performance evidence. It does not change any `spot_analyzer.validation` environment gate, overwrite an Issue #10 formal result, promote either provisional profile, or record user acceptance.

## Reproduction

Run these commands from the repository root with the existing conda installation. The environment is separate from `base`; no system Python or base packages are modified.

```powershell
conda create --name spot-analysis-py312 python=3.12 pip --yes
conda run -n spot-analysis-py312 python -m pip install . pytest==9.1.1
conda run -n spot-analysis-py312 python -m pip check
```

The project installation reads the runtime dependency pins from `pyproject.toml`. For this run, `git hash-object pyproject.toml` returned `7bade54a9c00f5ceed0309f63dd69b48462103b8`.

Verify the interpreter, environment path, and required versions with:

```powershell
conda run -n spot-analysis-py312 python -c "import sys, importlib.metadata as metadata, numpy, scipy, PIL; print(sys.version); print(sys.executable); print(numpy.__version__, scipy.__version__, PIL.__version__, metadata.version('rfc8785')); print(sys.version_info[:2] == (3, 12))"
conda list --name spot-analysis-py312
```

`importlib.metadata` is used for RFC 8785 because that package does not expose a supported `__version__` attribute.

## Observed environment manifest

- Conda environment: `spot-analysis-py312`
- Environment prefix and executable directory: `D:\anaconda3\envs\spot-analysis-py312`
- Python executable: `D:\anaconda3\envs\spot-analysis-py312\python.exe`
- Python: `3.12.14`
- NumPy: `2.2.6`
- SciPy: `1.15.3`
- Pillow: `12.2.0`
- RFC 8785: `0.1.4`
- pytest: `9.1.1`
- spot-analyzer: `0.1.0`
- `python -m pip check`: `No broken requirements found.`

The executable and prefix are inside the named environment, not `D:\anaconda3` base and not a system Python installation.

## Validation results

```text
conda run -n spot-analysis-py312 python -m pytest
138 passed in 78.78s

conda run -n spot-analysis-py312 python -m compileall -q spot_analyzer tests
passed (no output)
```

The first full Python 3.12 run exposed one environment-dependent test assertion that assumed the formal environment was always unavailable. The test now derives `formal_environment` from the reported Python and dependency checks while continuing to require a non-formal workload to remain `incomplete`. The corrected full suite passed as shown above.

This ticket stops at environment establishment and full-suite validation. Formal Issue #10 identity/performance evidence and explicit user acceptance remain follow-up work and are not asserted here.
