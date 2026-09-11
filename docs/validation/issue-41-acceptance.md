# Issue 41 acceptance: quality diagnostics and metric gating

The analysis core keeps `FlowStatus` separate from `MeasurementStatus`. A computed
analysis can therefore produce metrics marked `valid`, `caution`, `invalid`, or
`unavailable` without presenting calculation success as measurement validity.

The quality gate records stable, sorted reason codes in the analysis record and
carries them to each affected metric. The standard diagnostics include:

- background support and residual/noise availability;
- signal-to-noise thresholds;
- core support and bad-pixel/saturation counts;
- ROI/window boundary truncation;
- multiple peaks and ring-like candidates; and
- Gaussian fit convergence, boundary hits, residuals, and applicability.

Invalid and unavailable metrics expose `None` through `reported_value` and the
reportable metric representation. Input/parameter failures return no analysis
record, while a computed record can still have an invalid quality summary.

Representative acceptance coverage is in `tests/test_issue41_acceptance.py`,
with additional seeded low-SNR, saturation, multimodal, cropped-window, worker
parity, and adapter coverage in the existing test suite.

Validation command:

```text
python -m pytest tests/test_issue41_acceptance.py -q
```
