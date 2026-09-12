using System.Text.Json;
using SpotAnalysis.App;

static void Assert(bool condition, string message)
{
    if (!condition) throw new InvalidOperationException(message);
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
Assert(model.CanExportReport, "a current completed record should be exportable");

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

Console.WriteLine("Workspace presentation behavior passed");

static WorkerOutcome Success(string id)
{
    using var document = JsonDocument.Parse($"{{\"record\":{{\"record_id\":\"{id}\",\"measurement_validity\":\"valid\"}}}}");
    return new WorkerOutcome("success", null, RecordId: id, Result: document.RootElement.Clone());
}
