using System.Diagnostics;
using System.Text.Json;

namespace SpotAnalysis.App;

public sealed record WorkerOutcome(
    string Status,
    string? ErrorMessage,
    string? RecordId = null,
    string? AnalysisFingerprint = null,
    string? InputSummary = null,
    string? FailureCode = null);

public sealed class WorkerClient
{
    private const int ProtocolVersion = 1;

    public Task<WorkerOutcome> RunSyntheticAsync(CancellationToken cancellationToken, TimeSpan? timeout = null) =>
        RunAsync(new { kind = "synthetic", width = 16, height = 16 }, cancellationToken, timeout);

    public Task<WorkerOutcome> RunPngAsync(string path, string sha256, bool semanticsConfirmed,
        object spatialCalibration, object analysisRegion, object? backgroundRegion,
        CancellationToken cancellationToken, TimeSpan? timeout = null) =>
        RunAsync(new { kind = "png", path, sha256, intensity_semantics_confirmed = semanticsConfirmed, uri_hint = path,
            analysis_region = analysisRegion, background_region = backgroundRegion, spatial_calibration = spatialCalibration }, cancellationToken, timeout);

    private async Task<WorkerOutcome> RunAsync(object input, CancellationToken cancellationToken, TimeSpan? timeout)
    {
        using var timeoutSource = timeout.HasValue ? new CancellationTokenSource(timeout.Value) : null;
        using var stopSource = timeoutSource is null
            ? null
            : CancellationTokenSource.CreateLinkedTokenSource(cancellationToken, timeoutSource.Token);
        var stopToken = stopSource?.Token ?? cancellationToken;
        var workerPath = Path.Combine(AppContext.BaseDirectory, "worker.py");
        var startInfo = new ProcessStartInfo
        {
            FileName = "python", Arguments = $"\"{workerPath}\"", RedirectStandardInput = true,
            RedirectStandardOutput = true, RedirectStandardError = true, UseShellExecute = false, CreateNoWindow = true,
        };
        Process process;
        try
        {
            process = Process.Start(startInfo) ?? throw new InvalidOperationException("Unable to start the analysis worker.");
        }
        catch (Exception exception)
        {
            return new WorkerOutcome("failure", exception.Message, FailureCode: "worker_start_failed");
        }

        using (process)
        {
            try
            {
                var request = new { protocol_version = ProtocolVersion, request_id = Guid.NewGuid().ToString("N"), command = "analyze", input };
                await process.StandardInput.WriteLineAsync(JsonSerializer.Serialize(request));
                await process.StandardInput.FlushAsync(stopToken);
                process.StandardInput.Close();
                string? terminalLine = null;
                while (await process.StandardOutput.ReadLineAsync(stopToken) is { } line)
                {
                    using var document = JsonDocument.Parse(line);
                    if (document.RootElement.GetProperty("type").GetString() == "terminal") terminalLine = line;
                }
                await process.WaitForExitAsync(stopToken);
                _ = await process.StandardError.ReadToEndAsync(stopToken);
                if (terminalLine is null)
                    return new WorkerOutcome("failure", "worker did not return a terminal message", FailureCode: "worker_no_result");
                using var terminal = JsonDocument.Parse(terminalLine);
                var root = terminal.RootElement;
                var status = root.GetProperty("status").GetString() ?? "failure";
                if (status != "success")
                {
                    var error = root.TryGetProperty("error", out var e) && e.TryGetProperty("message", out var detail) ? detail.GetString() : "worker failure";
                    var code = e.ValueKind == JsonValueKind.Object && e.TryGetProperty("code", out var errorCode) ? errorCode.GetString() : null;
                    return new WorkerOutcome(status, error, FailureCode: code);
                }
                var result = root.GetProperty("result");
                var recordId = result.TryGetProperty("record_id", out var rid) ? rid.GetString() : null;
                var fingerprint = result.TryGetProperty("analysis_fingerprint", out var fp) ? fp.GetString() : null;
                var image = result.TryGetProperty("input", out var imageNode) ? imageNode : default;
                var summary = image.ValueKind == JsonValueKind.Object && image.TryGetProperty("sha256", out var hash)
                    ? $"{image.GetProperty("width")}×{image.GetProperty("height")}, {image.GetProperty("bit_depth")}-bit, SHA-256 {hash.GetString()}"
                    : null;
                return new WorkerOutcome(status, null, recordId, fingerprint, summary);
            }
            catch (OperationCanceledException) when (cancellationToken.IsCancellationRequested || timeoutSource?.IsCancellationRequested == true)
            {
                StopProcess(process);
                return cancellationToken.IsCancellationRequested
                    ? new WorkerOutcome("cancelled", "analysis cancelled", FailureCode: "worker_terminated_cancelled")
                    : new WorkerOutcome("timeout", "analysis timed out", FailureCode: "worker_terminated_timeout");
            }
            catch (JsonException exception)
            {
                StopProcess(process);
                return new WorkerOutcome("failure", exception.Message, FailureCode: "worker_protocol_invalid");
            }
            catch (Exception exception)
            {
                StopProcess(process);
                return new WorkerOutcome("failure", exception.Message, FailureCode: "worker_io_failed");
            }
        }
    }

    private static void StopProcess(Process process)
    {
        try
        {
            if (!process.HasExited) process.Kill(entireProcessTree: true);
            process.WaitForExit(500);
        }
        catch (InvalidOperationException) { }
        catch (System.ComponentModel.Win32Exception) { }
    }
}
