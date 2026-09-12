using System.Diagnostics;
using System.IO;
using System.Text.Json;

namespace SpotAnalysis.App;

public sealed record WorkerOutcome(
    string Status,
    string? ErrorMessage,
    string? RecordId = null,
    string? AnalysisFingerprint = null,
    string? InputSummary = null,
    string? FailureCode = null,
    JsonElement? Result = null,
    IReadOnlyList<JsonElement>? Diagnostics = null);

public sealed class WorkerClient
{
    private const string RequestSchema = "analysis-request-v1";
    private static readonly JsonSerializerOptions JsonOptions = new()
    {
        PropertyNamingPolicy = JsonNamingPolicy.SnakeCaseLower,
    };

    public Task<WorkerOutcome> RunPngAsync(
        string path,
        string sha256,
        bool semanticsConfirmed,
        int regionX,
        int regionY,
        int regionWidth,
        int regionHeight,
        int? backgroundX,
        int? backgroundY,
        int? backgroundWidth,
        int? backgroundHeight,
        string calibrationStatus,
        double? calibrationX,
        double? calibrationY,
        string calibrationUnits,
        string calibrationSource,
        CancellationToken cancellationToken,
        TimeSpan? timeout = null)
    {
        var input = new Dictionary<string, object?>
        {
            ["asset"] = new Dictionary<string, object?>
            {
                ["path"] = path,
                ["expected_sha256"] = sha256,
            },
            ["confirm_relative_intensity"] = semanticsConfirmed,
        };
        var configuration = CreateConfiguration(
            regionX, regionY, regionWidth, regionHeight,
            backgroundX, backgroundY, backgroundWidth, backgroundHeight,
            calibrationStatus, calibrationX, calibrationY, calibrationUnits, calibrationSource);
        return RunAsync(input, configuration, cancellationToken, timeout);
    }

    public Task<WorkerOutcome> RunAsync(
        AnalysisRequest request,
        CancellationToken cancellationToken,
        TimeSpan? timeout = null) =>
        RunAsync(request.ToWorkerPayload(), cancellationToken, timeout);

    private static Dictionary<string, object?> CreateConfiguration(
        int regionX,
        int regionY,
        int regionWidth,
        int regionHeight,
        int? backgroundX,
        int? backgroundY,
        int? backgroundWidth,
        int? backgroundHeight,
        string calibrationStatus,
        double? calibrationX,
        double? calibrationY,
        string calibrationUnits,
        string calibrationSource)
    {
        Dictionary<string, object?>? background = null;
        if (backgroundX.HasValue && backgroundY.HasValue && backgroundWidth.HasValue && backgroundHeight.HasValue)
        {
            background = new Dictionary<string, object?>
            {
                ["x"] = backgroundX.Value,
                ["y"] = backgroundY.Value,
                ["width"] = backgroundWidth.Value,
                ["height"] = backgroundHeight.Value,
            };
        }

        return new Dictionary<string, object?>
        {
            ["region"] = new Dictionary<string, object?>
            {
                ["x"] = regionX, ["y"] = regionY, ["width"] = regionWidth, ["height"] = regionHeight,
            },
            ["background_region"] = background,
            ["calibration"] = new Dictionary<string, object?>
            {
                ["x_unit_per_pixel"] = calibrationX,
                ["y_unit_per_pixel"] = calibrationY,
                ["physical_unit"] = calibrationUnits,
                ["source"] = calibrationSource,
                ["confirmation"] = calibrationStatus,
            },
            ["preprocessing"] = new Dictionary<string, object?>
            {
                ["background_source"] = "confirmed_region_affine",
                ["bad_pixel_policy"] = "mask_only",
                ["negative_value_policy"] = "preserve_signed",
                ["filtering"] = "none",
                ["dpc"] = "none",
                ["advanced_processing_enabled"] = false,
                ["background_signal_sigma_threshold"] = 3.0,
                ["background_signal_peak_fraction"] = 0.10,
                ["background_mask_dilation_pixels"] = 1,
                ["background_huber_delta"] = 1.345,
                ["background_max_iterations"] = 50,
                ["convergence_tolerance"] = 1e-8,
                ["localization_sigma_pixels"] = 1.0,
                ["localization_truncate_sigma"] = 3.0,
                ["core_threshold_fraction"] = 0.5,
                ["core_invalid_fraction"] = 0.8,
                ["core_caution_fraction"] = 0.95,
                ["snr_invalid_threshold"] = 5.0,
                ["snr_caution_threshold"] = 10.0,
                ["multiple_peak_relative_threshold"] = 0.20,
                ["multiple_peak_noise_threshold"] = 5.0,
                ["multiple_peak_min_support_pixels"] = 9,
                ["multiple_peak_min_separation_pixels"] = 3.0,
                ["advanced_interpolation_sigma_pixels"] = 1.0,
                ["advanced_interpolation_radius_pixels"] = 2,
                ["advanced_filter_sigma_pixels"] = 1.0,
                ["advanced_filter_radius_pixels"] = 3,
                ["advanced_dpc_sigma_pixels"] = 2.0,
                ["advanced_dpc_radius_pixels"] = 6,
                ["version"] = "preprocessing-v1",
            },
            ["model"] = new Dictionary<string, object?>
            {
                ["name"] = "rotated_elliptical_gaussian",
                ["sigma_min_pixels"] = 0.5,
                ["sigma_max_roi_fraction"] = 0.5,
                ["fallback_sigma_roi_fraction"] = 1.0 / 6.0,
                ["optimizer"] = "bounded-least-squares-trf",
                ["optimizer_tolerance"] = 1e-12,
                ["optimizer_max_evaluations"] = 1000,
                ["version"] = "gaussian-model-v1",
            },
            ["rref_pixels"] = null,
            ["analysis_contract"] = "analysis-contract-v1",
            ["standard_profile"] = "standard-profile-v1",
            ["quality_profile"] = "quality-profile-v1",
            ["profile_validation"] = "provisional",
            ["algorithm_version"] = "analysis-core-v1",
            ["bad_pixel_coordinates"] = Array.Empty<object>(),
            ["bad_pixel_mask_version"] = "bad-pixel-mask-v1",
        };
    }

