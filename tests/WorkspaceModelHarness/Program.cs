using System.Buffers.Binary;
using System.IO.Compression;
using System.Security.Cryptography;
using System.Text;
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

static byte[] PngBytes(int width, int height, int bitDepth, int colorType, ushort[][] rows, bool differentRgb = false)
{
    var channels = colorType == 0 ? 1 : 3;
    var sampleBytes = bitDepth / 8;
    Span<byte> sample = stackalloc byte[2];
    using var raw = new MemoryStream();
    foreach (var row in rows)
    {
        raw.WriteByte(0);
        foreach (var value in row)
        {
            for (var channel = 0; channel < channels; channel++)
            {
                var channelValue = differentRgb && channel == 1 ? (ushort)(value + 1) : value;
                if (sampleBytes == 1) raw.WriteByte((byte)channelValue);
                else
                {
                    BinaryPrimitives.WriteUInt16BigEndian(sample, channelValue);
                    raw.Write(sample);
                }
            }
        }
    }

    static byte[] Chunk(string kind, byte[] payload)
    {
        var kindBytes = Encoding.ASCII.GetBytes(kind);
        var chunk = new byte[12 + payload.Length];
        BinaryPrimitives.WriteUInt32BigEndian(chunk.AsSpan(0, 4), (uint)payload.Length);
        kindBytes.CopyTo(chunk, 4);
        payload.CopyTo(chunk, 8);
        BinaryPrimitives.WriteUInt32BigEndian(chunk.AsSpan(8 + payload.Length, 4), Crc32(kindBytes, payload));
        return chunk;
    }

    var ihdr = new byte[13];
    BinaryPrimitives.WriteUInt32BigEndian(ihdr.AsSpan(0, 4), (uint)width);
    BinaryPrimitives.WriteUInt32BigEndian(ihdr.AsSpan(4, 4), (uint)height);
    ihdr[8] = (byte)bitDepth; ihdr[9] = (byte)colorType;
    using var compressed = new MemoryStream();
    using (var zlib = new ZLibStream(compressed, CompressionMode.Compress, leaveOpen: true))
        zlib.Write(raw.ToArray());
    using var png = new MemoryStream();
    png.Write([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]);
    png.Write(Chunk("IHDR", ihdr));
    png.Write(Chunk("IDAT", compressed.ToArray()));
    png.Write(Chunk("IEND", []));
    return png.ToArray();
}

static uint Crc32(byte[] kind, byte[] payload)
{
    uint crc = 0xffffffff;
    foreach (var value in kind.Concat(payload))
    {
        crc ^= value;
        for (var bit = 0; bit < 8; bit++) crc = (crc & 1) != 0 ? (crc >> 1) ^ 0xedb88320 : crc >> 1;
    }
    return ~crc;
}

