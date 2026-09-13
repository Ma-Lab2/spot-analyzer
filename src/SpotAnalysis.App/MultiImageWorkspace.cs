namespace SpotAnalysis.App;

public enum WorkspaceImageStatus
{
    Unprocessed,
    Preview,
    ReviewRequired,
    Formal,
    Warning,
    Failed,
    Exported,
}

public sealed class MultiImageWorkspaceItem
{
    internal MultiImageWorkspaceItem(string id, WorkspaceInput input)
    {
        Id = id;
        Input = input;
        Presentation = new WorkspacePresentationModel();
        Presentation.LoadInput(input);
    }

    public string Id { get; }
    public WorkspaceInput Input { get; }
    public WorkspacePresentationModel Presentation { get; }
    public string DisplayName => Path.GetFileName(Input.Path);

    public WorkspaceImageStatus Status
    {
        get
        {
            var state = Presentation.State;
            if (state.WorkflowStatus == WorkspaceWorkflowStatus.Exported)
                return WorkspaceImageStatus.Exported;
            if (state.WorkflowStatus is WorkspaceWorkflowStatus.Failed
                or WorkspaceWorkflowStatus.AnalysisFailed
                or WorkspaceWorkflowStatus.WorkerError
                or WorkspaceWorkflowStatus.ProtocolError
                or WorkspaceWorkflowStatus.InputInvalid
                or WorkspaceWorkflowStatus.ConfigurationInvalid
                or WorkspaceWorkflowStatus.ExportFailed
                or WorkspaceWorkflowStatus.TimedOut)
                return WorkspaceImageStatus.Failed;
            if (state.NeedsRecalculation || state.CurrentRecord?.IsStale == true)
                return WorkspaceImageStatus.ReviewRequired;
            if (state.CurrentRecord is { } record
                && record.MeasurementValidity is MetricValidityStatus.Caution
                    or MetricValidityStatus.Invalid or MetricValidityStatus.Unavailable)
                return WorkspaceImageStatus.Warning;
            if (state.CurrentRecord is { IsFormal: true })
                return WorkspaceImageStatus.Formal;
            if (state.CurrentRecord is { IsPreview: true })
                return WorkspaceImageStatus.Preview;
            return WorkspaceImageStatus.Unprocessed;
        }
    }

    public string StatusText => Status switch
    {
        WorkspaceImageStatus.Unprocessed => "未处理",
        WorkspaceImageStatus.Preview => "预览",
        WorkspaceImageStatus.ReviewRequired => "待复核",
        WorkspaceImageStatus.Formal => "正式",
        WorkspaceImageStatus.Warning => "警告",
        WorkspaceImageStatus.Failed => "失败",
        WorkspaceImageStatus.Exported => "已导出",
        _ => "未处理",
    };

    public override string ToString() => $"{DisplayName} · {StatusText}";
}

/// <summary>
/// Client-side collection of independent image work items. Each item keeps its own
/// presentation state machine; no scientific or worker-protocol behavior is copied here.
/// </summary>
public sealed class MultiImageWorkspaceModel
{
    private readonly List<MultiImageWorkspaceItem> _items = [];
    private long _nextItemNumber;
    private RoiValues? _lockedRoi;
    private CalibrationValues? _batchCalibration;

    public IReadOnlyList<MultiImageWorkspaceItem> Items => _items.AsReadOnly();
    public MultiImageWorkspaceItem? SelectedItem { get; private set; }
    public bool IsRoiLocked => _lockedRoi is not null;

    public event EventHandler? Changed;

    public MultiImageWorkspaceItem Add(WorkspaceInput input)
    {
        var item = new MultiImageWorkspaceItem($"image-{++_nextItemNumber}", input);
        item.Presentation.Changed += ItemChanged;
        _items.Add(item);
        SelectedItem ??= item;
        Changed?.Invoke(this, EventArgs.Empty);
        return item;
    }

    public bool Select(string itemId)
    {
        var item = Find(itemId);
        if (item is null || ReferenceEquals(item, SelectedItem))
            return item is not null;
        SelectedItem = item;
        Changed?.Invoke(this, EventArgs.Empty);
        return true;
    }

    public bool Apply(string itemId, WorkspaceWorkerEvent workerEvent)
    {
        var item = Find(itemId);
        return item is not null && item.Presentation.Apply(workerEvent);
    }

    public bool SetAutomaticDraft(string itemId, AnalysisDraft automaticDraft)
    {
        var item = Find(itemId);
        if (item is null) return false;
        var draft = ApplyExplicitDefaults(automaticDraft);
        if (item.Presentation.Draft is null)
        {
            item.Presentation.SetPreviewDraft(automaticDraft);
            if (!Equals(automaticDraft, draft))
                item.Presentation.EditDraft(draft);
        }
        else
        {
            item.Presentation.EditDraft(draft);
        }
        return true;
    }

