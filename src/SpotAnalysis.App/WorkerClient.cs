using System.Diagnostics;
using System.Text.Json;

namespace SpotAnalysis.App;

public sealed record WorkerOutcome(string Status, string? ErrorMessage);

public sealed class WorkerClient
{
    private const int ProtocolVersion = 1;

    public async Task<WorkerOutcome> RunSyntheticAsync(CancellationToken cancellationToken)
    {
        var workerPath = Path.Combine(AppContext.BaseDirectory, "worker.py");
        var startInfo = new ProcessStartInfo
        {
            FileName = "python",
            Arguments = $"\"{workerPath}\"",
            RedirectStandardInput = true,
            RedirectStandardOutput = true,
            RedirectStandardError = true,
            UseShellExecute = false,
            CreateNoWindow = true,
        };
        using var process = Process.Start(startInfo)
            ?? throw new InvalidOperationException("Unable to start the analysis worker.");
        var request = new
        {
            protocol_version = ProtocolVersion,
            request_id = Guid.NewGuid().ToString("N"),
            command = "analyze",
            input = new { kind = "synthetic", width = 16, height = 16 },
        };
        await process.StandardInput.WriteLineAsync(JsonSerializer.Serialize(request));
        await process.StandardInput.FlushAsync(cancellationToken);
        process.StandardInput.Close();

        string? terminalLine = null;
        while (await process.StandardOutput.ReadLineAsync(cancellationToken) is { } line)
        {
            using var document = JsonDocument.Parse(line);
            if (document.RootElement.GetProperty("type").GetString() == "terminal")
                terminalLine = line;
        }
        await process.WaitForExitAsync(cancellationToken);
        _ = await process.StandardError.ReadToEndAsync(cancellationToken);
        if (terminalLine is null)
            return new WorkerOutcome("failure", "worker did not return a terminal message");

        using var terminal = JsonDocument.Parse(terminalLine);
        var root = terminal.RootElement;
        var status = root.GetProperty("status").GetString() ?? "failure";
        var message = root.TryGetProperty("error", out var error)
            && error.TryGetProperty("message", out var detail)
            ? detail.GetString()
            : null;
        return new WorkerOutcome(status, message);
    }
}