var issue76Directory = Path.Combine(Path.GetTempPath(), "SpotAnalysis-issue76-中文 folder");
Directory.CreateDirectory(issue76Directory);
var issue76GrayPath = Path.Combine(issue76Directory, "gray with spaces.PNG");
var issue76RgbPath = Path.Combine(issue76Directory, "equal-rgb.png");
var issue76BadRgbPath = Path.Combine(issue76Directory, "different-rgb.png");
var issue76CorruptPath = Path.Combine(issue76Directory, "corrupt.png");
var issue76GrayPayload = PngBytes(2, 1, 16, 0, [[1, 65535]]);
File.WriteAllBytes(issue76GrayPath, issue76GrayPayload);
File.WriteAllBytes(issue76RgbPath, PngBytes(2, 1, 8, 2, [[7, 251]]));
File.WriteAllBytes(issue76BadRgbPath, PngBytes(1, 1, 8, 2, [[1]], differentRgb: true));
File.WriteAllBytes(issue76CorruptPath, issue76GrayPayload[..^1]);
var grayBefore = (File.GetLastWriteTimeUtc(issue76GrayPath), File.ReadAllBytes(issue76GrayPath));
var grayInfo = RunOnSta(() => PngInput.ReadAsync(issue76GrayPath).GetAwaiter().GetResult());
Assert(grayInfo.Width == 2 && grayInfo.Height == 1 && grayInfo.BitDepth == 16, "WPF must preserve 16-bit dimensions");
Assert(grayInfo.Channels == 1 && grayInfo.IntensitySamples.SequenceEqual(new ushort[] { 1, 65535 }), "WPF must preserve 16-bit samples");
Assert(grayInfo.ByteOrder == "big" && grayInfo.Sha256 == Convert.ToHexString(SHA256.HashData(grayBefore.Item2)).ToLowerInvariant(), "WPF input identity must be deterministic");
Assert(grayInfo.UriHint.Contains("gray%20with%20spaces.PNG", StringComparison.Ordinal), "WPF input URI must preserve path identity safely");
var rgbInfo = RunOnSta(() => PngInput.ReadAsync(issue76RgbPath).GetAwaiter().GetResult());
Assert(rgbInfo.Channels == 3 && rgbInfo.ChannelsIdentical && rgbInfo.IntensitySamples.SequenceEqual(new ushort[] { 7, 251 }), "WPF must extract strict equal-channel RGB");
try
{
    RunOnSta(() => PngInput.ReadAsync(issue76BadRgbPath).GetAwaiter().GetResult());
    throw new InvalidOperationException("WPF must reject non-identical RGB");
}
catch (InputValidationException exception)
{
    Assert(exception.Code == "rgb_channels_not_identical" && exception.Message.Contains("不完全一致"), "WPF RGB rejection must be structured and actionable");
}
try
{
    RunOnSta(() => PngInput.ReadAsync(issue76CorruptPath).GetAwaiter().GetResult());
    throw new InvalidOperationException("WPF must reject corrupt PNG");
}
catch (InputValidationException exception)
{
    Assert(exception.Code == "png_decode_failed", "WPF corrupt PNG rejection must use the shared decode failure code");
}
Assert(File.GetLastWriteTimeUtc(issue76GrayPath) == grayBefore.Item1 && File.ReadAllBytes(issue76GrayPath).SequenceEqual(grayBefore.Item2), "WPF input read must not mutate source");
Directory.Delete(issue76Directory, recursive: true);

var wpfConnections = RunOnSta(() =>
{
    var app = new SpotAnalysis.App.App();
    app.InitializeComponent();
    var window = new MainWindow();
    var list = (System.Windows.Controls.ListBox)window.FindName("ImageItemsList");
    var cancel = (System.Windows.Controls.Button)window.FindName("CancelAnalysisButton");
    var roiLock = (System.Windows.Controls.CheckBox)window.FindName("LockRoiCheck");
    var batchCalibration = (System.Windows.Controls.Button)window.FindName("ApplyCalibrationBatchButton");
    list.ItemsSource = new[] { "first", "second" };
    list.SelectedIndex = 1;
    cancel.RaiseEvent(new System.Windows.RoutedEventArgs(System.Windows.Controls.Button.ClickEvent));
    roiLock.RaiseEvent(new System.Windows.RoutedEventArgs(System.Windows.Controls.Button.ClickEvent));
    batchCalibration.RaiseEvent(new System.Windows.RoutedEventArgs(System.Windows.Controls.Button.ClickEvent));
    window.Close();
    return (list.SelectedIndex, cancel is not null, roiLock is not null, batchCalibration is not null);
});
Assert(wpfConnections.SelectedIndex == 1 && wpfConnections.Item2 && wpfConnections.Item3 && wpfConnections.Item4,
    "real WPF controls for list selection, cancellation, ROI lock, and batch calibration should load with connected handlers");

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