    public bool LockRoiForSubsequent(string itemId)
    {
        var draft = Find(itemId)?.Presentation.Draft;
        if (draft is null) return false;
        _lockedRoi = new RoiValues(draft.RegionX, draft.RegionY, draft.RegionWidth, draft.RegionHeight);
        Changed?.Invoke(this, EventArgs.Empty);
        return true;
    }

    public void UnlockRoi()
    {
        if (_lockedRoi is null) return;
        _lockedRoi = null;
        Changed?.Invoke(this, EventArgs.Empty);
    }

    public bool ApplyCalibrationToBatch(string sourceItemId)
    {
        var source = Find(sourceItemId)?.Presentation.Draft;
        if (source is null || source.CalibrationStatus != "confirmed"
            || source.CalibrationX is null || source.CalibrationY is null
            || string.IsNullOrWhiteSpace(source.CalibrationUnits)
            || string.IsNullOrWhiteSpace(source.CalibrationSource))
            return false;

        _batchCalibration = new CalibrationValues(
            source.CalibrationStatus, source.CalibrationX, source.CalibrationY,
            source.CalibrationUnits, source.CalibrationSource);
        foreach (var item in _items.Where(item => item.Id != sourceItemId && item.Presentation.Draft is not null))
            item.Presentation.EditDraft(WithCalibration(item.Presentation.Draft!, _batchCalibration));
        Changed?.Invoke(this, EventArgs.Empty);
        return true;
    }

    private AnalysisDraft ApplyExplicitDefaults(AnalysisDraft draft)
    {
        if (_lockedRoi is { } roi)
            draft = draft with
            {
                RegionX = roi.X,
                RegionY = roi.Y,
                RegionWidth = roi.Width,
                RegionHeight = roi.Height,
            };
        if (_batchCalibration is { } calibration)
            draft = WithCalibration(draft, calibration);
        return draft;
    }

    private static AnalysisDraft WithCalibration(AnalysisDraft draft, CalibrationValues calibration) => draft with
    {
        CalibrationStatus = calibration.Status,
        CalibrationX = calibration.X,
        CalibrationY = calibration.Y,
        CalibrationUnits = calibration.Units,
        CalibrationSource = calibration.Source,
    };

    private MultiImageWorkspaceItem? Find(string itemId) =>
        _items.FirstOrDefault(item => string.Equals(item.Id, itemId, StringComparison.Ordinal));

    private void ItemChanged(object? sender, EventArgs e) => Changed?.Invoke(this, EventArgs.Empty);

    private sealed record RoiValues(int X, int Y, int Width, int Height);
    private sealed record CalibrationValues(string Status, double? X, double? Y, string Units, string Source);
}

public enum WorkspaceAnalysisPriority
{
    Current = 0,
    Background = 1,
}

public sealed record WorkspaceAnalysisJob(
    string ItemId,
    string RequestId,
    AnalysisRequest Request,
    WorkspaceAnalysisPriority Priority);

public sealed record WorkspaceAnalysisResult(
    string ItemId,
    string RequestId,
    WorkerOutcome Outcome);

/// <summary>
/// Bounded client scheduler for immutable worker requests. Worker execution remains
/// process-isolated in WorkerClient; failures and cancellation are scoped to one job.
/// </summary>
public sealed class WorkspaceAnalysisScheduler : IDisposable
{
    private readonly object _gate = new();
    private readonly Func<WorkspaceAnalysisJob, CancellationToken, Task<WorkerOutcome>> _execute;
    private readonly int _maxConcurrency;
    private readonly List<PendingJob> _pending = [];
    private readonly Dictionary<long, PendingJob> _running = [];
    private long _nextSequence;
    private bool _disposed;

    public WorkspaceAnalysisScheduler(
        Func<WorkspaceAnalysisJob, CancellationToken, Task<WorkerOutcome>> execute,
        int maxConcurrency = 1)
    {
        ArgumentNullException.ThrowIfNull(execute);
        if (maxConcurrency < 1) throw new ArgumentOutOfRangeException(nameof(maxConcurrency));
        _execute = execute;
        _maxConcurrency = maxConcurrency;
    }

    public Task<WorkspaceAnalysisResult> Schedule(WorkspaceAnalysisJob job)
    {
        ArgumentNullException.ThrowIfNull(job);
        lock (_gate)
        {
            ObjectDisposedException.ThrowIf(_disposed, this);
            var pending = new PendingJob(++_nextSequence, job);
            _pending.Add(pending);
            if (job.Priority == WorkspaceAnalysisPriority.Current)
                PreemptBackgroundLocked(job.ItemId);
            PumpLocked();
            return pending.Completion.Task;
        }
    }

