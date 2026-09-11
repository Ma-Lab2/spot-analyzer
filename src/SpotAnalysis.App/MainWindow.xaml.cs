using System.Globalization;
using System.Text;
using System.Text.Json;
using System.Windows;
using Microsoft.Win32;

namespace SpotAnalysis.App;

public partial class MainWindow : Window
{
    private readonly WorkerClient _workerClient = new();
    private PngInputInfo? _selectedInput;
    private bool _configurationConfirmed;
    private bool _hasResult;
    private WorkerOutcome? _lastOutcome;
    private WorkerOutcome? _lastSuccessfulOutcome;
    private CancellationTokenSource? _analysisCancellation;
    private AnalysisRequest? _pendingRequest;
    private AnalysisRequest? _inFlightRequest;
    private ConfigurationValues? _lastConfiguration;
    private string _flowStatus = "ready";
    private string? _failureCode;
    private string? _failureDetails;

    public MainWindow()
    {
        InitializeComponent();
        DiagnosticsText.Text = DiagnosticPackage.BuildAboutText();
        DiagnosticLog.Write("client_started", new { output_capability = DiagnosticPackage.OutputCapability });
    }

    private sealed record ConfigurationValues(
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

    private ConfigurationValues ReadConfiguration()
    {
        var status = (CalibrationStatus.SelectedItem as ComboBoxItem)?.Content?.ToString() ?? "missing";
        var region = Rectangle(
            RoiXText, RoiYText, RoiWidthText, RoiHeightText, "analysis region");
        if (_selectedInput is not null && (region.x + region.width > _selectedInput.Width || region.y + region.height > _selectedInput.Height))
            throw new ConfigurationValidationException("invalid_roi", "Analysis region must be inside the selected input image.");

        var backgroundFields = new[] { BackgroundXText, BackgroundYText, BackgroundWidthText, BackgroundHeightText };
        var backgroundEmpty = backgroundFields.All(field => string.IsNullOrWhiteSpace(field.Text));
        var backgroundPartial = backgroundFields.Any(field => string.IsNullOrWhiteSpace(field.Text));
        (int x, int y, int width, int height)? background = null;
        if (!backgroundEmpty)
        {
            if (backgroundPartial)
                throw new ConfigurationValidationException("invalid_background_region", "Background region must be empty or have all four values.");
            background = Rectangle(BackgroundXText, BackgroundYText, BackgroundWidthText, BackgroundHeightText, "background region");
            if (_selectedInput is not null && (background.Value.x + background.Value.width > _selectedInput.Width || background.Value.y + background.Value.height > _selectedInput.Height))
                throw new ConfigurationValidationException("invalid_background_region", "Background region must be inside the selected input image.");
        }

        double? calibrationX = null;
        double? calibrationY = null;
        if (status != "missing")
        {
            calibrationX = PositiveNumber(CalibrationXText, "x calibration");
            calibrationY = PositiveNumber(CalibrationYText, "y calibration");
            if (string.IsNullOrWhiteSpace(CalibrationUnitsText.Text))
                throw new ConfigurationValidationException("invalid_calibration", "Calibration units are required.");
            if (string.IsNullOrWhiteSpace(CalibrationSourceText.Text))
                throw new ConfigurationValidationException("invalid_calibration", "Calibration source is required.");
        }

        return new ConfigurationValues(
            region.x, region.y, region.width, region.height,
            background?.x, background?.y, background?.width, background?.height,
            status, calibrationX, calibrationY,
            CalibrationUnitsText.Text.Trim(), CalibrationSourceText.Text.Trim());
    }

    private static (int x, int y, int width, int height) Rectangle(
        System.Windows.Controls.TextBox xField,
        System.Windows.Controls.TextBox yField,
        System.Windows.Controls.TextBox widthField,
        System.Windows.Controls.TextBox heightField,
        string name)
    {
        var x = Integer(xField, $"{name} x");
        var y = Integer(yField, $"{name} y");
        var width = Integer(widthField, $"{name} width");
        var height = Integer(heightField, $"{name} height");
        if (x < 0 || y < 0 || width <= 0 || height <= 0)
            throw new ConfigurationValidationException("invalid_roi", $"{name} must have a non-negative origin and positive dimensions.");
        return (x, y, width, height);
    }

    private static int Integer(System.Windows.Controls.TextBox field, string name) =>
        int.TryParse(field.Text, NumberStyles.Integer, CultureInfo.InvariantCulture, out var value)
            ? value
            : throw new ConfigurationValidationException("invalid_configuration", $"{name} must be an integer.");

    private static double PositiveNumber(System.Windows.Controls.TextBox field, string name) =>
        double.TryParse(field.Text, NumberStyles.Float, CultureInfo.InvariantCulture, out var value) && double.IsFinite(value) && value > 0
            ? value
            : throw new ConfigurationValidationException("invalid_calibration", $"{name} must be a positive number.");

    private AnalysisRequest BuildRequest(ConfigurationValues configuration)
    {
        if (_selectedInput is null)
            throw new ConfigurationValidationException("input_required", "Open an 8-bit or 16-bit grayscale PNG before confirming configuration.");
        if (configuration.CalibrationStatus != "confirmed")
            throw new ConfigurationValidationException("calibration_unconfirmed", "Calibration must be confirmed before analysis can start.");

        return AnalysisRequest.Create(
            _selectedInput.Path,
            _selectedInput.Sha256,
            configuration.RegionX,
            configuration.RegionY,
            configuration.RegionWidth,
            configuration.RegionHeight,
            configuration.BackgroundX,
            configuration.BackgroundY,
            configuration.BackgroundWidth,
            configuration.BackgroundHeight,
            configuration.CalibrationStatus,
            configuration.CalibrationX,
            configuration.CalibrationY,
            configuration.CalibrationUnits,
            configuration.CalibrationSource,
            Path.Combine(Path.GetTempPath(), "SpotAnalysis", "derived"));
    }

    private void ConfirmConfiguration_Click(object sender, RoutedEventArgs e)
    {
        try
        {
            _lastConfiguration = ReadConfiguration();
            _pendingRequest = BuildRequest(_lastConfiguration);
            _configurationConfirmed = true;
            SummaryText.Text = _pendingRequest.Summary;
            _flowStatus = "configuration_confirmed";
            _failureCode = null;
            _failureDetails = null;
            DiagnosticLog.Write("configuration_confirmed", new { configuration = BuildConfigurationSnapshot(_lastConfiguration), request = _pendingRequest.Summary });
            StatusText.Text = "Configuration confirmed; ready to run";
            MarkResultStale();
            UpdateRunAvailability();
        }
        catch (ConfigurationValidationException exception)
        {
            _configurationConfirmed = false;
            _pendingRequest = null;
            UpdateRunAvailability();
            StatusText.Text = $"Invalid configuration ({exception.Code}): {exception.Message}";
        }
    }

    private async void RunAnalysis_Click(object sender, RoutedEventArgs e)
    {
        if (!_configurationConfirmed || _pendingRequest is null)
        {
            StatusText.Text = "Confirm a valid pending configuration before running analysis.";
            return;
        }

        var request = _pendingRequest;
        _inFlightRequest = request;
        UpdateRunAvailability();
        await RunAnalysisAsync(() => _workerClient.RunAsync(
            request,
            _analysisCancellation!.Token,
            TimeSpan.FromSeconds(30)));
        _inFlightRequest = null;
        RefreshDraftSummary();
        UpdateRunAvailability();
    }

    private async void CancelAnalysis_Click(object sender, RoutedEventArgs e)
    {
        _analysisCancellation?.Cancel();
        _flowStatus = "cancelling";
        DiagnosticLog.Write("analysis_cancellation_requested", new { flow_status = _flowStatus });
        StatusText.Text = "Cancelling";
        await Task.CompletedTask;
    }

    private async Task RunAnalysisAsync(Func<Task<WorkerOutcome>> operation)
    {
        _analysisCancellation?.Dispose();
        _analysisCancellation = new CancellationTokenSource();
        _lastOutcome = null;
        _flowStatus = "processing";
        _failureCode = null;
        _failureDetails = null;
        DiagnosticLog.Write("analysis_started", new { flow_status = _flowStatus, input = _selectedInput?.Summary });
        StatusText.Text = "Processing";
        try
        {
            var outcome = await operation();
            _lastOutcome = outcome;
            _failureCode = outcome.FailureCode;
            _failureDetails = outcome.ErrorMessage;
            _flowStatus = outcome.Status switch
            {
                "success" => "completed",
                "cancelled" => "cancelled",
                "timeout" => "timeout",
                _ => "failed",
            };
            DiagnosticLog.Write("analysis_finished", new
            {
                flow_status = _flowStatus,
                outcome.FailureCode,
                outcome.ErrorMessage,
                outcome.RecordId,
                outcome.AnalysisFingerprint,
            });
            switch (outcome.Status)
            {
                case "success":
                    StatusText.Text = "Completed";
                    _lastOutcome = outcome;
                    _hasResult = true;
                    RecordText.Text = FormatRecordSummary(outcome);
                    MetricsText.Text = FormatMetrics(outcome);
                    ExportResultButton.IsEnabled = true;
                    break;
                case "cancelled":
                    StatusText.Text = "Cancelled";
                    RecordText.Text = $"Cancelled ({outcome.FailureCode}): {outcome.ErrorMessage}";
                    break;
                case "timeout":
                    StatusText.Text = "Timed out";
                    RecordText.Text = $"Timed out ({outcome.FailureCode}): {outcome.ErrorMessage}";
                    break;
                default:
                    StatusText.Text = $"Failed ({outcome.FailureCode ?? "worker_failure"}): {outcome.ErrorMessage}";
                    RecordText.Text = StatusText.Text;
                    MetricsText.Text = FormatDiagnostics(outcome);
                    ExportResultButton.IsEnabled = false;
                    break;
            }
        }
        catch (ConfigurationValidationException exception)
        {
            _flowStatus = "configuration_invalid";
            _failureCode = exception.Code;
            _failureDetails = exception.Message;
            DiagnosticLog.Write("analysis_rejected", new { flow_status = _flowStatus, code = exception.Code, message = exception.Message });
            StatusText.Text = $"Invalid configuration ({exception.Code}): {exception.Message}";
            RecordText.Text = StatusText.Text;
        }
        catch (InputValidationException exception)
        {
            _flowStatus = "input_invalid";
            _failureCode = exception.Code;
            _failureDetails = exception.Message;
            DiagnosticLog.Write("analysis_rejected", new { flow_status = _flowStatus, code = exception.Code, message = exception.Message });
            StatusText.Text = $"Invalid input ({exception.Code}): {exception.Message}";
            RecordText.Text = StatusText.Text;
        }
        catch (OperationCanceledException)
        {
            _flowStatus = "cancelled";
            _failureCode = "analysis_cancelled";
            _failureDetails = "analysis cancelled";
            DiagnosticLog.Write("analysis_cancelled", new { flow_status = _flowStatus });
            StatusText.Text = "Cancelled";
        }
        finally
        {
            _analysisCancellation?.Dispose();
            _analysisCancellation = null;
        }
    }

    private async void OpenPng_Click(object sender, RoutedEventArgs e)
    {
        var dialog = new OpenFileDialog
        {
            Filter = "PNG files (*.png)|*.png",
            CheckFileExists = true,
            Multiselect = false,
        };
        if (dialog.ShowDialog() != true) return;
        try
        {
            var input = await PngInput.ReadAsync(dialog.FileName);
            _selectedInput = input;
            InputSummaryText.Text = input.Summary;
            InputPreview.Source = input.Preview;
            _configurationConfirmed = false;
            _flowStatus = "input_loaded";
            _failureCode = null;
            _failureDetails = null;
            DiagnosticLog.Write("input_loaded", new { input.Summary, input.Sha256 });
            StatusText.Text = "Input loaded; confirm configuration";
            MarkResultStale();
            RefreshDraftSummary();
            UpdateRunAvailability();
        }
        catch (InputValidationException exception)
        {
            _selectedInput = null;
            _pendingRequest = null;
            _configurationConfirmed = false;
            InputPreview.Source = null;
            InputSummaryText.Text = "No input selected";
            SummaryText.Text = "No input selected; pending request is not valid.";
            UpdateRunAvailability();
            _flowStatus = "input_invalid";
            _failureCode = exception.Code;
            _failureDetails = exception.Message;
            DiagnosticLog.Write("input_rejected", new { flow_status = _flowStatus, code = exception.Code, message = exception.Message });
            StatusText.Text = $"Invalid input ({exception.Code}): {exception.Message}";
        }
    }

    private void ExportDiagnostics_Click(object sender, RoutedEventArgs e)
    {
        var includeImage = IncludeOriginalImageCheck.IsChecked == true;
        var dialog = new SaveFileDialog
        {
            Filter = includeImage
                ? "Diagnostic ZIP package (*.zip)|*.zip"
                : "Diagnostic JSON package (*.json)|*.json",
            DefaultExt = includeImage ? ".zip" : ".json",
            AddExtension = true,
            FileName = $"spot-analysis-diagnostics-{DateTime.Now:yyyyMMdd-HHmmss}",
            OverwritePrompt = true,
        };
        if (dialog.ShowDialog() != true) return;
        try
        {
            DiagnosticPackage.Write(dialog.FileName, BuildDiagnosticSnapshot(), includeImage);
            DiagnosticLog.Write("diagnostic_exported", new
            {
                path = dialog.FileName,
                include_original_image = includeImage,
                flow_status = _flowStatus,
            });
            StatusText.Text = $"Diagnostics exported: {dialog.FileName}";
        }
        catch (Exception exception)
        {
            DiagnosticLog.Write("diagnostic_export_failed", new { code = "diagnostic_export_failed", message = exception.Message });
            StatusText.Text = $"Diagnostic export failed: {exception.Message}";
        }
    }

    private DiagnosticSnapshot BuildDiagnosticSnapshot()
    {
        var outcome = _lastOutcome;
        JsonElement? record = null;
        if (outcome?.Result is JsonElement result && result.TryGetProperty("record", out var recordNode))
            record = recordNode;
        var diagnostics = ReadArray(record, "diagnostics");
        if (diagnostics.Count == 0 && outcome?.Diagnostics is not null)
            diagnostics = outcome.Diagnostics;
        var reasons = ReadStringArray(record, "quality_reason_codes");
        if (reasons.Count == 0)
            reasons = ReadStringArray(record, "reason_codes");
        var validity = ReadString(record, "measurement_validity") ?? ReadString(record, "summary_status");
        return new DiagnosticSnapshot(
            _flowStatus,
            _failureCode,
            _failureDetails,
            outcome?.RecordId ?? ReadString(record, "record_id"),
            outcome?.AnalysisFingerprint ?? ReadString(record, "analysis_fingerprint"),
            _selectedInput?.Summary ?? outcome?.InputSummary,
            _selectedInput?.Sha256,
            _selectedInput?.Path,
            BuildConfigurationSnapshot(_lastConfiguration),
            record,
            diagnostics,
            validity,
            reasons);
    }

    private static object BuildConfigurationSnapshot(ConfigurationValues? configuration) => configuration is null
        ? new Dictionary<string, object?> { ["status"] = "not_confirmed" }
        : new Dictionary<string, object?>
        {
            ["analysis_region"] = new
            {
                x = configuration.RegionX,
                y = configuration.RegionY,
                width = configuration.RegionWidth,
                height = configuration.RegionHeight,
            },
            ["background_region"] = configuration.BackgroundX.HasValue
                ? new
                {
                    x = configuration.BackgroundX,
                    y = configuration.BackgroundY,
                    width = configuration.BackgroundWidth,
                    height = configuration.BackgroundHeight,
                }
                : null,
            ["spatial_calibration"] = new
            {
                x_unit_per_pixel = configuration.CalibrationX,
                y_unit_per_pixel = configuration.CalibrationY,
                physical_unit = configuration.CalibrationUnits,
                source = configuration.CalibrationSource,
                confirmation = configuration.CalibrationStatus,
            },
        };

    private static string? ReadString(JsonElement? node, string property) =>
        node is { } value && value.ValueKind == JsonValueKind.Object &&
        value.TryGetProperty(property, out var child) && child.ValueKind == JsonValueKind.String
            ? child.GetString()
            : null;

    private static IReadOnlyList<JsonElement> ReadArray(JsonElement? node, string property) =>
        node is { } value && value.ValueKind == JsonValueKind.Object &&
        value.TryGetProperty(property, out var child) && child.ValueKind == JsonValueKind.Array
            ? child.EnumerateArray().Select(item => item.Clone()).ToArray()
            : Array.Empty<JsonElement>();

    private static IReadOnlyList<string> ReadStringArray(JsonElement? node, string property) =>
        node is { } value && value.ValueKind == JsonValueKind.Object &&
        value.TryGetProperty(property, out var child) && child.ValueKind == JsonValueKind.Array
            ? child.EnumerateArray().Where(item => item.ValueKind == JsonValueKind.String)
                .Select(item => item.GetString()!).ToArray()
            : Array.Empty<string>();

    private void RefreshDraftSummary()
    {
        if (_inFlightRequest is not null)
            return;
        try
        {
            var draft = ReadConfiguration();
            _pendingRequest = _selectedInput is null ? null : BuildRequestForDisplay(draft);
            SummaryText.Text = _pendingRequest?.Summary ?? "No input selected; pending request is not valid.";
        }
        catch (ConfigurationValidationException exception)
        {
            _pendingRequest = null;
            SummaryText.Text = $"Pending configuration is invalid ({exception.Code}): {exception.Message}";
        }
        UpdateRunAvailability();
    }

    private AnalysisRequest BuildRequestForDisplay(ConfigurationValues configuration)
    {
        if (_selectedInput is null)
            throw new ConfigurationValidationException("input_required", "Open an input before building a request snapshot.");
        return AnalysisRequest.Create(
            _selectedInput.Path,
            _selectedInput.Sha256,
            configuration.RegionX,
            configuration.RegionY,
            configuration.RegionWidth,
            configuration.RegionHeight,
            configuration.BackgroundX,
            configuration.BackgroundY,
            configuration.BackgroundWidth,
            configuration.BackgroundHeight,
            configuration.CalibrationStatus,
            configuration.CalibrationX,
            configuration.CalibrationY,
            configuration.CalibrationUnits,
            configuration.CalibrationSource,
            Path.Combine(Path.GetTempPath(), "SpotAnalysis", "derived"));
    }

    private void UpdateRunAvailability() =>
        RunAnalysisButton.IsEnabled = _inFlightRequest is null
            && _configurationConfirmed
            && _pendingRequest is not null
            && _pendingRequest.IsValid
            && _selectedInput is not null;

    private void ConfigurationChanged(object sender, RoutedEventArgs e)
    {
        _configurationConfirmed = false;
        _pendingRequest = null;
        _flowStatus = _hasResult ? "needs_recalculation" : "configuration_changed";
        MarkResultStale();
        if (_inFlightRequest is null)
            RefreshDraftSummary();
        UpdateRunAvailability();
    }

    private string FormatRecordSummary(WorkerOutcome outcome)
    {
        if (outcome.Result is not JsonElement terminal || !terminal.TryGetProperty("record", out var record)
            || record.ValueKind != JsonValueKind.Object)
            return $"Input: {_selectedInput?.Summary ?? outcome.InputSummary}\nRecord: {outcome.RecordId}\nFingerprint: {outcome.AnalysisFingerprint}";

        var input = record.TryGetProperty("input", out var inputNode) ? inputNode : default;
        var inputIdentity = input.ValueKind == JsonValueKind.Object
            ? $"{GetString(input, "asset_id") ?? "unknown"}, SHA-256 {GetString(input, "sha256") ?? "unknown"}"
            : _selectedInput?.Summary ?? outcome.InputSummary ?? "unknown";
        var flowStatus = GetString(record, "flow_status") ?? "unknown";
        var summaryStatus = GetString(record, "summary_status") ?? "unknown";
        var configuration = record.TryGetProperty("configuration", out var configurationNode) ? configurationNode : default;
        var region = configuration.ValueKind == JsonValueKind.Object && configuration.TryGetProperty("region", out var regionNode)
            ? $"({GetNumber(regionNode, "x")}, {GetNumber(regionNode, "y")}, {GetNumber(regionNode, "width")}, {GetNumber(regionNode, "height")})"
            : "unknown";
        var calibration = configuration.ValueKind == JsonValueKind.Object && configuration.TryGetProperty("calibration", out var calibrationNode)
            ? $"{GetNumber(calibrationNode, "x_unit_per_pixel")} × {GetNumber(calibrationNode, "y_unit_per_pixel")} {GetString(calibrationNode, "physical_unit") ?? ""} ({GetString(calibrationNode, "confirmation") ?? "missing"})"
            : "unknown";
        return $"Input: {inputIdentity}\nRecord: {GetString(record, "record_id") ?? outcome.RecordId}\nFingerprint: {GetString(record, "analysis_fingerprint") ?? outcome.AnalysisFingerprint}\nFlow status: {flowStatus}; measurement validity: {summaryStatus}\nCalibration: {calibration}\nAnalysis region: {region}";
    }

    private string FormatMetrics(WorkerOutcome outcome)
    {
        if (outcome.Result is not JsonElement terminal || !terminal.TryGetProperty("record", out var record)
            || !record.TryGetProperty("metrics", out var metrics) || metrics.ValueKind != JsonValueKind.Object)
            return FormatDiagnostics(outcome);

        var lines = new StringBuilder();
        foreach (var metric in metrics.EnumerateObject())
        {
            if (metric.Value.TryGetProperty("domains", out var domains) && domains.ValueKind == JsonValueKind.Object)
            {
                foreach (var domain in domains.EnumerateObject())
                    AppendMetric(lines, metric.Name, domain.Name, domain.Value);
            }
            else
            {
                AppendMetric(lines, metric.Name, "", metric.Value);
            }
        }
        return lines.Length == 0 ? "No metrics returned" : lines.ToString().TrimEnd();
    }

    private static void AppendMetric(StringBuilder lines, string name, string domain, JsonElement metric)
    {
        var value = metric.TryGetProperty("value", out var valueNode) && valueNode.ValueKind != JsonValueKind.Null
            ? valueNode.ToString()
            : "N/A";
        var unit = GetString(metric, "unit") ?? "";
        var status = GetString(metric, "status") ?? "unknown";
        var reasons = metric.TryGetProperty("reason_codes", out var reasonNode) && reasonNode.ValueKind == JsonValueKind.Array
            ? string.Join(", ", reasonNode.EnumerateArray().Select(item => item.ToString()))
            : "none";
        var label = string.IsNullOrEmpty(domain) ? name : $"{name} ({domain})";
        lines.AppendLine($"{label}: {value} {unit}; validity={status}; reasons={reasons}");
    }

    private static string FormatDiagnostics(WorkerOutcome outcome)
    {
        if (outcome.Diagnostics is null || outcome.Diagnostics.Count == 0)
            return "No metrics returned";
        return string.Join(Environment.NewLine, outcome.Diagnostics.Select(item =>
            $"{GetString(item, "code") ?? "diagnostic"}: {GetString(item, "message") ?? item.ToString()}"));
    }

    private static string? GetString(JsonElement node, string property) =>
        node.ValueKind == JsonValueKind.Object && node.TryGetProperty(property, out var value)
            && value.ValueKind != JsonValueKind.Null ? value.ToString() : null;

    private static string GetNumber(JsonElement node, string property) => GetString(node, property) ?? "?";

    private async void ExportResult_Click(object sender, RoutedEventArgs e)
    {
        if (!_hasResult || _lastOutcome?.Result is not JsonElement result)
        {
            StatusText.Text = "No current result is available to export.";
            return;
        }

        var dialog = new SaveFileDialog
        {
            Filter = "JSON report (*.json)|*.json",
            DefaultExt = ".json",
            AddExtension = true,
            OverwritePrompt = false,
            FileName = $"spot-analysis-{_lastOutcome.RecordId ?? "result"}.json",
        };
        if (dialog.ShowDialog() != true) return;
        try
        {
            var target = ReserveReportPath(dialog.FileName);
            var options = new JsonSerializerOptions { WriteIndented = true };
            var json = JsonSerializer.Serialize(result, options);
            var temporary = target + ".tmp-" + Guid.NewGuid().ToString("N");
            await File.WriteAllTextAsync(temporary, json, Encoding.UTF8);
            File.Move(temporary, target);
            StatusText.Text = $"Result exported: {target}";
        }
        catch (Exception exception) when (exception is IOException or UnauthorizedAccessException or JsonException)
        {
            StatusText.Text = $"Export failed (report_write_failed): {exception.Message}";
        }
    }

    private static string ReserveReportPath(string requestedPath)
    {
        var directory = Path.GetDirectoryName(requestedPath) ?? AppContext.BaseDirectory;
        var stem = Path.GetFileNameWithoutExtension(requestedPath);
        var extension = Path.GetExtension(requestedPath);
        var candidate = Path.Combine(directory, stem + extension);
        for (var index = 2; File.Exists(candidate); index++)
            candidate = Path.Combine(directory, $"{stem}-{index}{extension}");
        return candidate;
    }

    private void ConfigurationSelectionChanged(object sender, System.Windows.Controls.SelectionChangedEventArgs e) =>
        ConfigurationChanged(sender, e);

    private void MarkResultStale()
    {
        if (!_hasResult) return;
        _flowStatus = "needs_recalculation";
        _hasResult = false;
        _lastOutcome = null;
        ExportResultButton.IsEnabled = false;
        MetricsText.Text = "Previous result is stale; run analysis again for the current input and configuration.";
        RecordText.Text = MetricsText.Text;
    }
}

public sealed class ConfigurationValidationException(string code, string message) : Exception(message)
{
    public string Code { get; } = code;
}
