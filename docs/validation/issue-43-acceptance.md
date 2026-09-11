# Issue 43 acceptance — ReportPackage preview semantics

The report preview contract is backed by one immutable `AnalysisRecord` and one
`ReportPackage`. `prepare_report` carries record identity, analysis fingerprint,
input identity, calibration, preprocessing, analysis region, model/profile,
metrics, units/status/reason codes, diagnostics, provenance, and visual/curve
assets. Internal fit-attempt fields are removed from report diagnostics, and
invalid or unavailable metric values are represented as null reportable values.

The package is the semantic source for both PNG and PDF rendering. Choosing the
output format is a rendering decision and does not recalculate metrics or alter
the record fingerprint. Preview display settings therefore remain separate from
measurement data.

Evidence: `tests/test_issue43_acceptance.py` verifies identity and provenance,
read-only visual assets, filtering of internal fit fields, and successful PNG
and PDF exports from the same package (`2 passed`).