    private static Task<WorkerOutcome> RunAsync(
        Dictionary<string, object?> input,
        Dictionary<string, object?> configuration,
        CancellationToken cancellationToken,
        TimeSpan? timeout) =>
        RunAsync(
            new Dictionary<string, object?>
            {
                ["input"] = input,
                ["configuration"] = configuration,
                ["output_strategy"] = new Dictionary<string, object?>
                {
                    ["work_directory"] = Path.Combine(Path.GetTempPath(), "SpotAnalysis", "derived"),
                    ["derived_format"] = "npy",
                },
            },
            cancellationToken,
            timeout);

    private static async Task<WorkerOutcome> RunAsync(
        Dictionary<string, object?> payload,
        CancellationToken cancellationToken,
        TimeSpan? timeout)
    {
        using var timeoutSource = timeout.HasValue ? new CancellationTokenSource(timeout.Value) : null;
        using var stopSource = timeoutSource is null
            ? null
            : CancellationTokenSource.CreateLinkedTokenSource(cancellationToken, timeoutSource.Token);
        var stopToken = stopSource?.Token ?? cancellationToken;
        string workerPath;
        try
        {
            workerPath = ResolveWorkerPath();
        }
        catch (Exception exception)
        {
            return new WorkerOutcome("failure", exception.Message, FailureCode: "worker_start_failed");
        }
        var startInfo = new ProcessStartInfo
        {
            FileName = workerPath,
            WorkingDirectory = AppContext.BaseDirectory,
            RedirectStandardInput = true,
            RedirectStandardOutput = true,
            RedirectStandardError = true,
            UseShellExecute = false,
            CreateNoWindow = true,
        };
        Process process;
        try
        {
            process = Process.Start(startInfo) ?? throw new InvalidOperationException("Unable to start the packaged analysis worker.");
        }
        catch (Exception exception)
        {
            return new WorkerOutcome("failure", exception.Message, FailureCode: "worker_start_failed");
        }

        using (process)
        {
            try
            {
                var request = new Dictionary<string, object?>
                {
                    ["schema"] = RequestSchema,
                    ["request_id"] = Guid.NewGuid().ToString("N"),
                    ["input"] = payload["input"],
                    ["configuration"] = payload["configuration"],
                    ["output_strategy"] = payload["output_strategy"],
                    ["lifecycle"] = new Dictionary<string, object?>
                    {
                        ["timeout_ms"] = timeout?.TotalMilliseconds,
                    },
                };
                await process.StandardInput.WriteLineAsync(JsonSerializer.Serialize(request, JsonOptions));
                await process.StandardInput.FlushAsync(stopToken);
                process.StandardInput.Close();
                var messages = new List<JsonElement>();
                while (await process.StandardOutput.ReadLineAsync(stopToken) is { } line)
                {
                    using var document = JsonDocument.Parse(line);
                    messages.Add(document.RootElement.Clone());
                }
                await process.WaitForExitAsync(stopToken);
                var stderr = await process.StandardError.ReadToEndAsync(stopToken);
                if (process.ExitCode != 0)
                    return new WorkerOutcome("failure", stderr.Trim(), FailureCode: "worker_crashed");
                return ParseOutcome(messages);
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

    private static string ResolveWorkerPath()
    {
        var configured = Environment.GetEnvironmentVariable("SPOT_ANALYSIS_WORKER");
        if (!string.IsNullOrWhiteSpace(configured) && File.Exists(configured)) return configured;
        foreach (var name in new[] { "SpotAnalysis.Worker.exe", "worker.exe" })
        {
            var path = Path.Combine(AppContext.BaseDirectory, name);
            if (File.Exists(path)) return path;
        }
        throw new FileNotFoundException("The packaged worker executable was not found beside the client.");
    }

    private static WorkerOutcome ParseOutcome(IReadOnlyList<JsonElement> messages)
    {
        if (messages.Count == 0)
            return new WorkerOutcome("failure", "worker did not return an analysis event", FailureCode: "worker_no_result");

        var terminal = messages[^1];
        if (terminal.TryGetProperty("kind", out var kind) && kind.GetString() == "completed")
        {
            var record = terminal.TryGetProperty("record", out var recordNode) ? recordNode : default;
            var recordId = record.ValueKind == JsonValueKind.Object && record.TryGetProperty("record_id", out var id) ? id.GetString() : null;
            var fingerprint = record.ValueKind == JsonValueKind.Object && record.TryGetProperty("analysis_fingerprint", out var hash) ? hash.GetString() : null;
            var summary = record.ValueKind == JsonValueKind.Object && record.TryGetProperty("input", out var image)
                && image.ValueKind == JsonValueKind.Object
                ? $"{GetShapeDimension(record, 1)}×{GetShapeDimension(record, 0)}, {GetInt(image, "bit_depth")}-bit"
                : null;
            return new WorkerOutcome("success", null, recordId, fingerprint, summary, Result: terminal.Clone());
        }

        var diagnostics = ReadFailureDiagnostics(terminal);
        var failureCode = ReadFailureCode(terminal, diagnostics);
        var flowStatus = terminal.TryGetProperty("flow_status", out var flow) ? flow.GetString() : null;
        var status = flowStatus switch
        {
            "cancelled" => "cancelled",
            "timeout" => "timeout",
            _ => "failure",
        };
        var errorMessage = ReadFailureMessage(diagnostics)
            ?? ReadFailureCodeFromTerminal(terminal)
            ?? "worker analysis failed";
        return new WorkerOutcome(status, errorMessage, FailureCode: failureCode, Result: terminal.Clone(), Diagnostics: diagnostics);
    }

    private static IReadOnlyList<JsonElement> ReadFailureDiagnostics(JsonElement terminal)
    {
        if (!terminal.TryGetProperty("diagnostics", out var diagnosticsNode) || diagnosticsNode.ValueKind == JsonValueKind.Null)
            return Array.Empty<JsonElement>();

        return diagnosticsNode.ValueKind switch
        {
            JsonValueKind.Array => diagnosticsNode.EnumerateArray().Select(item => item.Clone()).ToArray(),
            JsonValueKind.Object => new[] { diagnosticsNode.Clone() },
            _ => Array.Empty<JsonElement>(),
        };
    }

    private static string ReadFailureCode(JsonElement terminal, IReadOnlyList<JsonElement> diagnostics)
    {
        foreach (var diagnostic in diagnostics)
        {
            if (ReadString(diagnostic, "code") is { } code && !string.IsNullOrWhiteSpace(code))
                return code;
        }

        return ReadFailureCodeFromTerminal(terminal) ?? "worker_failure";
    }

    private static string? ReadFailureCodeFromTerminal(JsonElement terminal) => terminal.TryGetProperty("code", out var code)
        && code.ValueKind == JsonValueKind.String ? code.GetString() : null;

    private static string? ReadFailureMessage(IReadOnlyList<JsonElement> diagnostics)
    {
        foreach (var diagnostic in diagnostics)
        {
            if (ReadString(diagnostic, "message") is { } message && !string.IsNullOrWhiteSpace(message))
                return message;
        }

        return null;
    }

    private static string? ReadString(JsonElement node, string property) => node.ValueKind == JsonValueKind.Object
        && node.TryGetProperty(property, out var value)
        && value.ValueKind == JsonValueKind.String
        ? value.GetString()
        : null;

    private static int GetInt(JsonElement value, string property) => value.TryGetProperty(property, out var node) && node.TryGetInt32(out var result) ? result : 0;

    private static int GetShapeDimension(JsonElement record, int index)
    {
        if (!record.TryGetProperty("input_shape", out var shape) || shape.ValueKind != JsonValueKind.Array)
            return 0;
        var dimensions = shape.EnumerateArray().ToArray();
        return index < dimensions.Length && dimensions[index].TryGetInt32(out var result) ? result : 0;
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