var previewModel = new WorkspacePresentationModel();
previewModel.LoadInput(input);
var previewRequest = AnalysisRequest.CreatePreview(input.Path, input.Sha256, "derived");
var previewId = previewModel.StartPreview(previewRequest);
Assert(previewId is not null, "automatic preview should start without configuration confirmation");
Assert(previewModel.Apply(new WorkspaceWorkerEvent.Completed(previewId!, Success("preview-record", "preview"))), "preview completion should be accepted");
Assert(previewModel.CurrentRecord is { IsPreview: true, IsFormal: false }, "preview must be explicitly identified");
Assert(!previewModel.CanExportReport && previewModel.CanConfirmFormal, "preview must be non-exportable but confirmable");
previewModel.SetPreviewDraft(draft);
previewModel.EditDraft(draft with { RegionWidth = 15 });
Assert(previewModel.CurrentRecord is { IsStale: true } && previewModel.Records.Count == 1, "preview configuration changes must retain the prior record");
var freshPreviewId = previewModel.StartPreview(previewRequest);
Assert(freshPreviewId is not null, "changed configuration should expose a fresh preview seam");
Assert(previewModel.Apply(new WorkspaceWorkerEvent.Completed(freshPreviewId!, Success("preview-record-2", "preview"))), "fresh preview completion should be accepted");
Assert(previewModel.CurrentRecord is { IsPreview: true, IsStale: false } && previewModel.Records.Count == 2, "fresh preview should replace current preview without deleting history");
Assert(previewModel.Confirm(request2), "preview should support one final formal confirmation");
var formalId = previewModel.Start();
Assert(formalId is not null, "formal confirmation should start analysis");
Assert(previewModel.Apply(new WorkspaceWorkerEvent.Completed(formalId!, Success("formal-record", "formal", "invalid"))), "invalid-but-structured formal record should complete");
Assert(previewModel.CurrentRecord is { IsFormal: true, IsStale: false } && previewModel.Records.Count == 3, "formal record should be new and preserve old records");
Assert(previewModel.CanExportReport, "formal record remains reportable even when measurement validity is invalid");

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

var multi = new MultiImageWorkspaceModel();
var firstItem = multi.Add(new WorkspaceInput("first.png", "sha-first", 32, 32, 8, "first"));
var secondItem = multi.Add(new WorkspaceInput("second.png", "sha-second", 32, 32, 8, "second"));
Assert(multi.Items.Count == 2 && multi.SelectedItem?.Id == firstItem.Id, "multi-image workspace should retain all inputs and select the first");
var firstDraft = draft with { RegionX = 2, RegionY = 3, RegionWidth = 12, RegionHeight = 13 };
var secondDraft = draft with { RegionX = 7, RegionY = 8, RegionWidth = 9, RegionHeight = 10, CalibrationStatus = "missing", CalibrationX = null, CalibrationY = null, CalibrationUnits = "", CalibrationSource = "" };
multi.SetAutomaticDraft(firstItem.Id, firstDraft);
multi.SetAutomaticDraft(secondItem.Id, secondDraft);
multi.Select(secondItem.Id);
Assert(multi.SelectedItem?.Presentation.Draft == secondDraft, "selection should restore the selected image draft without cross-image state");
multi.Select(firstItem.Id);
Assert(multi.SelectedItem?.Presentation.Draft == firstDraft, "switching back should restore the first image draft");
multi.LockRoiForSubsequent(firstItem.Id);
var thirdItem = multi.Add(new WorkspaceInput("third.png", "sha-third", 32, 32, 8, "third"));
var thirdAutomatic = secondDraft with { RegionX = 1, RegionY = 1, RegionWidth = 5, RegionHeight = 5 };
multi.SetAutomaticDraft(thirdItem.Id, thirdAutomatic);
Assert(thirdItem.Presentation.Draft is { RegionX: 2, RegionY: 3, RegionWidth: 12, RegionHeight: 13 }, "ROI should apply to later images only after explicit locking");
var fourthItem = multi.Add(new WorkspaceInput("fourth.png", "sha-fourth", 32, 32, 8, "fourth"));
multi.UnlockRoi();
multi.SetAutomaticDraft(fourthItem.Id, thirdAutomatic);
Assert(fourthItem.Presentation.Draft is { RegionX: 1, RegionY: 1, RegionWidth: 5, RegionHeight: 5 }, "unlocked ROI must not inherit from the prior image");

var firstRequest = AnalysisRequest.Create(firstItem.Input.Path, firstItem.Input.Sha256, 2, 3, 12, 13, null, null, null, null,
    firstDraft.CalibrationStatus, firstDraft.CalibrationX, firstDraft.CalibrationY, firstDraft.CalibrationUnits, firstDraft.CalibrationSource, "derived");
