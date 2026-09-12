using System.Text.Json;

namespace SpotAnalysis.App;

public enum WorkspaceWorkflowStatus
{
    Ready,
    InputLoaded,
    ConfigurationChanged,
    ConfigurationConfirmed,
    Processing,
    Cancelling,
    Completed,
    Cancelled,
    Failed,
    TimedOut,
    InputInvalid,
    ConfigurationInvalid,
    NeedsRecalculation,
}

public enum MetricValidityStatus
{
    Unknown,
    Valid,
    Caution,
    Invalid,
    Unavailable,
}

public sealed record WorkspaceInput(
    string Path,
    string Sha256,
    int Width,
    int Height,
    int BitDepth,
    string Summary);

/// <summary>Unsubmitted analysis choices. Display choices are deliberately not included.</summary>
public sealed record AnalysisDraft(
    int RegionX,
    int RegionY,
    int RegionWidth,
    int RegionHeight,
    int? BackgroundX,
    int? BackgroundY,
    int? BackgroundWidth,
    int? BackgroundHeight,
    string CalibrationStatus,
    double? CalibrationX,
    double? CalibrationY,
    string CalibrationUnits,
    string CalibrationSource);

/// <summary>Settings that alter rendering only, never analysis identity or staleness.</summary>
public sealed record DisplaySettings(
    string ColorMap = "gray",
    string DisplayRange = "full",
    double Zoom = 1.0,
    bool ShowOverlays = true);

public sealed record AnalysisRecordSnapshot(
    string RequestId,
    WorkerOutcome Outcome,
    MetricValidityStatus MeasurementValidity,
    bool IsStale);

public abstract record WorkspaceWorkerEvent(string RequestId)
{
    public sealed record Started(string Id) : WorkspaceWorkerEvent(Id);
    public sealed record Progress(string Id, string Message, double? Fraction = null) : WorkspaceWorkerEvent(Id);
    public sealed record Completed(string Id, WorkerOutcome Outcome) : WorkspaceWorkerEvent(Id);
    public sealed record Failed(string Id, WorkerOutcome Outcome) : WorkspaceWorkerEvent(Id);
    public sealed record Cancelled(string Id, WorkerOutcome Outcome) : WorkspaceWorkerEvent(Id);
    public sealed record ProtocolError(string Id, string Message) : WorkspaceWorkerEvent(Id);
}

/// <summary>
/// Testable presentation seam for the WPF workspace. It owns workflow state and
/// identity checks; it does not perform PNG decoding, measurement, report writing,
/// or diagnostics serialization.
/// </summary>
public sealed class WorkspacePresentationModel
{
    private long _nextRequestNumber;
    private string? _activeRequestId;
    private bool _cancelRequested;

    public WorkspacePresentationState State { get; private set; } = WorkspacePresentationState.Empty;

    public event EventHandler? Changed;

    public WorkspaceInput? Input => State.Input;
    public AnalysisDraft? Draft => State.Draft;
    public AnalysisRequest? ConfirmedRequest => State.ConfirmedRequest;
    public AnalysisRecordSnapshot? CurrentRecord => State.CurrentRecord;
    public DisplaySettings Display => State.Display;
    public bool CanRun => State.Input is not null && State.ConfirmedRequest is not null
        && State.WorkflowStatus is WorkspaceWorkflowStatus.ConfigurationConfirmed or WorkspaceWorkflowStatus.NeedsRecalculation;
    public bool CanExportReport => State.CurrentRecord is not null;
    public bool IsProcessing => State.WorkflowStatus is WorkspaceWorkflowStatus.Processing or WorkspaceWorkflowStatus.Cancelling;

    public void LoadInput(WorkspaceInput input)
    {
        _activeRequestId = null;
        _cancelRequested = false;
        State = State with
        {
            Input = input,
            ConfirmedRequest = null,
            WorkflowStatus = WorkspaceWorkflowStatus.InputLoaded,
            NeedsRecalculation = State.CurrentRecord is not null,
            FailureCode = null,
            FailureMessage = null,
        };
        MarkRecordStale();
    }

    public void RejectInput(string code, string message)
    {
        _activeRequestId = null;
        State = State with
        {
            Input = null,
            ConfirmedRequest = null,
            WorkflowStatus = WorkspaceWorkflowStatus.InputInvalid,
            FailureCode = code,
            FailureMessage = message,
        };
        Changed?.Invoke(this, EventArgs.Empty);
    }

    public void EditDraft(AnalysisDraft draft)
    {
        var changed = !Equals(State.Draft, draft);
        State = State with { Draft = draft };
        if (changed && State.CurrentRecord is not null)
            MarkRecordStale();
        else if (changed)
        {
            State = State with { ConfirmedRequest = null, WorkflowStatus = WorkspaceWorkflowStatus.ConfigurationChanged };
            Changed?.Invoke(this, EventArgs.Empty);
        }
    }

    public bool Confirm(AnalysisRequest request)
    {
        if (State.Input is null)
            return RejectConfiguration("input_required", "Open an input before confirming configuration.");
        State = State with
        {
            ConfirmedRequest = request,
            WorkflowStatus = WorkspaceWorkflowStatus.ConfigurationConfirmed,
            NeedsRecalculation = State.CurrentRecord is not null,
            FailureCode = null,
            FailureMessage = null,
        };
        Changed?.Invoke(this, EventArgs.Empty);
        return true;
    }

    public bool RejectConfiguration(string code, string message)
    {
        State = State with
        {
            ConfirmedRequest = null,
            WorkflowStatus = WorkspaceWorkflowStatus.ConfigurationInvalid,
            FailureCode = code,
            FailureMessage = message,
        };
        Changed?.Invoke(this, EventArgs.Empty);
        return false;
    }

