using System.Diagnostics;
using System.Text.Json;

namespace SpotAnalysis.App;

public sealed record WorkerOutcome(string Status, string? ErrorMessage, string? RecordId = null, string? AnalysisFingerprint = null, string? InputSummary = null);

public sealed class WorkerClient
{
    private const int ProtocolVersion = 1;

    public Task<WorkerOutcome> RunSyntheticAsync(CancellationToken cancellationToken) =>
        RunAsync(new { kind = "synthetic", width = 16, height = 16 }, cancellationToken);

    public Task<WorkerOutcome> RunPngAsync(string path, string sha256, bool semanticsConfirmed, CancellationToken cancellationToken) =>
        RunAsync(new { kind = "png", path, sha256, intensity_semantics_confirmed = semanticsConfirmed, uri_hint = path }, cancellationToken);

    private async Task<WorkerOutcome> RunAsync(object input, CancellationToken cancellationToken)
    {
        var workerPath = Path.Combine(AppContext.BaseDirectory, "worker.py");
        var startInfo = new ProcessStartInfo
        {
            FileName = "python", Arguments = $"\"{workerPath}\"", RedirectStandardInput = true,
            RedirectStandardOutput = true, RedirectStandardError = true, UseShellExecute = false, CreateNoWindow = true,
        };
        using var process = Process.Start(startInfo) ?? throw new InvalidOperationException("Unable to start the analysis worker.");
        var request = new { protocol_version = ProtocolVersion, request_id = Guid.NewGuid().ToString("N"), command = "analyze", input };
        await process.StandardInput.WriteLineAsync(JsonSerializer.Serialize(request));
        await process.StandardInput.FlushAsync(cancellationToken);
        process.StandardInput.Close();
        string? terminalLine = null;
        while (await process.StandardOutput.ReadLineAsync(cancellationToken) is { } line)
        {
            using var document = JsonDocument.Parse(line);
            if (document.RootElement.GetProperty("type").GetString() == "terminal") terminalLine = line;
        }
        await process.WaitForExitAsync(cancellationToken);
        _ = await process.StandardError.ReadToEndAsync(cancellationToken);
        if (terminalLine is null) return new WorkerOutcome("failure", "worker did not return a terminal message");
        using var terminal = JsonDocument.Parse(terminalLine);
        var root = terminal.RootElement;
        var status = root.GetProperty("status").GetString() ?? "failure";
        if (status != "success")
        {
            var error = root.TryGetProperty("error", out var e) && e.TryGetProperty("message", out var detail) ? detail.GetString() : "worker failure";
            return new WorkerOutcome(status, error);
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
}