    public bool Cancel(string itemId)
    {
        lock (_gate)
        {
            var cancelled = false;
            foreach (var pending in _pending.Where(candidate => candidate.Job.ItemId == itemId).ToArray())
            {
                _pending.Remove(pending);
                pending.Cancellation.Cancel();
                pending.Completion.TrySetResult(Cancelled(pending.Job));
                pending.Cancellation.Dispose();
                cancelled = true;
            }
            foreach (var running in _running.Values.Where(candidate => candidate.Job.ItemId == itemId))
            {
                running.RequeueAfterPreemption = false;
                running.Cancellation.Cancel();
                cancelled = true;
            }
            return cancelled;
        }
    }

    public bool Promote(string itemId)
    {
        lock (_gate)
        {
            var matches = _pending.Where(candidate => candidate.Job.ItemId == itemId).ToArray();
            foreach (var pending in matches)
                pending.Job = pending.Job with { Priority = WorkspaceAnalysisPriority.Current };
            if (matches.Length > 0)
                PreemptBackgroundLocked(itemId);
            return matches.Length > 0;
        }
    }

    private void PreemptBackgroundLocked(string currentItemId)
    {
        if (_running.Count < _maxConcurrency) return;
        foreach (var running in _running.Values.Where(candidate =>
            candidate.Job.Priority == WorkspaceAnalysisPriority.Background
            && candidate.Job.ItemId != currentItemId))
        {
            running.RequeueAfterPreemption = true;
            running.Cancellation.Cancel();
        }
    }

    private void PumpLocked()
    {
        while (_running.Count < _maxConcurrency && _pending.Count > 0)
        {
            var next = _pending
                .OrderBy(candidate => candidate.Job.Priority)
                .ThenBy(candidate => candidate.Sequence)
                .First();
            _pending.Remove(next);
            _running.Add(next.Sequence, next);
            _ = ExecuteAsync(next);
        }
    }

    private async Task ExecuteAsync(PendingJob pending)
    {
        WorkspaceAnalysisResult result;
        try
        {
            var outcome = await _execute(pending.Job, pending.Cancellation.Token).ConfigureAwait(false);
            result = new WorkspaceAnalysisResult(pending.Job.ItemId, pending.Job.RequestId, outcome);
        }
        catch (OperationCanceledException)
        {
            result = Cancelled(pending.Job);
        }
        catch (Exception exception)
        {
            result = new WorkspaceAnalysisResult(
                pending.Job.ItemId,
                pending.Job.RequestId,
                new WorkerOutcome("failure", exception.Message, FailureCode: "scheduler_execution_failed", FlowStatus: "worker_error"));
        }

        var requeued = false;
        lock (_gate)
        {
            _running.Remove(pending.Sequence);
            if (pending.RequeueAfterPreemption && result.Outcome.Status == "cancelled" && !_disposed)
            {
                pending.RequeueAfterPreemption = false;
                pending.Cancellation.Dispose();
                pending.Cancellation = new CancellationTokenSource();
                _pending.Add(pending);
                requeued = true;
            }
            PumpLocked();
        }
        if (requeued) return;
        pending.Completion.TrySetResult(result);
        pending.Cancellation.Dispose();
    }

    private static WorkspaceAnalysisResult Cancelled(WorkspaceAnalysisJob job) => new(
        job.ItemId,
        job.RequestId,
        new WorkerOutcome("cancelled", "The workspace item was cancelled.", FailureCode: "cancelled", FlowStatus: "cancelled"));

    public void Dispose()
    {
        lock (_gate)
        {
            if (_disposed) return;
            _disposed = true;
            foreach (var pending in _pending.ToArray())
            {
                pending.Completion.TrySetResult(Cancelled(pending.Job));
                pending.Cancellation.Dispose();
            }
            _pending.Clear();
            foreach (var running in _running.Values)
                running.Cancellation.Cancel();
        }
    }

    private sealed class PendingJob(long sequence, WorkspaceAnalysisJob job)
    {
        public long Sequence { get; } = sequence;
        public WorkspaceAnalysisJob Job { get; set; } = job;
        public CancellationTokenSource Cancellation { get; set; } = new();
        public bool RequeueAfterPreemption { get; set; }
        public TaskCompletionSource<WorkspaceAnalysisResult> Completion { get; } =
            new(TaskCreationOptions.RunContinuationsAsynchronously);
    }
}