    public string? Start()
    {
        if (!CanRun || State.ConfirmedRequest is null)
            return null;
        var id = $"workspace-{++_nextRequestNumber}";
        _activeRequestId = id;
        _cancelRequested = false;
        State = State with { WorkflowStatus = WorkspaceWorkflowStatus.Processing, ActiveRequestId = id, FailureCode = null, FailureMessage = null };
        Changed?.Invoke(this, EventArgs.Empty);
        return id;
    }

    public bool RequestCancel()
    {
        if (!IsProcessing || _activeRequestId is null)
            return false;
        _cancelRequested = true;
        State = State with { WorkflowStatus = WorkspaceWorkflowStatus.Cancelling };
        Changed?.Invoke(this, EventArgs.Empty);
        return true;
    }

    public bool Apply(WorkspaceWorkerEvent workerEvent)
    {
        if (_activeRequestId is null || workerEvent.RequestId != _activeRequestId)
            return false;

        switch (workerEvent)
        {
            case WorkspaceWorkerEvent.Started:
                State = State with { WorkflowStatus = WorkspaceWorkflowStatus.Processing };
                break;
            case WorkspaceWorkerEvent.Progress progress:
                State = State with { ProgressMessage = progress.Message, ProgressFraction = progress.Fraction };
                break;
            case WorkspaceWorkerEvent.Completed completed when completed.Outcome.Status == "success":
                State = State with
                {
                    WorkflowStatus = WorkspaceWorkflowStatus.Completed,
                    CurrentRecord = CreateRecord(completed.RequestId, completed.Outcome, isStale: false),
                    NeedsRecalculation = false,
                    FailureCode = null,
                    FailureMessage = null,
                    ActiveRequestId = null,
                };
                _activeRequestId = null;
                break;
            case WorkspaceWorkerEvent.Cancelled cancelled:
                FinishWithoutReplacingRecord(cancelled.Outcome, WorkspaceWorkflowStatus.Cancelled);
                break;
            case WorkspaceWorkerEvent.Failed failed:
                FinishWithoutReplacingRecord(failed.Outcome, failed.Outcome.Status == "timeout" ? WorkspaceWorkflowStatus.TimedOut : WorkspaceWorkflowStatus.Failed);
                break;
            case WorkspaceWorkerEvent.ProtocolError protocol:
                FinishWithoutReplacingRecord(new WorkerOutcome("failure", protocol.Message, FailureCode: "worker_protocol_invalid"), WorkspaceWorkflowStatus.Failed);
                break;
            default:
                return false;
        }
        Changed?.Invoke(this, EventArgs.Empty);
        return true;
    }

    public void SetDisplaySettings(DisplaySettings settings)
    {
        State = State with { Display = settings };
        Changed?.Invoke(this, EventArgs.Empty);
    }

    private void FinishWithoutReplacingRecord(WorkerOutcome outcome, WorkspaceWorkflowStatus status)
    {
        State = State with
        {
            WorkflowStatus = status,
            FailureCode = outcome.FailureCode,
            FailureMessage = outcome.ErrorMessage,
            NeedsRecalculation = State.CurrentRecord is not null || _cancelRequested,
            CurrentRecord = State.CurrentRecord is null ? null : State.CurrentRecord with { IsStale = true },
            ActiveRequestId = null,
        };
        _activeRequestId = null;
        _cancelRequested = false;
    }

    private void MarkRecordStale()
    {
        State = State with
        {
            ConfirmedRequest = null,
            WorkflowStatus = State.CurrentRecord is null ? State.WorkflowStatus : WorkspaceWorkflowStatus.NeedsRecalculation,
            NeedsRecalculation = State.CurrentRecord is not null,
            CurrentRecord = State.CurrentRecord is null ? null : State.CurrentRecord with { IsStale = true },
        };
        Changed?.Invoke(this, EventArgs.Empty);
    }

    private static AnalysisRecordSnapshot CreateRecord(string requestId, WorkerOutcome outcome, bool isStale)
    {
        var validity = MetricValidityStatus.Unknown;
        if (outcome.Result is JsonElement terminal && terminal.TryGetProperty("record", out var record)
            && record.ValueKind == JsonValueKind.Object)
        {
            var value = record.TryGetProperty("measurement_validity", out var validityNode)
                ? validityNode.ToString()
                : record.TryGetProperty("summary_status", out var summaryNode) ? summaryNode.ToString() : null;
            validity = value?.ToLowerInvariant() switch
            {
                "valid" => MetricValidityStatus.Valid,
                "caution" or "warning" => MetricValidityStatus.Caution,
                "invalid" => MetricValidityStatus.Invalid,
                "unavailable" or "not_applicable" => MetricValidityStatus.Unavailable,
                _ => MetricValidityStatus.Unknown,
            };
        }
        return new AnalysisRecordSnapshot(requestId, outcome, validity, isStale);
    }
}

public sealed record WorkspacePresentationState(
    WorkspaceInput? Input,
    AnalysisDraft? Draft,
    AnalysisRequest? ConfirmedRequest,
    AnalysisRecordSnapshot? CurrentRecord,
    DisplaySettings Display,
    WorkspaceWorkflowStatus WorkflowStatus,
    bool NeedsRecalculation,
    string? ActiveRequestId,
    string? ProgressMessage,
    double? ProgressFraction,
    string? FailureCode,
    string? FailureMessage)
{
    public static WorkspacePresentationState Empty { get; } = new(
        null, null, null, null, new DisplaySettings(), WorkspaceWorkflowStatus.Ready,
        false, null, null, null, null, null);
}
