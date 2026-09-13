using System.Text.Json;
using SpotAnalysis.App;

static void Assert(bool condition, string message)
{
    if (!condition) throw new InvalidOperationException(message);
}

static T RunOnSta<T>(Func<T> action)
{
    T? result = default;
    Exception? failure = null;
    var thread = new Thread(() =>
    {
        try { result = action(); }
        catch (Exception exception) { failure = exception; }
    });
    thread.SetApartmentState(ApartmentState.STA);
    thread.Start();
    thread.Join();
    if (failure is not null) throw failure;
    return result!;
}

var model = new WorkspacePresentationModel();
var input = new WorkspaceInput("sample.png", "abc", 32, 32, 8, "32×32, 8-bit grayscale PNG, SHA-256 abc");
var draft = new AnalysisDraft(0, 0, 16, 16, null, null, null, null, "confirmed", 1, 1, "mm", "fixture");
var request = AnalysisRequest.Create(input.Path, input.Sha256, 0, 0, 16, 16, null, null, null, null,
    draft.CalibrationStatus, draft.CalibrationX, draft.CalibrationY, draft.CalibrationUnits,
    draft.CalibrationSource, "derived");

model.LoadInput(input);
model.EditDraft(draft);
Assert(model.State.WorkflowStatus == WorkspaceWorkflowStatus.ConfigurationChanged, "draft edit should be visible");
Assert(model.Confirm(request), "valid request should confirm");
var firstId = model.Start();
Assert(firstId is not null && model.IsProcessing, "confirmed request should start processing");
Assert(!model.Apply(new WorkspaceWorkerEvent.Completed("old-request", Success("old"))), "stale completion must be ignored");
Assert(model.IsProcessing, "stale completion must not change active workflow");

Assert(model.Apply(new WorkspaceWorkerEvent.Progress(firstId!, "half", 0.5)), "progress should be accepted");
Assert(model.State.ProgressFraction == 0.5, "progress should be retained");
Assert(model.Apply(new WorkspaceWorkerEvent.Completed(firstId!, Success("first"))), "active completion should be accepted");
Assert(model.State.WorkflowStatus == WorkspaceWorkflowStatus.Completed, "successful run should complete");
Assert(model.CurrentRecord?.MeasurementValidity == MetricValidityStatus.Valid, "metric validity is separate from workflow status");
Assert(model.CurrentRecord?.RecordId == "first", "record identity should be retained separately from request identity");
Assert(model.CurrentRecord?.AnalysisFingerprint == "fingerprint-first", "analysis fingerprint should be retained");
Assert(model.CurrentRecord?.Metrics?.Count == 2, "all worker metrics should be represented at the presentation seam");
Assert(model.CurrentRecord?.Metrics?.Single(metric => metric.Name == "invalid_metric").Value is null,
    "invalid metric values must be presented as N/A rather than internal attempts");
Assert(model.CurrentRecord?.QualityReasonCodes?.Contains("low_snr") == true,
    "quality reason codes should be retained for the record summary");
Assert(model.CanExportReport, "a current computed record should be exportable");

model.SetDisplaySettings(new DisplaySettings("viridis", "percentile", 2, false));
Assert(!model.State.NeedsRecalculation && model.CurrentRecord is { IsStale: false }, "display settings must not stale a record");
model.EditDraft(draft with { RegionWidth = 15 });
Assert(model.State.NeedsRecalculation && model.CurrentRecord is { IsStale: true }, "analysis configuration change should stale the record");
var request2 = AnalysisRequest.Create(input.Path, input.Sha256, 0, 0, 15, 16, null, null, null, null,
    draft.CalibrationStatus, draft.CalibrationX, draft.CalibrationY, draft.CalibrationUnits,
    draft.CalibrationSource, "derived");
Assert(model.ConfirmedRequest is null, "analysis configuration changes must clear the confirmed request");
Assert(model.Confirm(request2), "updated configuration should be confirmable");
var secondId = model.Start();
Assert(secondId is not null, "stale record should be recomputable after reconfirmation");
model.EditDraft(draft with { RegionWidth = 15, RegionHeight = 15 });
Assert(model.IsProcessing && model.State.WorkflowStatus == WorkspaceWorkflowStatus.Processing,
    "a conflicting draft edit must not interrupt the active run");
