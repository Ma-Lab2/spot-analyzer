using System.Globalization;
using System.IO;
using System.Text;
using System.Text.Json;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Media;
using Shapes = System.Windows.Shapes;
using Microsoft.Win32;

namespace SpotAnalysis.App;

public partial class MainWindow : Window
{
    private readonly WorkerClient _workerClient = new();
    private readonly WorkspacePresentationModel _workspace = new();
    private PngInputInfo? _selectedInput;
    private bool _configurationConfirmed;
    private bool _hasResult;
    private bool _resultStale;
    private WorkerOutcome? _lastOutcome;
    private WorkerOutcome? _lastSuccessfulOutcome;
    private CancellationTokenSource? _analysisCancellation;
    private AnalysisRequest? _pendingRequest;
    private AnalysisRequest? _inFlightRequest;
    private ConfigurationValues? _lastConfiguration;
    private string _flowStatus = "ready";
    private bool _configurationReady;
    private string? _failureCode;
    private string? _failureDetails;

    public MainWindow()
    {
        InitializeComponent();
        _configurationReady = true;
        _workspace.Changed += (_, _) => WorkspaceChanged();
        RefreshDraftSummary();
        UpdateConfigurationAvailability();
        UpdateProgressVisual();
        UpdateStatusVisual();
        ImageEmptyState.Visibility = Visibility.Visible;
        CurvesEmptyStateText.Text = "No analysis result yet — curves will appear here after a successful run.";
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
            throw new ConfigurationValidationException("input_required", "Open an 8-bit or 16-bit grayscale PNG, or an 8-bit RGB PNG with R=G=B, before confirming configuration.");
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
            if (!_workspace.Confirm(_pendingRequest))
                throw new ConfigurationValidationException(
                    _workspace.State.FailureCode ?? "configuration_invalid",
                    _workspace.State.FailureMessage ?? "The configuration snapshot could not be confirmed.");
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
        var workspaceRequestId = _workspace.Start();
        if (workspaceRequestId is null)
        {
            StatusText.Text = "Confirm a valid pending configuration before running analysis.";
            return;
        }
        _inFlightRequest = request;
        UpdateRunAvailability();
        await RunAnalysisAsync(async () =>
        {
            var outcome = await _workerClient.RunAsync(
                request,
                _analysisCancellation!.Token,
                TimeSpan.FromSeconds(30));
            WorkspaceWorkerEvent workerEvent = outcome.Status switch
            {
                "success" => new WorkspaceWorkerEvent.Completed(workspaceRequestId, outcome),
                "cancelled" => new WorkspaceWorkerEvent.Cancelled(workspaceRequestId, outcome),
                _ => new WorkspaceWorkerEvent.Failed(workspaceRequestId, outcome),
            };
            if (!_workspace.Apply(workerEvent))
                return new WorkerOutcome("failure", "The worker event was rejected because it no longer belongs to the active run.", FailureCode: "worker_event_ignored");
            return outcome;
        });
        _inFlightRequest = null;
        RefreshDraftSummary();
        UpdateRunAvailability();
    }

    private async void CancelAnalysis_Click(object sender, RoutedEventArgs e)
    {
        _analysisCancellation?.Cancel();
        _workspace.RequestCancel();
        _flowStatus = "cancelling";
        DiagnosticLog.Write("analysis_cancellation_requested", new { flow_status = _flowStatus });
        StatusText.Text = "Cancelling";
        await Task.CompletedTask;
    }