Assert(firstItem.Presentation.Confirm(firstRequest), "first multi-image item should confirm independently");
var firstMultiRequestId = firstItem.Presentation.Start();
Assert(firstMultiRequestId is not null, "first multi-image item should start independently");
Assert(!multi.Apply(secondItem.Id, new WorkspaceWorkerEvent.Completed(firstMultiRequestId!, Success("wrong-item"))), "an event routed to another item must not overwrite it");
Assert(multi.Apply(firstItem.Id, new WorkspaceWorkerEvent.Completed(firstMultiRequestId!, Success("first-multi"))), "matching item and request identities should apply");
Assert(firstItem.Presentation.CurrentRecord?.RecordId == "first-multi" && secondItem.Presentation.CurrentRecord is null, "records must remain isolated by image");
var secondRequest = AnalysisRequest.Create(secondItem.Input.Path, secondItem.Input.Sha256, 7, 8, 9, 10, null, null, null, null,
    secondDraft.CalibrationStatus, secondDraft.CalibrationX, secondDraft.CalibrationY, secondDraft.CalibrationUnits, secondDraft.CalibrationSource, "derived");
Assert(secondItem.Presentation.Confirm(secondRequest), "second item should confirm its own calibration and ROI");
var secondMultiRequestId = secondItem.Presentation.Start();
Assert(secondMultiRequestId is not null && multi.Apply(secondItem.Id, new WorkspaceWorkerEvent.Completed(secondMultiRequestId!, Success("second-multi"))), "second item should complete independently");

multi.ApplyCalibrationToBatch(firstItem.Id);
Assert(secondItem.Presentation.Draft is { CalibrationStatus: "confirmed", CalibrationX: 1, CalibrationY: 1, CalibrationUnits: "mm", CalibrationSource: "fixture" }, "explicit batch calibration should update other image drafts");
Assert(secondItem.Presentation.CurrentRecord is { IsStale: true } && secondItem.Presentation.State.NeedsRecalculation, "batch calibration should stale affected formal records");
Assert(firstItem.Presentation.CurrentRecord is { IsStale: false }, "batch calibration should not stale an unchanged source record");

var executionOrder = new List<string>();
var gates = new Dictionary<string, TaskCompletionSource<WorkerOutcome>>();
var scheduler = new WorkspaceAnalysisScheduler(async (job, cancellationToken) =>
{
    executionOrder.Add(job.ItemId);
    var gate = new TaskCompletionSource<WorkerOutcome>(TaskCreationOptions.RunContinuationsAsynchronously);
    gates[job.ItemId] = gate;
    using var registration = cancellationToken.Register(() => gate.TrySetResult(new WorkerOutcome("cancelled", "cancelled", FailureCode: "cancelled", FlowStatus: "cancelled")));
    return await gate.Task;
}, maxConcurrency: 1);
var currentTask = scheduler.Schedule(new WorkspaceAnalysisJob(firstItem.Id, "request-current", firstRequest, WorkspaceAnalysisPriority.Current));
var backgroundFailure = scheduler.Schedule(new WorkspaceAnalysisJob(secondItem.Id, "request-background-failure", firstRequest, WorkspaceAnalysisPriority.Background));
var backgroundSuccess = scheduler.Schedule(new WorkspaceAnalysisJob(thirdItem.Id, "request-background-success", firstRequest, WorkspaceAnalysisPriority.Background));
await Task.Yield();
Assert(executionOrder.SequenceEqual(new[] { firstItem.Id }), "current image work should start before queued background preparation");
gates[firstItem.Id].SetResult(Success("scheduled-current"));
var currentResult = await currentTask;
await Task.Yield();
Assert(executionOrder.SequenceEqual(new[] { firstItem.Id, secondItem.Id }), "background work should start after current work without blocking the caller");
gates[secondItem.Id].SetResult(new WorkerOutcome("failure", "fixture failure", FailureCode: "analysis_failed", FlowStatus: "analysis_failed"));
var failedResult = await backgroundFailure;
await Task.Yield();
Assert(executionOrder.SequenceEqual(new[] { firstItem.Id, secondItem.Id, thirdItem.Id }), "one image failure must not stop later work items");
Assert(failedResult.Outcome.Status == "failure", "scheduler should preserve per-image failure outcomes");
Assert(scheduler.Cancel(thirdItem.Id), "an individual running work item should be cancellable");
var cancelledResult = await backgroundSuccess;
Assert(cancelledResult.Outcome.Status == "cancelled" && currentResult.Outcome.Status == "success", "per-image cancellation must not alter another completed item");