Assert(model.RequestCancel(), "active run should be cancellable");
Assert(model.Apply(new WorkspaceWorkerEvent.Completed(firstId!, Success("late-first"))) == false, "old run event must stay rejected");
Assert(model.State.WorkflowStatus == WorkspaceWorkflowStatus.Cancelling, "old event must not overwrite cancellation");
Assert(model.Apply(new WorkspaceWorkerEvent.Cancelled(secondId!, new WorkerOutcome("cancelled", "user cancelled", FailureCode: "cancelled"))), "active cancellation should be accepted");
Assert(model.CurrentRecord is { IsStale: true }, "cancelled recompute retains stale immutable record");
Assert(!model.CanExportReport, "stale retained records must not be exportable");

var request3 = AnalysisRequest.Create(input.Path, input.Sha256, 0, 0, 15, 15, null, null, null, null,
    draft.CalibrationStatus, draft.CalibrationX, draft.CalibrationY, draft.CalibrationUnits,
    draft.CalibrationSource, "derived");
Assert(model.Confirm(request3), "the staged draft should be explicitly reconfirmable");
var thirdId = model.Start();
Assert(thirdId is not null, "retry should start after cancellation");
model.EditDraft(draft with { RegionWidth = 15, RegionHeight = 14 });
Assert(model.Apply(new WorkspaceWorkerEvent.Completed(thirdId!, Success("third"))), "new run completion should be accepted");
Assert(model.CurrentRecord is { IsStale: true } && !model.CanExportReport,
    "a successful result for a superseded draft must remain stale and non-exportable");

var inputFailureModel = new WorkspacePresentationModel();
inputFailureModel.LoadInput(input);
inputFailureModel.EditDraft(draft);
Assert(inputFailureModel.Confirm(request), "failure fixture configuration should confirm");
var failedRequest = inputFailureModel.Start();
Assert(failedRequest is not null, "failure fixture should start");
Assert(inputFailureModel.Apply(new WorkspaceWorkerEvent.Failed(
    failedRequest!, new WorkerOutcome("failure", "bad input", FailureCode: "input_hash_mismatch", FlowStatus: "input_invalid"))),
    "structured input failure should be accepted");
Assert(inputFailureModel.State.WorkflowStatus == WorkspaceWorkflowStatus.InputInvalid,
    "input-invalid flow must remain distinct from generic analysis failure");

var protocolModel = new WorkspacePresentationModel();
protocolModel.LoadInput(input);
protocolModel.EditDraft(draft);
Assert(protocolModel.Confirm(request), "protocol fixture configuration should confirm");
var protocolRequest = protocolModel.Start();
Assert(protocolRequest is not null, "protocol fixture should start");
Assert(protocolModel.Apply(new WorkspaceWorkerEvent.ProtocolError(protocolRequest!, "malformed event")),
    "protocol failure should be accepted");
Assert(protocolModel.State.WorkflowStatus == WorkspaceWorkflowStatus.ProtocolError,
    "protocol failures must be distinct from analysis failures");

var exportStateModel = new WorkspacePresentationModel();
exportStateModel.LoadInput(input);
exportStateModel.EditDraft(draft);
Assert(exportStateModel.Confirm(request), "export fixture configuration should confirm");
var exportRequest = exportStateModel.Start();
Assert(exportRequest is not null, "export fixture should start");
Assert(exportStateModel.Apply(new WorkspaceWorkerEvent.Completed(exportRequest!, Success("export-state"))),
    "export fixture should complete");
exportStateModel.ReportExportFailed("report_write_failed", "destination unavailable");
Assert(exportStateModel.State.WorkflowStatus == WorkspaceWorkflowStatus.ExportFailed,
    "export failure must be a distinct workflow status");
Assert(exportStateModel.CanExportReport, "a current record should remain retryable after export failure");
exportStateModel.ReportExported();
Assert(exportStateModel.State.WorkflowStatus == WorkspaceWorkflowStatus.Exported,
    "successful report export should be visible in workflow state");

