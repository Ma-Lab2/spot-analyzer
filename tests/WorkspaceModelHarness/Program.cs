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
Assert(model.RequestCancel(), "active run should be cancellable");
Assert(model.Apply(new WorkspaceWorkerEvent.Completed(firstId!, Success("late-first"))) == false, "old run event must stay rejected");
Assert(model.State.WorkflowStatus == WorkspaceWorkflowStatus.Cancelling, "old event must not overwrite cancellation");
Assert(model.Apply(new WorkspaceWorkerEvent.Cancelled(secondId!, new WorkerOutcome("cancelled", "user cancelled", FailureCode: "cancelled"))), "active cancellation should be accepted");
Assert(model.CurrentRecord is { IsStale: true }, "cancelled recompute retains stale immutable record");

Console.WriteLine("Workspace presentation behavior passed");

static WorkerOutcome Success(string id)
{
    using var document = JsonDocument.Parse($"{{\"record\":{{\"record_id\":\"{id}\",\"measurement_validity\":\"valid\"}}}}");
    return new WorkerOutcome("success", null, RecordId: id, Result: document.RootElement.Clone());
}