var timeoutSequence = new Queue<WorkerOutcome>([
    new WorkerOutcome("timeout", "fixture timeout", FailureCode: "worker_timeout", FlowStatus: "timeout"),
    Success("after-timeout"),
]);
var timeoutScheduler = new WorkspaceAnalysisScheduler((job, cancellationToken) => Task.FromResult(timeoutSequence.Dequeue()), maxConcurrency: 1);
var timeoutTask = timeoutScheduler.Schedule(new WorkspaceAnalysisJob(secondItem.Id, "timeout-item", firstRequest, WorkspaceAnalysisPriority.Background));
var afterTimeoutTask = timeoutScheduler.Schedule(new WorkspaceAnalysisJob(thirdItem.Id, "after-timeout-item", firstRequest, WorkspaceAnalysisPriority.Background));
Assert((await timeoutTask).Outcome.Status == "timeout" && (await afterTimeoutTask).Outcome.Status == "success",
    "one item timeout must not stop later background work");

var preemptOrder = new List<string>();
var preemptGates = new List<TaskCompletionSource<WorkerOutcome>>();
var secondExecutionStarted = new TaskCompletionSource(TaskCreationOptions.RunContinuationsAsynchronously);
var thirdExecutionStarted = new TaskCompletionSource(TaskCreationOptions.RunContinuationsAsynchronously);
var preemptScheduler = new WorkspaceAnalysisScheduler(async (job, cancellationToken) =>
{
    preemptOrder.Add(job.ItemId);
    var gate = new TaskCompletionSource<WorkerOutcome>(TaskCreationOptions.RunContinuationsAsynchronously);
    preemptGates.Add(gate);
    if (preemptOrder.Count == 2) secondExecutionStarted.TrySetResult();
    if (preemptOrder.Count == 3) thirdExecutionStarted.TrySetResult();
    using var registration = cancellationToken.Register(() => gate.TrySetResult(new WorkerOutcome("cancelled", "preempted", FailureCode: "cancelled", FlowStatus: "cancelled")));
    return await gate.Task;
}, maxConcurrency: 1);
var preemptedBackground = preemptScheduler.Schedule(new WorkspaceAnalysisJob(secondItem.Id, "background-preempted", firstRequest, WorkspaceAnalysisPriority.Background));
var promotedCurrent = preemptScheduler.Schedule(new WorkspaceAnalysisJob(firstItem.Id, "current-promoted", firstRequest, WorkspaceAnalysisPriority.Current));
await secondExecutionStarted.Task.WaitAsync(TimeSpan.FromSeconds(2));
Assert(preemptOrder.SequenceEqual(new[] { secondItem.Id, firstItem.Id }), "promoted current work should preempt a running background preparation");
preemptGates[1].SetResult(Success("promoted-current"));
Assert((await promotedCurrent).Outcome.Status == "success", "promoted current work should complete normally");
await thirdExecutionStarted.Task.WaitAsync(TimeSpan.FromSeconds(2));
Assert(preemptOrder.SequenceEqual(new[] { secondItem.Id, firstItem.Id, secondItem.Id }), "preempted background preparation should resume after current work");
preemptGates[2].SetResult(Success("resumed-background"));
Assert((await preemptedBackground).Outcome.Status == "success", "preemption must not turn background preparation into a user-visible cancellation");

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

static WorkerOutcome Success(string id, string kind = "formal", string validity = "valid")
{
    using var document = JsonDocument.Parse($"{{\"record\":{{\"record_id\":\"{id}\",\"analysis_fingerprint\":\"fingerprint-{id}\",\"record_kind\":\"{kind}\",\"is_preview\":{(kind == "preview" ? "true" : "false")},\"is_formal\":{(kind == "formal" ? "true" : "false")},\"measurement_semantics\":\"relative_intensity_code\",\"measurement_semantics_confirmed\":{(kind == "formal" ? "true" : "false")},\"flow_status\":\"computed\",\"measurement_validity\":\"{validity}\",\"quality_reason_codes\":[\"low_snr\"],\"metrics\":{{\"valid_metric\":{{\"value\":3.2,\"unit\":\"mm\",\"status\":\"valid\",\"reason_codes\":[]}},\"invalid_metric\":{{\"value\":999,\"unit\":\"mm\",\"status\":\"invalid\",\"reason_codes\":[\"low_snr\"]}}}}}}}}");
    return new WorkerOutcome("success", null, RecordId: id, Result: document.RootElement.Clone(), FlowStatus: "computed", RecordKind: kind);
}
