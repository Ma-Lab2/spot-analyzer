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