var reportDirectory = Path.Combine(Path.GetTempPath(), "SpotAnalysis-WorkspaceHarness-" + Guid.NewGuid().ToString("N"));
Directory.CreateDirectory(reportDirectory);
try
{
    using var reportDocument = JsonDocument.Parse("{\"record\":{\"record_id\":\"report-record\",\"flow_status\":\"computed\",\"summary_status\":\"caution\",\"quality_reason_codes\":[\"low_snr\"],\"metrics\":{\"fwhm\":{\"value\":17,\"unit\":\"px\",\"status\":\"valid\",\"reason_codes\":[]},\"ee80\":{\"value\":99,\"unit\":\"px\",\"status\":\"invalid\",\"reason_codes\":[\"roi_truncated\"]}}}}");
    var pngSpec = new ReportSpecification("png", "报告 / collision", reportDirectory);
    var firstReport = RunOnSta(() => ReportExporter.Write(reportDocument.RootElement, pngSpec));
    var secondReport = RunOnSta(() => ReportExporter.Write(reportDocument.RootElement, pngSpec));
    Assert(firstReport.FlowStatus == "exported" && secondReport.FlowStatus == "exported", "PNG report export should succeed");
    Assert(firstReport.Path is not null && secondReport.Path is not null && firstReport.Path != secondReport.Path,
        "report export must use collision-safe paths");
    Assert(firstReport.Path!.EndsWith(".png", StringComparison.Ordinal) && File.Exists(firstReport.Path),
        "PNG report should expose its actual output path");
    var pdfSpec = new ReportSpecification("pdf", "pdf-report", reportDirectory, AppendTimestamp: true);
    var pdfReport = RunOnSta(() => ReportExporter.Write(reportDocument.RootElement, pdfSpec));
    Assert(pdfReport.FlowStatus == "exported" && pdfReport.Path is not null && pdfReport.Path.EndsWith(".pdf", StringComparison.Ordinal),
        "PDF report export should expose its actual path");
    var gated = RunOnSta(() => ReportExporter.Write(
        JsonDocument.Parse("{\"record\":{\"record_id\":\"stale\",\"flow_status\":\"input_invalid\"}}").RootElement,
        new ReportSpecification("png", "stale", reportDirectory)));
    Assert(gated.ErrorCode == "report_record_not_current", "non-computed records must be gated from report export");
    var cleanName = ReportExporter.SanitizeName("中文报告 / collision");
    Assert(!cleanName.Contains('/') && !cleanName.Contains('\\'), "report names should be safely cleaned");

    var originalInput = Path.Combine(reportDirectory, "input.png");
    File.WriteAllBytes(originalInput, [1, 2, 3]);
    var diagnosticSnapshot = new DiagnosticSnapshot(
        "computed", null, null, "report-record", "fingerprint-report", "input summary", "hash", originalInput,
        new Dictionary<string, object?>(), reportDocument.RootElement.Clone(), Array.Empty<JsonElement>(),
        "caution", ["low_snr"]);
    var diagnosticJson = Path.Combine(reportDirectory, "diagnostics.json");
    DiagnosticPackage.Write(diagnosticJson, diagnosticSnapshot, includeOriginalImage: false);
    using (var diagnosticDocument = JsonDocument.Parse(File.ReadAllText(diagnosticJson)))
    {
        Assert(diagnosticDocument.RootElement.GetProperty("input").GetProperty("image_attached").GetBoolean() == false,
            "diagnostic attachment must be opt-in");
    }
    var diagnosticZip = Path.Combine(reportDirectory, "diagnostics.zip");
    DiagnosticPackage.Write(diagnosticZip, diagnosticSnapshot, includeOriginalImage: true);
    using (var archive = System.IO.Compression.ZipFile.OpenRead(diagnosticZip))
    {
        Assert(archive.GetEntry("diagnostics.json") is not null, "diagnostic ZIP should include its summary");
        Assert(archive.GetEntry("input/input.png") is not null, "explicit diagnostic attachment should include the original image");
    }
}
finally
{
    try { Directory.Delete(reportDirectory, recursive: true); } catch (IOException) { }
}

Console.WriteLine("Workspace presentation behavior passed");

static WorkerOutcome Success(string id)
{
    using var document = JsonDocument.Parse($"{{\"record\":{{\"record_id\":\"{id}\",\"analysis_fingerprint\":\"fingerprint-{id}\",\"flow_status\":\"computed\",\"measurement_validity\":\"valid\",\"quality_reason_codes\":[\"low_snr\"],\"metrics\":{{\"valid_metric\":{{\"value\":3.2,\"unit\":\"mm\",\"status\":\"valid\",\"reason_codes\":[]}},\"invalid_metric\":{{\"value\":999,\"unit\":\"mm\",\"status\":\"invalid\",\"reason_codes\":[\"low_snr\"]}}}}}}}}");
    return new WorkerOutcome("success", null, RecordId: id, Result: document.RootElement.Clone(), FlowStatus: "computed");
}
