# Issue 46 acceptance evidence

## Scope

The advanced preprocessing branch is exploratory and opt-in. The standard branch is retained for every analysis and remains the source of the standard measurement semantics. Supported advanced steps are versioned bad-pixel interpolation, Gaussian filtering, and gradient DPC.

## Evidence

- `PreprocessingConfiguration` rejects an enabled branch without an explicit supported option.
- Advanced execution records `advanced-preprocessing-v1`, actual steps, parameters, standard-branch retention, and standard/advanced sensitivity comparisons in `AnalysisRecord.diagnostics`.
- Sensitivity thresholds are shared by the core status gate and reason codes; an advanced branch cannot promote a provisional profile to `valid`.
- Bad-pixel interpolation, filtering, DPC, and invalid configuration paths are covered by `tests/test_issue46_acceptance.py`.

## Validation

```text
python -m pytest tests/test_issue46_acceptance.py -q
3 passed
```

The .NET SDK is not installed in this environment, so the WPF build was not run. WPF validation must be repeated in the declared Windows/.NET 8 environment.
