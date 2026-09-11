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
    private CancellationTokenSource? _analysisCancellation;

    public MainWindow() => InitializeComponent();

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
        var status = ((ComboBoxItem)CalibrationStatus.SelectedItem).Content?.ToString() ?? "missing";
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

    private void ConfirmConfiguration_Click(object sender, RoutedEventArgs e)
    {
        try
        {
            _ = ReadConfiguration();
            _configurationConfirmed = true;
            StatusText.Text = "Configuration confirmed";
            MarkResultStale();
        }
        catch (ConfigurationValidationException exception)
        {
            _configurationConfirmed = false;
            StatusText.Text = $"Invalid configuration ({exception.Code}): {exception.Message}";
        }
    }

    private async void RunAnalysis_Click(object sender, RoutedEventArgs e)
    {
        await RunAnalysisAsync(async () =>
        {
            if (_selectedInput is null)
                throw new ConfigurationValidationException("input_required", "Open an 8-bit or 16-bit grayscale PNG before running analysis.");
            if (!_configurationConfirmed)
                throw new ConfigurationValidationException("configuration_unconfirmed", "Confirm calibration and analysis region before running analysis.");
            var configuration = ReadConfiguration();
            return await _workerClient.RunPngAsync(
                _selectedInput.Path,
                _selectedInput.Sha256,
                semanticsConfirmed: true,
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
                _analysisCancellation!.Token,
                TimeSpan.FromSeconds(30));
        });
    }

    private async void CancelAnalysis_Click(object sender, RoutedEventArgs e)
    {
        _analysisCancellation?.Cancel();
        StatusText.Text = "Cancelling";
        await Task.CompletedTask;
    }

    private async Task RunAnalysisAsync(Func<Task<WorkerOutcome>> operation)
    {
        _analysisCancellation?.Dispose();
        _analysisCancellation = new CancellationTokenSource();
        StatusText.Text = "Processing";
        try
        {
            var outcome = await operation();
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
            StatusText.Text = $"Invalid configuration ({exception.Code}): {exception.Message}";
            RecordText.Text = StatusText.Text;
        }
        catch (InputValidationException exception)
        {
            StatusText.Text = $"Invalid input ({exception.Code}): {exception.Message}";
            RecordText.Text = StatusText.Text;
        }
        catch (OperationCanceledException)
        {
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
            StatusText.Text = "Input loaded; confirm configuration";
            MarkResultStale();
        }
        catch (InputValidationException exception)
        {
            _selectedInput = null;
            InputPreview.Source = null;
            InputSummaryText.Text = "No input selected";
            StatusText.Text = $"Invalid input ({exception.Code}): {exception.Message}";
        }
    }

    private void ConfigurationChanged(object sender, RoutedEventArgs e)
    {
        _configurationConfirmed = false;
        MarkResultStale();
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