    private async Task RunAnalysisAsync(Func<Task<WorkerOutcome>> operation)
    {
        _analysisCancellation?.Dispose();
        _analysisCancellation = new CancellationTokenSource();
        // A new run never invalidates the last successful record. It is retained
        // as a stale/previous record until a new run completes successfully.
        _resultStale = _lastSuccessfulOutcome is not null;
        _flowStatus = "processing";
        _failureCode = null;
        _failureDetails = null;
        DiagnosticLog.Write("analysis_started", new { flow_status = _flowStatus, input = _selectedInput?.Summary });
        StatusText.Text = "Processing";
        ShowRetainedResult("Processing current run; previous result is retained as stale.");
        try
        {
            var outcome = await operation();
            _lastOutcome = outcome;
            RefreshDiagnosticsSummary();
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
                    _lastSuccessfulOutcome = outcome;
                    _hasResult = _workspace.CurrentRecord is not null;
                    _resultStale = false;
                    _resultStale = _workspace.CurrentRecord?.IsStale != false;
                    if (_resultStale)
                    {
                        StatusText.Text = "Completed, but configuration changed during the run; recompute required";
                        ShowRetainedResult("The completed record belongs to the superseded configuration.");
                    }
                    else
                    {
                        StatusText.Text = "Completed";
                        RecordText.Text = FormatRecordSummary(outcome);
                        MetricsText.Text = FormatMetrics(outcome);
                        RenderCurves(outcome, stale: false);
                        ExportResultButton.IsEnabled = _workspace.CanExportReport;
                    }
                    break;
                case "cancelled":
                    StatusText.Text = "Cancelled";
                    _resultStale = _workspace.CurrentRecord?.IsStale != false;
                    _hasResult = _workspace.CurrentRecord is not null;
                    if (_lastSuccessfulOutcome is not null) _resultStale = true;
                    ShowRetainedResult($"Cancelled ({outcome.FailureCode}): {outcome.ErrorMessage}");
                    break;
                case "timeout":
                    StatusText.Text = "Timed out";
                    _resultStale = _workspace.CurrentRecord?.IsStale != false;
                    _hasResult = _workspace.CurrentRecord is not null;
                    if (_lastSuccessfulOutcome is not null) _resultStale = true;
                    ShowRetainedResult($"Timed out ({outcome.FailureCode}): {outcome.ErrorMessage}");
                    break;
                default:
                    StatusText.Text = $"Failed ({outcome.FailureCode ?? "worker_failure"}): {outcome.ErrorMessage}";
                    _resultStale = _workspace.CurrentRecord?.IsStale != false;
                    _hasResult = _workspace.CurrentRecord is not null;
                    if (_lastSuccessfulOutcome is not null) _resultStale = true;
                    ShowRetainedResult(StatusText.Text);
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
            ShowRetainedResult(StatusText.Text);
        }
        catch (InputValidationException exception)
        {
            _flowStatus = "input_invalid";
            _failureCode = exception.Code;
            _failureDetails = exception.Message;
            DiagnosticLog.Write("analysis_rejected", new { flow_status = _flowStatus, code = exception.Code, message = exception.Message });
            StatusText.Text = $"Invalid input ({exception.Code}): {exception.Message}";
            ShowRetainedResult(StatusText.Text);
        }
        catch (OperationCanceledException)
        {
            _flowStatus = "cancelled";
            _failureCode = "analysis_cancelled";
            _failureDetails = "analysis cancelled";
            DiagnosticLog.Write("analysis_cancelled", new { flow_status = _flowStatus });
            StatusText.Text = "Cancelled";
            _resultStale = _workspace.CurrentRecord?.IsStale != false;
            _hasResult = _workspace.CurrentRecord is not null;
            ShowRetainedResult("Cancelled: analysis did not produce a new record.");
        }
        finally
        {
            _analysisCancellation?.Dispose();
            _analysisCancellation = null;
        }
    }

