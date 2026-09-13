# Issue #83 performance and DPI evidence matrix

This is a repeatable recording sheet, not an acceptance result. Every row starts as
`NOT RUN`; only a human tester observing the freshly extracted WPF client may record a
result. Automated tests, source inspection, and package validation cannot fill these rows.

## Run identity

- Package SHA-256: `<from zip-sha256.txt>`
- Manifest SHA-256: `<from manifest-sha256.txt>`
- Client/worker build identity: `<from package-validation.json>`
- Evidence root: `<path>`
- Tester/date UTC: `<name / YYYY-MM-DD>`

## Performance matrix

Use the same declared build and fixture identities for all rows. Record cold start
separately from warm interaction, wall-clock method, and whether progress remained visible.

| Scenario | Target | Observation | Result | Evidence |
| --- | --- | --- | --- | --- |
| Fresh extraction launch to usable workspace | Current declared hardware, bundled example | `<seconds; UI responsive?>` | NOT RUN | `<screenshot/log>` |
| `1600x1200` current image to usable preview | 1 second | `<seconds; UI responsive?>` | NOT RUN | `<timing capture>` |
| ROI change to updated preview | 2 seconds | `<seconds; progress/non-blocking?>` | NOT RUN | `<timing capture>` |
| Formal analysis completion | Record workload and p95; no unmeasured pass implied | `<seconds/status>` | NOT RUN | `<report/log>` |
| Cancellation or timeout path | Progress and actionable state remain visible | `<observation>` | NOT RUN | `<screenshot/log>` |

A target miss is recorded as `FAIL` with the measured value and limitation; it must not be
hidden by rerunning. Startup time includes process startup and first usable UI, while ROI
preview timing begins after the ROI edit is committed.

## Display and DPI matrix

For each row, capture the complete main workspace and a close-up of any warning/result
area. Record Windows display scaling, effective resolution, and whether every primary
workflow action is reachable without overlap, clipping, or hidden Chinese text.

| Display resolution | Windows scale | Reachability | Clipping/overlap | Chinese wording | Result | Evidence |
| --- | --- | --- | --- | --- | --- | --- |
| 1920x1080 | 100% | `<notes>` | `<notes>` | `<notes>` | NOT RUN | `<screenshots>` |
| 1920x1080 | 150% | `<notes>` | `<notes>` | `<notes>` | NOT RUN | `<screenshots>` |
| 1920x1080 | 200% | `<notes>` | `<notes>` | `<notes>` | NOT RUN | `<screenshots>` |
| 1366x768 | 100% | `<notes>` | `<notes>` | `<notes>` | NOT RUN | `<screenshots>` |
| 1366x768 | 150% | `<notes>` | `<notes>` | `<notes>` | NOT RUN | `<screenshots>` |
| 1366x768 | 200% | `<notes>` | `<notes>` | `<notes>` | NOT RUN | `<screenshots>` |

## Evidence boundary

This matrix does not claim human acceptance, clean-machine portability, prototype visual
approval, or profile validation. Preserve failed and untested rows and link follow-up
issues where needed. Use `packaging/invoke-alpha-validation.ps1 -Stage PackagedSmoke` for
repeatable ZIP validation and fresh extraction before any interactive observations.
