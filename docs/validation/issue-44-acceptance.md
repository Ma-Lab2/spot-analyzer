# Issue 44 acceptance: secure PDF/PNG export

The report export seam consumes an immutable `ReportPackage`; it does not recalculate
analysis metrics. `ReportSpecification` selects PNG or PDF, report name, output
directory, and an optional UTC timestamp suffix.

Acceptance evidence:

- `tests/test_issue44_acceptance.py` verifies stable report-name sanitization,
  UTC timestamp naming, collision suffixes, and output-directory separation.
- Existing files are reserved with exclusive creation and are never overwritten.
  Rendered output is written beside the destination as a temporary file and
  installed with atomic replacement; temporary files are removed on failure.
- PNG and PDF exports preserve one `record_id` and package identity.
- Export failures return `flow_status=export_failed` with a stable diagnostic code
  and do not change the measurement record's flow status.
- Input files are only read while constructing the analysis record; export writes
  only to the requested output directory.

Validation command:

```text
python -m pytest tests/test_issue44_acceptance.py tests/test_issue43_acceptance.py -q
```

The WPF build remains an environment check because the current environment does
not include the .NET SDK.
