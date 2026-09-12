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
// The rendering definition lives in DisplayProjection.cs; this alias-compatible
// shape keeps the presentation model and renderer on one immutable value.


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
    private bool _draftChangedDuringRun;

    public WorkspacePresentationState State { get; private set; } = WorkspacePresentationState.Empty;

    public event EventHandler? Changed;

    public WorkspaceInput? Input => State.Input;
    public AnalysisDraft? Draft => State.Draft;
    public AnalysisRequest? ConfirmedRequest => State.ConfirmedRequest;
    public AnalysisRecordSnapshot? CurrentRecord => State.CurrentRecord;
    public DisplaySettings Display => State.Display;
    public bool CanRun => State.Input is not null && State.ConfirmedRequest is not null
        && State.WorkflowStatus is WorkspaceWorkflowStatus.ConfigurationConfirmed or WorkspaceWorkflowStatus.NeedsRecalculation;
    public bool CanExportReport => !IsProcessing && State.CurrentRecord is { IsStale: false };
    public bool IsProcessing => State.WorkflowStatus is WorkspaceWorkflowStatus.Processing or WorkspaceWorkflowStatus.Cancelling;
    public bool CanEditConfiguration => !IsProcessing;

    public bool LoadInput(WorkspaceInput input)
    {
        if (IsProcessing)
        {
            State = State with
            {
                FailureCode = "analysis_in_progress",
                FailureMessage = "Cancel the active analysis before changing the input.",
            };
            Changed?.Invoke(this, EventArgs.Empty);
            return false;
        }

        _activeRequestId = null;
        _draftChangedDuringRun = false;
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
        return true;
    }

    public void RejectInput(string code, string message)
    {
        if (IsProcessing)
        {
            State = State with { FailureCode = "analysis_in_progress", FailureMessage = "Cancel the active analysis before changing the input." };
            Changed?.Invoke(this, EventArgs.Empty);
            return;
        }

        _activeRequestId = null;
        State = State with
        {
            Input = null,
            ConfirmedRequest = null,
            WorkflowStatus = WorkspaceWorkflowStatus.InputInvalid,
            NeedsRecalculation = State.CurrentRecord is not null,
            CurrentRecord = State.CurrentRecord is null ? null : State.CurrentRecord with { IsStale = true },
            FailureCode = code,
            FailureMessage = message,
        };
        Changed?.Invoke(this, EventArgs.Empty);
    }

    public void EditDraft(AnalysisDraft draft)
    {
        var changed = !Equals(State.Draft, draft);
        State = State with { Draft = draft };
        if (!changed)
            return;

        // The active request is immutable. During a run, retain the new draft as
        // staged intent, but do not revoke the request or disturb cancellation.
        if (IsProcessing)
        {
            _draftChangedDuringRun = true;
            Changed?.Invoke(this, EventArgs.Empty);
            return;
        }

        if (State.CurrentRecord is not null)
            MarkRecordStale();
        else
        {
            State = State with { ConfirmedRequest = null, WorkflowStatus = WorkspaceWorkflowStatus.ConfigurationChanged };
            Changed?.Invoke(this, EventArgs.Empty);
        }
    }

    private static bool RequestMatchesCurrentDraft(AnalysisRequest request, WorkspaceInput input, AnalysisDraft draft)
    {
        try
        {
            using var document = JsonDocument.Parse(request.Summary);
            var root = document.RootElement;
            var asset = root.GetProperty("input").GetProperty("asset");
            if (!string.Equals(asset.GetProperty("path").GetString(), input.Path, StringComparison.Ordinal)
                || !string.Equals(asset.GetProperty("expected_sha256").GetString(), input.Sha256, StringComparison.Ordinal))
                return false;

            var configuration = root.GetProperty("configuration");
            var region = configuration.GetProperty("region");
            if (region.GetProperty("x").GetInt32() != draft.RegionX
                || region.GetProperty("y").GetInt32() != draft.RegionY
                || region.GetProperty("width").GetInt32() != draft.RegionWidth
                || region.GetProperty("height").GetInt32() != draft.RegionHeight)
                return false;

            var background = configuration.GetProperty("background_region");
            if (draft.BackgroundX.HasValue != (background.ValueKind != JsonValueKind.Null))
                return false;
            if (draft.BackgroundX.HasValue && (background.GetProperty("x").GetInt32() != draft.BackgroundX
                || background.GetProperty("y").GetInt32() != draft.BackgroundY
                || background.GetProperty("width").GetInt32() != draft.BackgroundWidth
                || background.GetProperty("height").GetInt32() != draft.BackgroundHeight))
                return false;

            var calibration = configuration.GetProperty("calibration");
            if (!string.Equals(calibration.GetProperty("confirmation").GetString(), draft.CalibrationStatus, StringComparison.Ordinal)
                || !string.Equals(calibration.GetProperty("physical_unit").GetString(), draft.CalibrationUnits, StringComparison.Ordinal)
                || !string.Equals(calibration.GetProperty("source").GetString(), draft.CalibrationSource, StringComparison.Ordinal))
                return false;
            return NullableDoubleEquals(calibration.GetProperty("x_unit_per_pixel"), draft.CalibrationX)
                && NullableDoubleEquals(calibration.GetProperty("y_unit_per_pixel"), draft.CalibrationY);
        }
        catch (Exception exception) when (exception is JsonException or KeyNotFoundException or InvalidOperationException or FormatException)
        {
            return false;
        }
    }

    private static bool NullableDoubleEquals(JsonElement value, double? expected) =>
        value.ValueKind == JsonValueKind.Number
            ? expected.HasValue && Math.Abs(value.GetDouble() - expected.Value) < 1e-12
            : value.ValueKind == JsonValueKind.Null && !expected.HasValue;

    public void StageInvalidDraft(string message = "The unsubmitted configuration is not yet valid.")
    {
        if (IsProcessing)
        {
            _draftChangedDuringRun = true;
            State = State with { FailureCode = "configuration_changed", FailureMessage = message };
            Changed?.Invoke(this, EventArgs.Empty);
            return;
        }

        RejectConfiguration("configuration_changed", message);
    }

    public bool Confirm(AnalysisRequest request)
    {
        if (IsProcessing)
        {
            State = State with
            {
                FailureCode = "analysis_in_progress",
                FailureMessage = "Cancel the active analysis before confirming a changed configuration.",
            };
            Changed?.Invoke(this, EventArgs.Empty);
            return false;
        }
        if (!request.IsValid)
            return RejectConfiguration(request.ValidationError ?? "invalid_configuration", "The confirmed analysis request is invalid.");
        if (State.Input is null)
            return RejectConfiguration("input_required", "Open an input before confirming configuration.");
        if (State.Draft is null || !RequestMatchesCurrentDraft(request, State.Input, State.Draft))
            return RejectConfiguration("configuration_snapshot_mismatch", "The confirmed request does not match the staged input and configuration.");
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
        _draftChangedDuringRun = false;
        State = State with { WorkflowStatus = WorkspaceWorkflowStatus.Processing, ActiveRequestId = id, FailureCode = null, FailureMessage = null };
        Changed?.Invoke(this, EventArgs.Empty);
        return id;
    }

    public bool RequestCancel()
    {
        if (!IsProcessing || _activeRequestId is null)
            return false;
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
                if (State.WorkflowStatus != WorkspaceWorkflowStatus.Cancelling)
                    State = State with { WorkflowStatus = WorkspaceWorkflowStatus.Processing };
                break;
            case WorkspaceWorkerEvent.Progress progress:
                State = State with { ProgressMessage = progress.Message, ProgressFraction = progress.Fraction };
                break;
            case WorkspaceWorkerEvent.Completed completed when completed.Outcome.Status == "success":
                if (!TryCreateRecord(completed.RequestId, completed.Outcome, _draftChangedDuringRun, out var completedRecord))
                {
                    FinishWithoutReplacingRecord(
                        new WorkerOutcome("failure", "The worker completed without a complete analysis record.", FailureCode: "worker_protocol_invalid"),
                        WorkspaceWorkflowStatus.Failed);
                    Changed?.Invoke(this, EventArgs.Empty);
                    return true;
                }
                if (State.CurrentRecord is { Outcome.RecordId: { } priorId }
                    && completed.Outcome.RecordId is { } completedId
                    && string.Equals(priorId, completedId, StringComparison.Ordinal))
                {
                    FinishWithoutReplacingRecord(
                        new WorkerOutcome("failure", "The worker reused an existing analysis record identity.", FailureCode: "record_identity_reused"),
                        WorkspaceWorkflowStatus.Failed);
                    Changed?.Invoke(this, EventArgs.Empty);
                    return true;
                }
                State = State with
                {
                    WorkflowStatus = _draftChangedDuringRun ? WorkspaceWorkflowStatus.NeedsRecalculation : WorkspaceWorkflowStatus.Completed,
                    CurrentRecord = completedRecord,
                    NeedsRecalculation = _draftChangedDuringRun,
                    ConfirmedRequest = _draftChangedDuringRun ? null : State.ConfirmedRequest,
                    FailureCode = null,
                    FailureMessage = _draftChangedDuringRun ? "Configuration changed while this run was processing; recompute with the staged draft." : null,
                    ActiveRequestId = null,
                };
                _activeRequestId = null;
                _draftChangedDuringRun = false;
                break;
            case WorkspaceWorkerEvent.Completed completed:
                FinishWithoutReplacingRecord(
                    new WorkerOutcome("failure", "The worker returned a contradictory completion event.", FailureCode: "worker_protocol_invalid"),
                    WorkspaceWorkflowStatus.Failed);
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
            NeedsRecalculation = State.CurrentRecord?.IsStale == true,
            CurrentRecord = State.CurrentRecord,
            ActiveRequestId = null,
        };
        _activeRequestId = null;
        _draftChangedDuringRun = false;
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

    private static bool TryCreateRecord(
        string requestId,
        WorkerOutcome outcome,
        bool isStale,
        out AnalysisRecordSnapshot? snapshot)
    {
        snapshot = null;
        if (outcome.Result is not JsonElement terminal
            || !terminal.TryGetProperty("record", out var record)
            || record.ValueKind != JsonValueKind.Object
            || string.IsNullOrWhiteSpace(outcome.RecordId))
            return false;

        var value = record.TryGetProperty("measurement_validity", out var validityNode)
            ? validityNode.ToString()
            : record.TryGetProperty("summary_status", out var summaryNode) ? summaryNode.ToString() : null;
        var validity = value?.ToLowerInvariant() switch
        {
            "valid" => MetricValidityStatus.Valid,
            "caution" or "warning" => MetricValidityStatus.Caution,
            "invalid" => MetricValidityStatus.Invalid,
            "unavailable" or "not_applicable" => MetricValidityStatus.Unavailable,
            _ => MetricValidityStatus.Unknown,
        };
        snapshot = new AnalysisRecordSnapshot(requestId, outcome, validity, isStale);
        return true;
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
