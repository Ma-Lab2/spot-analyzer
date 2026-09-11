# Issue 42 acceptance evidence

The display projection is a read-only view of one `AnalysisRecord`.
`spot_analyzer.display.project(record)` returns the record identity,
measurement layers, explanatory curves, center, ROI, and axis units. Every
layer and curve carries the same `record_id` and missing data is represented
by a structured `DisplayUnavailable` value with a stable reason code.

The projection copies arrays before presentation. Colormap, stretch,
interpolation, zoom, and overlays are therefore presentation concerns and do
not mutate corrected intensity, masks, metrics, configuration, or the
analysis fingerprint. Display code does not call the analysis core or create
a second measurement source.

The WPF shell can bind its left configuration, central layer, right result,
and bottom explanation regions to this projection. The profile curves are
read from `diagnostics.report_curves`, including actual/fitted center profiles
and radius/cumulative-energy values with their declared axis unit.

Formal UI build evidence remains pending until the declared Windows/.NET 8
validation environment is available; the current environment has no .NET SDK.