    private async void OpenPng_Click(object sender, RoutedEventArgs e)
    {
        if (_workspace.IsProcessing)
        {
            StatusText.Text = "Cancel the active analysis before changing the input.";
            return;
        }

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
            _workspace.LoadInput(new WorkspaceInput(input.Path, input.Sha256, input.Width, input.Height, input.BitDepth, input.Summary));
            InputSummaryText.Text = input.Summary;
            InputPreview.Source = input.Preview;
            ImageEmptyState.Visibility = Visibility.Collapsed;
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
            _workspace.RejectInput(exception.Code, exception.Message);
            _pendingRequest = null;
            _configurationConfirmed = false;
            InputPreview.Source = null;
            ImageEmptyState.Visibility = Visibility.Visible;
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
        // Keep the prior successful record in diagnostics even when the current
        // attempt was cancelled, timed out, or failed.
        var outcome = _lastOutcome;
        var recordOutcome = _lastSuccessfulOutcome ?? outcome;
        JsonElement? record = null;
        if (recordOutcome?.Result is JsonElement result && result.TryGetProperty("record", out var recordNode))
            record = recordNode;
        var diagnostics = ReadRecordDiagnostics(record);
        if (outcome?.Diagnostics is not null && outcome.Status is not "success")
            diagnostics = outcome.Diagnostics;
        var reasons = ReadQualityReasonCodes(record);
        var validity = ReadString(record, "measurement_validity") ?? ReadString(record, "summary_status");
        return new DiagnosticSnapshot(
            _flowStatus,
            _failureCode,
            _failureDetails,
            recordOutcome?.RecordId ?? ReadString(record, "record_id"),
            recordOutcome?.AnalysisFingerprint ?? ReadString(record, "analysis_fingerprint"),
            _selectedInput?.Summary ?? recordOutcome?.InputSummary,
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

    private static IReadOnlyList<JsonElement> ReadRecordDiagnostics(JsonElement? record) =>
        record is { } value && value.ValueKind == JsonValueKind.Object
        && value.TryGetProperty("diagnostics", out var child)
            ? child.ValueKind switch
            {
                JsonValueKind.Array => child.EnumerateArray().Select(item => item.Clone()).ToArray(),
                JsonValueKind.Object => new[] { child.Clone() },
                _ => Array.Empty<JsonElement>(),
            }
            : Array.Empty<JsonElement>();

    private static IReadOnlyList<string> ReadQualityReasonCodes(JsonElement? record)
    {
        var reasons = new HashSet<string>(StringComparer.Ordinal);
        foreach (var reason in ReadStringArray(record, "quality_reason_codes"))
            reasons.Add(reason);
        foreach (var reason in ReadStringArray(record, "reason_codes"))
            reasons.Add(reason);

        if (record is { } value && value.TryGetProperty("metrics", out var metrics) && metrics.ValueKind == JsonValueKind.Object)
        {
            foreach (var metric in metrics.EnumerateObject())
                AddMetricReasonCodes(metric.Value, reasons);
        }

        return reasons.ToArray();
    }

    private static void AddMetricReasonCodes(JsonElement metric, HashSet<string> reasons)
    {
        if (metric.ValueKind != JsonValueKind.Object)
            return;

        foreach (var reason in ReadStringArray(metric, "reason_codes"))
            reasons.Add(reason);

        if (metric.TryGetProperty("domains", out var domains) && domains.ValueKind == JsonValueKind.Object)
        {
            foreach (var domain in domains.EnumerateObject())
                AddMetricReasonCodes(domain.Value, reasons);
        }
    }

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
            && _selectedInput is not null
            && _workspace.CanRun;

    private void WorkspaceChanged()
    {
        UpdateConfigurationAvailability();
        UpdateProgressVisual();
        UpdateStatusVisual();
        RefreshDiagnosticsSummary();
    }

    private void StatusText_Changed(object sender, TextChangedEventArgs e) => UpdateStatusVisual();

    private void UpdateStatusVisual()
    {
        if (WorkflowStatusBadge is null || StatusText is null)
            return;

        var status = StatusText.Text.ToLowerInvariant();
        var (surface, border, foreground) = status.Contains("fail") || status.Contains("invalid") || status.Contains("unavailable")
            ? ("ColorInvalidSurface", "ColorInvalid", "ColorInvalid")
            : status.Contains("caution") || status.Contains("stale") || status.Contains("recompute") || status.Contains("timeout")
                ? ("ColorCautionSurface", "ColorCaution", "ColorCaution")
                : status.Contains("complete") || status.Contains("export")
                    ? ("ColorSuccessSurface", "ColorSuccess", "ColorSuccess")
                    : status.Contains("process") || status.Contains("cancel")
                        ? ("ColorInfoSurface", "ColorInfo", "ColorInfo")
                        : ("ColorPanelSubtle", "ColorBorder", "ColorInk");
        WorkflowStatusBadge.Background = (Brush)FindResource(surface);
        WorkflowStatusBadge.BorderBrush = (Brush)FindResource(border);
        StatusText.Foreground = (Brush)FindResource(foreground);
    }

    private void UpdateProgressVisual()
    {
        var state = _workspace.State;
        if (_workspace.IsProcessing)
        {
            AnalysisProgressBar.IsIndeterminate = !state.ProgressFraction.HasValue;
            if (state.ProgressFraction is { } fraction)
                AnalysisProgressBar.Value = Math.Clamp(fraction, 0, 1);
            ProgressText.Text = string.IsNullOrWhiteSpace(state.ProgressMessage)
                ? "Processing the current analysis request…"
                : state.ProgressMessage;
            return;
        }

        AnalysisProgressBar.IsIndeterminate = false;
        AnalysisProgressBar.Value = state.WorkflowStatus is WorkspaceWorkflowStatus.Completed or WorkspaceWorkflowStatus.Exported ? 1 : 0;
        ProgressText.Text = state.WorkflowStatus switch
        {
            WorkspaceWorkflowStatus.Ready => "Ready for an input image.",
            WorkspaceWorkflowStatus.NeedsRecalculation => "Needs recalculation — the retained record is stale.",
            WorkspaceWorkflowStatus.InputInvalid or WorkspaceWorkflowStatus.ConfigurationInvalid => "Resolve the highlighted input or configuration issue.",
            WorkspaceWorkflowStatus.Failed or WorkspaceWorkflowStatus.AnalysisFailed or WorkspaceWorkflowStatus.WorkerError or WorkspaceWorkflowStatus.ProtocolError => "Analysis failed; inspect diagnostics and try again.",
            WorkspaceWorkflowStatus.Cancelled or WorkspaceWorkflowStatus.TimedOut => "Run ended without a new record; the prior record is retained.",
            WorkspaceWorkflowStatus.ExportFailed => "Export failed; the current record remains available for retry.",
            WorkspaceWorkflowStatus.Completed or WorkspaceWorkflowStatus.Exported => "Current analysis record is ready.",
            _ => "Confirm the configuration to continue.",
        };
    }

    private void RefreshDiagnosticsSummary()
    {
        var lines = new List<string> { DiagnosticPackage.BuildAboutText() };
        if (_workspace.CurrentRecord is { } record)
        {
            var state = record.IsStale ? "stale / recompute required" : "current";
            lines.Add($"\nRecord {record.RecordId ?? record.Outcome.RecordId ?? "unknown"} · {state}");
            lines.Add($"Metric validity: {record.MeasurementValidity.ToString().ToLowerInvariant()}");
            if (record.QualityReasonCodes is { Count: > 0 })
                lines.Add("Quality reasons: " + string.Join(", ", record.QualityReasonCodes));
            if (record.Diagnostics is { Count: > 0 })
                lines.Add("Diagnostics: " + string.Join("; ", record.Diagnostics.Select(item => $"{item.Code}: {item.Message}")));
        }
        else if (_lastOutcome?.Diagnostics is { Count: > 0 } diagnostics)
        {
            lines.Add("\nCurrent run diagnostics: " + string.Join("; ", diagnostics.Select(item =>
                $"{GetString(item, "code") ?? "diagnostic"}: {GetString(item, "message") ?? item.ToString()}")));
        }
        DiagnosticsText.Text = string.Join(Environment.NewLine, lines);
    }

    private void UpdateConfigurationAvailability()
    {
        var enabled = _workspace.CanEditConfiguration;
        OpenInputButton.IsEnabled = enabled;
        CalibrationXText.IsEnabled = enabled;
        CalibrationYText.IsEnabled = enabled;
        CalibrationUnitsText.IsEnabled = enabled;
        CalibrationSourceText.IsEnabled = enabled;
        CalibrationStatus.IsEnabled = enabled;
        RoiXText.IsEnabled = enabled;
        RoiYText.IsEnabled = enabled;
        RoiWidthText.IsEnabled = enabled;
        RoiHeightText.IsEnabled = enabled;
        BackgroundXText.IsEnabled = enabled;
        BackgroundYText.IsEnabled = enabled;
        BackgroundWidthText.IsEnabled = enabled;
        BackgroundHeightText.IsEnabled = enabled;
        ConfirmConfigurationButton.IsEnabled = enabled;
        CancelAnalysisButton.IsEnabled = _workspace.IsProcessing;
    }

    private void ConfigurationChanged(object sender, RoutedEventArgs e)
    {
        if (!_configurationReady)
            return;
        _configurationConfirmed = false;
        _pendingRequest = null;
        try
        {
            var draft = ReadConfiguration();
            _workspace.EditDraft(new AnalysisDraft(
                draft.RegionX, draft.RegionY, draft.RegionWidth, draft.RegionHeight,
                draft.BackgroundX, draft.BackgroundY, draft.BackgroundWidth, draft.BackgroundHeight,
                draft.CalibrationStatus, draft.CalibrationX, draft.CalibrationY,
                draft.CalibrationUnits, draft.CalibrationSource));
        }
        catch (ConfigurationValidationException)
        {
            _workspace.StageInvalidDraft();
        }
        _flowStatus = _hasResult ? "needs_recalculation" : "configuration_changed";
        MarkResultStale();
        if (_inFlightRequest is null)
            RefreshDraftSummary();
        UpdateRunAvailability();
    }

    private void CurveCanvas_SizeChanged(object sender, SizeChangedEventArgs e)
    {
        if (_lastSuccessfulOutcome is not null && !_resultStale)
            RenderCurves(_lastSuccessfulOutcome, stale: false);
    }

    private void RenderCurves(WorkerOutcome? outcome, bool stale)
    {
        XProfileCanvas.Children.Clear(); YProfileCanvas.Children.Clear(); EnergyCanvas.Children.Clear();
        CurveIdentityText.Text = ""; EnergyMarkerText.Text = "";
        if (stale)
        {
            CurvesEmptyState.Visibility = Visibility.Visible;
            CurvesEmptyStateText.Text = "N/A — curves belong to a stale analysis record; recompute to interpret the current configuration.";
            CurveStatusText.Text = "stale";
            return;
        }
        if (outcome?.Result is not JsonElement result || !result.TryGetProperty("record", out var record)
            || record.ValueKind != JsonValueKind.Object)
        {
            CurvesEmptyState.Visibility = Visibility.Visible;
            CurvesEmptyStateText.Text = "N/A — no current analysis record.";
            CurveStatusText.Text = "unavailable";
            return;
        }
        var curves = record.TryGetProperty("display_projection", out var projection)
            && projection.ValueKind == JsonValueKind.Object && projection.TryGetProperty("curves", out var projected)
            ? projected
            : record.TryGetProperty("diagnostics", out var diagnostics) && diagnostics.TryGetProperty("report_curves", out var reported)
                ? reported : default;
        if (curves.ValueKind != JsonValueKind.Object)
        {
            CurvesEmptyState.Visibility = Visibility.Visible;
            CurvesEmptyStateText.Text = "N/A — curve projection unavailable (curve_unavailable).";
            CurveStatusText.Text = "unavailable";
            return;
        }
        CurvesEmptyState.Visibility = Visibility.Collapsed;
        var id = GetString(record, "record_id") ?? outcome.RecordId ?? "unknown";
        CurveIdentityText.Text = $"record {id} · read-only projection";
        CurveStatusText.Text = "current";
        var axisUnit = GetString(curves, "profile_axis_unit") ?? "px";
        var energyUnit = GetString(curves, "energy_radius_unit") ?? "N/A";
        DrawSeries(XProfileCanvas, Numbers(curves, "profile_x"), Numbers(curves, "profile_x_actual"), Brushes.SteelBlue, false);
        DrawSeries(XProfileCanvas, Numbers(curves, "profile_x"), Numbers(curves, "profile_x_fitted"), Brushes.IndianRed, true);
        DrawSeries(YProfileCanvas, Numbers(curves, "profile_y"), Numbers(curves, "profile_y_actual"), Brushes.SteelBlue, false);
        DrawSeries(YProfileCanvas, Numbers(curves, "profile_y"), Numbers(curves, "profile_y_fitted"), Brushes.IndianRed, true);
        DrawSeries(EnergyCanvas, Numbers(curves, "energy_radius"), Numbers(curves, "energy_fraction"), Brushes.DarkGreen, false);
        var markers = curves.TryGetProperty("energy_markers", out var markerNode) && markerNode.ValueKind == JsonValueKind.Object
            ? markerNode : default;
        EnergyMarkerText.Text = $"EE50 / EE80 ({energyUnit}): {Marker(markers, "ee50")} / {Marker(markers, "ee80")} · Y={GetString(curves, "energy_fraction_unit") ?? "fraction"}";
    }

    private static string Marker(JsonElement markers, string name)
    {
        if (markers.ValueKind == JsonValueKind.Object && markers.TryGetProperty(name, out var node)
            && node.ValueKind == JsonValueKind.Object && node.TryGetProperty("radius", out var radius)
            && radius.ValueKind != JsonValueKind.Null)
            return radius.ToString();
        return "N/A (unavailable)";
    }

    private static double[] Numbers(JsonElement node, string property) =>
        node.ValueKind == JsonValueKind.Object && node.TryGetProperty(property, out var values) && values.ValueKind == JsonValueKind.Array
            ? values.EnumerateArray().Where(item => item.ValueKind == JsonValueKind.Number && double.IsFinite(item.GetDouble())).Select(item => item.GetDouble()).ToArray()
            : Array.Empty<double>();

    private static void DrawSeries(Canvas canvas, double[] xs, double[] ys, Brush brush, bool dashed)
    {
        if (xs.Length == 0 || xs.Length != ys.Length || canvas.ActualWidth < 2 || canvas.ActualHeight < 2) return;
        var finite = xs.Zip(ys, (x, y) => (x, y)).Where(pair => double.IsFinite(pair.x) && double.IsFinite(pair.y)).ToArray();
        if (finite.Length < 2) return;
        var minX = finite.Min(pair => pair.x); var maxX = finite.Max(pair => pair.x);
        var minY = finite.Min(pair => pair.y); var maxY = finite.Max(pair => pair.y);
        var dx = Math.Max(maxX - minX, 1e-12); var dy = Math.Max(maxY - minY, 1e-12);
        var line = new Shapes.Polyline { Stroke = brush, StrokeThickness = 1.5, Opacity = .9 };
        if (dashed) line.StrokeDashArray = new DoubleCollection { 4, 3 };
        foreach (var pair in finite)
            line.Points.Add(new Point((pair.x - minX) / dx * (canvas.ActualWidth - 4) + 2, canvas.ActualHeight - 2 - (pair.y - minY) / dy * (canvas.ActualHeight - 6)));
        canvas.Children.Add(line);
    }

    private void ShowRetainedResult(string currentRunStatus)
    {
        RenderCurves(_lastSuccessfulOutcome, stale: true);
        if (_lastSuccessfulOutcome is null)
        {
            RecordText.Text = currentRunStatus;
            if (_lastOutcome?.Status is not "success")
                MetricsText.Text = _lastOutcome is null ? "No result available" : FormatDiagnostics(_lastOutcome);
            ExportResultButton.IsEnabled = false;
            return;
        }

        var recordLabel = _resultStale ? "Previous result (stale)" : "Previous result";
        RecordText.Text = $"Current run: {currentRunStatus}\n{recordLabel}:\n{FormatRecordSummary(_lastSuccessfulOutcome)}";
        MetricsText.Text = $"Previous result (stale; no new record was produced.)\n{FormatMetrics(_lastSuccessfulOutcome)}";
        ExportResultButton.IsEnabled = _workspace.CanExportReport;
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
        var status = GetString(metric, "status") ?? "unknown";
        var value = status is "valid" or "caution" or "warning"
            && metric.TryGetProperty("value", out var valueNode)
            && valueNode.ValueKind is not (JsonValueKind.Null or JsonValueKind.Undefined)
            ? valueNode.ToString()
            : "N/A";
        var unit = GetString(metric, "unit") ?? "";
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

    private void ExportResult_Click(object sender, RoutedEventArgs e)
    {
        if (!_hasResult || !_workspace.CanExportReport || _lastSuccessfulOutcome?.Result is not JsonElement result)
        {
            StatusText.Text = "No current result is available to export; recompute the stale record first.";
            return;
        }

        var requestedName = ReportNameText.Text.Trim();
        if (requestedName.Length == 0)
        {
            _workspace.ReportExportFailed("report_name_empty", "Enter a report name.");
            StatusText.Text = "Report export failed: enter a report name.";
            return;
        }
        var dialog = new SaveFileDialog
        {
            Filter = "PDF report (*.pdf)|*.pdf|PNG report (*.png)|*.png",
            DefaultExt = ".pdf",
            AddExtension = true,
            OverwritePrompt = false,
            FileName = ReportExporter.SanitizeName(requestedName) + ".pdf",
        };
        if (dialog.ShowDialog() != true) return;
        try
        {
            var format = Path.GetExtension(dialog.FileName).TrimStart('.').ToLowerInvariant();
            var outputDirectory = Path.GetDirectoryName(dialog.FileName) ?? AppContext.BaseDirectory;
            var outcome = ReportExporter.Write(
                result,
                new ReportSpecification(
                    format,
                    requestedName,
                    outputDirectory,
                    ReportTimestampCheck.IsChecked == true));
            if (outcome.FlowStatus != "exported")
            {
                _workspace.ReportExportFailed(
                    outcome.ErrorCode ?? "report_write_failed",
                    outcome.ErrorMessage ?? "The report could not be written.");
                StatusText.Text = $"Export failed ({outcome.ErrorCode ?? "report_write_failed"}): {outcome.ErrorMessage}";
                return;
            }
            _workspace.ReportExported();
            StatusText.Text = $"Report exported: {outcome.Path}";
        }
        catch (Exception exception) when (exception is IOException or UnauthorizedAccessException or ArgumentException)
        {
            _workspace.ReportExportFailed("report_write_failed", exception.Message);
            StatusText.Text = $"Export failed (report_write_failed): {exception.Message}";
        }
    }

    private void ConfigurationSelectionChanged(object sender, System.Windows.Controls.SelectionChangedEventArgs e) =>
        ConfigurationChanged(sender, e);

    private void MarkResultStale()
    {
        if (_lastSuccessfulOutcome is null) return;
        _flowStatus = "needs_recalculation";
        _resultStale = true;
        _hasResult = true;
        ShowRetainedResult("Configuration or input changed; run analysis again for a current record.");
    }
}

public sealed class ConfigurationValidationException(string code, string message) : Exception(message)
{
    public string Code { get; } = code;
}
