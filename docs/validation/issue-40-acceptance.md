# Issue 40 acceptance record

Issue 40 freezes the standard measurement profile while keeping its validation
state explicitly provisional.

## Evidence

- `tests/test_issue40_acceptance.py` verifies the required D4σ, Gaussian FWHM,
  ellipticity, angle, EE50/EE80, fixed-reference concentration, residual and
  center diagnostics.
- Every metric carries a unit, method version, and reason-code tuple. The
  analysis configuration carries `standard-profile-v1`,
  `quality-profile-v1`, and `profile_validation: provisional`.
- Pixel-domain and physical-domain values are both emitted for dimensional
  metrics. The physical value uses the configured anisotropic x/y spatial
  calibration and records the physical unit.
- A result that is valid before profile gating is reported as `caution` while
  the profile remains provisional; the metric reason includes
  `provisional_profile`.
- Repeating the same seeded input and complete configuration yields the same
  metric values, statuses, diagnostics, and analysis fingerprint. Only the
  record identity differs.

## Validation command

```text
python -m pytest tests/test_issue40_acceptance.py -q
```

Result: 2 passed.

The WPF build was not run in this environment because the .NET SDK is not
installed; rerun `dotnet build` in the declared Windows/.NET 8 environment.
