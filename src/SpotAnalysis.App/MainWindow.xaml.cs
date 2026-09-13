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
    private readonly MultiImageWorkspaceModel _workspaceItems = new();
    private readonly WorkspacePresentationModel _emptyWorkspace = new();
    private readonly Dictionary<string, UiWorkspaceItem> _uiItems = new(StringComparer.Ordinal);
    private readonly WorkspaceAnalysisScheduler _scheduler;
    private UiWorkspaceItem? _currentItem;
    private bool _configurationReady;

    private WorkspacePresentationModel _workspace => _currentItem?.Item.Presentation ?? _emptyWorkspace;
    private PngInputInfo? _selectedInput => _currentItem?.Input;
    private bool _configurationConfirmed { get => _currentItem?.ConfigurationConfirmed == true; set { if (_currentItem is not null) _currentItem.ConfigurationConfirmed = value; } }
    private bool _hasResult { get => _currentItem?.HasResult == true; set { if (_currentItem is not null) _currentItem.HasResult = value; } }
    private bool _resultStale { get => _currentItem?.ResultStale == true; set { if (_currentItem is not null) _currentItem.ResultStale = value; } }
    private WorkerOutcome? _lastOutcome { get => _currentItem?.LastOutcome; set { if (_currentItem is not null) _currentItem.LastOutcome = value; } }
    private WorkerOutcome? _lastSuccessfulOutcome { get => _currentItem?.LastSuccessfulOutcome; set { if (_currentItem is not null) _currentItem.LastSuccessfulOutcome = value; } }
    private AnalysisRequest? _pendingRequest { get => _currentItem?.PendingRequest; set { if (_currentItem is not null) _currentItem.PendingRequest = value; } }
    private AnalysisRequest? _inFlightRequest { get => _currentItem?.InFlightRequest; set { if (_currentItem is not null) _currentItem.InFlightRequest = value; } }
    private ConfigurationValues? _lastConfiguration { get => _currentItem?.LastConfiguration; set { if (_currentItem is not null) _currentItem.LastConfiguration = value; } }
    private string _flowStatus { get => _currentItem?.FlowStatus ?? "ready"; set { if (_currentItem is not null) _currentItem.FlowStatus = value; } }
    private string? _failureCode { get => _currentItem?.FailureCode; set { if (_currentItem is not null) _currentItem.FailureCode = value; } }
    private string? _failureDetails { get => _currentItem?.FailureDetails; set { if (_currentItem is not null) _currentItem.FailureDetails = value; } }

    public MainWindow()
    {
        InitializeComponent();
        _scheduler = new WorkspaceAnalysisScheduler(
            (job, token) => _workerClient.RunAsync(job.Request, token, TimeSpan.FromSeconds(30)),
            maxConcurrency: 1);
        _configurationReady = true;
        _workspaceItems.Changed += (_, _) => RefreshImageItemsList();
        RefreshDraftSummary();
        UpdateConfigurationAvailability();
        UpdateProgressVisual();
        UpdateStatusVisual();
        ImageEmptyState.Visibility = Visibility.Visible;
        CurvesEmptyStateText.Text = "No analysis result yet — curves will appear here after a successful run.";
        DiagnosticsText.Text = DiagnosticPackage.BuildAboutText();
        DiagnosticLog.Write("client_started", new { output_capability = DiagnosticPackage.OutputCapability });
    }

    private sealed class UiWorkspaceItem(MultiImageWorkspaceItem item, PngInputInfo? input)
    {
        public MultiImageWorkspaceItem Item { get; } = item;
        public PngInputInfo? Input { get; } = input;
        public bool ConfigurationConfirmed { get; set; }
        public bool HasResult { get; set; }
        public bool ResultStale { get; set; }
        public WorkerOutcome? LastOutcome { get; set; }
        public WorkerOutcome? LastSuccessfulOutcome { get; set; }
        public AnalysisRequest? PendingRequest { get; set; }
        public AnalysisRequest? InFlightRequest { get; set; }
        public ConfigurationValues? LastConfiguration { get; set; }
        public string FlowStatus { get; set; } = "ready";
        public string? FailureCode { get; set; }
        public string? FailureDetails { get; set; }
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
        string CalibrationSource,
        AdvancedAnalysisSettings? AdvancedSettings = null);

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

        var filtering = (AdvancedFiltering.SelectedItem as ComboBoxItem)?.Content?.ToString() ?? "none";
        var dpc = (AdvancedDpc.SelectedItem as ComboBoxItem)?.Content?.ToString() ?? "none";
        var advanced = filtering == "none" && dpc == "none"
            ? null
            : new AdvancedAnalysisSettings(Filtering: filtering, Dpc: dpc);
        if (AdvancedSettingsStatus is not null)
            AdvancedSettingsStatus.Text = advanced is null ? "使用推荐默认值" : "已偏离推荐默认值";

        return new ConfigurationValues(
            region.x, region.y, region.width, region.height,
            background?.x, background?.y, background?.width, background?.height,
            status, calibrationX, calibrationY,
            CalibrationUnitsText.Text.Trim(), CalibrationSourceText.Text.Trim(), advanced);
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
        // Pixel-domain analysis is valid without calibration. Physical-domain
        // metrics are gated by the Python core instead of a fake scale.
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
            Path.Combine(Path.GetTempPath(), "SpotAnalysis", "derived"),
            configuration.AdvancedSettings);
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
            StatusText.Text = "已确认 ROI 和相对强度码值语义，正在生成正式结果";
            MarkResultStale();
            UpdateRunAvailability();
            // The final confirmation is the only formal-run action.  The
            // hidden compatibility button is not part of the normal path.
            RunAnalysis_Click(sender, e);
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
        var item = _currentItem;
        if (item is null || !item.ConfigurationConfirmed || item.PendingRequest is null)
        {
            StatusText.Text = "Confirm a valid pending configuration before running analysis.";
            return;
        }

        var request = _pendingRequest;
        if (request is null) return;
        var workspaceRequestId = item.Item.Presentation.Start();
        if (workspaceRequestId is null)
        {
            StatusText.Text = "Confirm a valid pending configuration before running analysis.";
            return;
        }
        item.InFlightRequest = request;
        UpdateRunAvailability();
        await RunScheduledAsync(item, request, workspaceRequestId, WorkspaceAnalysisPriority.Current, isPreview: false);
    }

    private async void CancelAnalysis_Click(object sender, RoutedEventArgs e)
    {
        var item = _currentItem;
        if (item is null) return;
        _scheduler.Cancel(item.Item.Id);
        item.Item.Presentation.RequestCancel();
        item.FlowStatus = "cancelling";
        DiagnosticLog.Write("analysis_cancellation_requested", new { item_id = item.Item.Id, flow_status = item.FlowStatus });
        StatusText.Text = "Cancelling";
        await Task.CompletedTask;
    }

    private void ApplyPreviewConfiguration(UiWorkspaceItem item, WorkerOutcome outcome)
    {
        if (outcome.Result is not JsonElement terminal || !terminal.TryGetProperty("record", out var record)
            || !record.TryGetProperty("configuration", out var configuration)
            || !configuration.TryGetProperty("region", out var region)
            || region.ValueKind != JsonValueKind.Object) return;
        var background = configuration.TryGetProperty("background_region", out var backgroundNode)
            && backgroundNode.ValueKind == JsonValueKind.Object ? backgroundNode : default;
        var calibration = configuration.TryGetProperty("calibration", out var calibrationNode)
            && calibrationNode.ValueKind == JsonValueKind.Object ? calibrationNode : default;
        var draft = new AnalysisDraft(
            IntValue(region, "x"), IntValue(region, "y"), IntValue(region, "width"), IntValue(region, "height"),
            OptionalInt(background, "x"), OptionalInt(background, "y"), OptionalInt(background, "width"), OptionalInt(background, "height"),
            GetString(calibration, "confirmation") ?? "missing",
            OptionalDouble(calibration, "x_unit_per_pixel"), OptionalDouble(calibration, "y_unit_per_pixel"),
            GetString(calibration, "physical_unit") ?? "", GetString(calibration, "source") ?? "");
        _workspaceItems.SetAutomaticDraft(item.Item.Id, draft);
        item.LastConfiguration = ToConfiguration(item.Item.Presentation.Draft ?? draft);
        if (ReferenceEquals(item, _currentItem))
            PopulateConfiguration(item.Item.Presentation.Draft ?? draft);
    }

    private static int IntValue(JsonElement node, string property) =>
        node.TryGetProperty(property, out var value) && value.TryGetInt32(out var parsed) ? parsed : 0;

    private static int? OptionalInt(JsonElement node, string property) =>
        node.ValueKind == JsonValueKind.Object && node.TryGetProperty(property, out var value) && value.TryGetInt32(out var parsed) ? parsed : null;

    private static double? OptionalDouble(JsonElement node, string property) =>
        node.ValueKind == JsonValueKind.Object && node.TryGetProperty(property, out var value) && value.ValueKind == JsonValueKind.Number ? value.GetDouble() : null;

    private static ConfigurationValues ToConfiguration(AnalysisDraft draft) => new(
        draft.RegionX, draft.RegionY, draft.RegionWidth, draft.RegionHeight,
        draft.BackgroundX, draft.BackgroundY, draft.BackgroundWidth, draft.BackgroundHeight,
        draft.CalibrationStatus, draft.CalibrationX, draft.CalibrationY,
        draft.CalibrationUnits, draft.CalibrationSource, draft.AdvancedSettings);

    private async Task RunScheduledAsync(
        UiWorkspaceItem item,
        AnalysisRequest request,
        string workspaceRequestId,
        WorkspaceAnalysisPriority priority,
        bool isPreview)
    {
        item.ResultStale = item.LastSuccessfulOutcome is not null;
        item.FlowStatus = isPreview ? "preview_processing" : "processing";
        item.FailureCode = null;
        item.FailureDetails = null;
        if (ReferenceEquals(item, _currentItem))
        {
            StatusText.Text = isPreview ? "Preview processing" : "Processing";
            ShowRetainedResult("Processing current run; previous result is retained as stale.");
            UpdateRunAvailability();
        }

        var scheduled = await _scheduler.Schedule(new WorkspaceAnalysisJob(item.Item.Id, workspaceRequestId, request, priority));
        var outcome = scheduled.Outcome;
        WorkspaceWorkerEvent workerEvent = outcome.Status switch
        {
            "success" => new WorkspaceWorkerEvent.Completed(scheduled.RequestId, outcome),
            "cancelled" => new WorkspaceWorkerEvent.Cancelled(scheduled.RequestId, outcome),
            _ => new WorkspaceWorkerEvent.Failed(scheduled.RequestId, outcome),
        };
        var accepted = _workspaceItems.Apply(scheduled.ItemId, workerEvent);
        if (!accepted)
        {
            DiagnosticLog.Write("workspace_item_event_ignored", new
            {
                item_id = scheduled.ItemId,
                request_id = scheduled.RequestId,
                outcome.Status,
            });
            return;
        }

        item.LastOutcome = outcome;
        item.FailureCode = outcome.FailureCode;
        item.FailureDetails = outcome.ErrorMessage;
        if (outcome.Status == "success")
        {
            if (isPreview) ApplyPreviewConfiguration(item, outcome);
            item.LastSuccessfulOutcome = outcome;
            item.HasResult = item.Item.Presentation.CurrentRecord is not null;
            item.ResultStale = item.Item.Presentation.CurrentRecord?.IsStale != false;
            item.FlowStatus = isPreview ? "preview" : item.ResultStale ? "needs_recalculation" : "completed";
        }
        else
        {
            item.FlowStatus = outcome.Status switch
            {
                "cancelled" => "cancelled",
                "timeout" => "timeout",
                _ => "failed",
            };
            item.ResultStale = item.Item.Presentation.CurrentRecord?.IsStale == true;
            item.HasResult = item.Item.Presentation.CurrentRecord is not null;
        }
        item.InFlightRequest = null;
        if (isPreview && accepted && outcome.Status == "success"
            && item.Item.Presentation.CurrentRecord?.IsStale == true
            && item.Item.Presentation.Draft is { } updatedDraft && item.Input is not null)
        {
            var updatedRequest = BuildRequestForItem(item, ToConfiguration(updatedDraft)).AsPreview();
            var updatedRequestId = item.Item.Presentation.StartPreview(updatedRequest);
            if (updatedRequestId is not null)
            {
                item.InFlightRequest = updatedRequest;
                _ = RunScheduledAsync(item, updatedRequest, updatedRequestId, priority, isPreview: true);
            }
        }
        DiagnosticLog.Write("workspace_item_finished", new
        {
            item_id = item.Item.Id,
            request_id = scheduled.RequestId,
            priority = priority.ToString().ToLowerInvariant(),
            accepted,
            outcome.Status,
            outcome.FailureCode,
        });
        if (Dispatcher.HasShutdownStarted || Dispatcher.HasShutdownFinished) return;
        await Dispatcher.InvokeAsync(() =>
        {
            RefreshImageItemsList();
            if (ReferenceEquals(item, _currentItem))
                ProjectCurrentItem();
        });
    }

    private async void OpenPng_Click(object sender, RoutedEventArgs e)
    {
        var dialog = new OpenFileDialog
        {
            Filter = "PNG files (*.png)|*.png",
            CheckFileExists = true,
            Multiselect = true,
        };
        if (dialog.ShowDialog() != true) return;

        UiWorkspaceItem? firstAdded = null;
        foreach (var path in dialog.FileNames)
        {
            var item = await AddWorkspaceItemAsync(path);
            firstAdded ??= item;
            if (ReferenceEquals(item, firstAdded))
            {
                SelectItem(item);
                RefreshImageItemsList();
                ImageItemsList.SelectedItem = item.Item;
                QueuePreview(item, WorkspaceAnalysisPriority.Current);
            }
            else
            {
                QueuePreview(item, WorkspaceAnalysisPriority.Background);
            }
        }
    }

    private async Task<UiWorkspaceItem> AddWorkspaceItemAsync(string path)
    {
        try
        {
            var input = await PngInput.ReadAsync(path);
            var workspaceInput = new WorkspaceInput(input.Path, input.Sha256, input.Width, input.Height, input.BitDepth, input.Summary);
            var modelItem = _workspaceItems.Add(workspaceInput);
            var uiItem = new UiWorkspaceItem(modelItem, input) { FlowStatus = "input_loaded" };
            _uiItems.Add(modelItem.Id, uiItem);
            modelItem.Presentation.Changed += (_, _) =>
            {
                if (ReferenceEquals(uiItem, _currentItem)) WorkspaceChanged();
            };
            DiagnosticLog.Write("input_loaded", new { item_id = modelItem.Id, input.Summary, input.Sha256 });
            return uiItem;
        }
        catch (InputValidationException exception)
        {
            var rejectedInput = new WorkspaceInput(path, "", 0, 0, 0, Path.GetFileName(path));
            var modelItem = _workspaceItems.Add(rejectedInput);
            modelItem.Presentation.RejectInput(exception.Code, exception.Message);
            var uiItem = new UiWorkspaceItem(modelItem, null)
            {
                FlowStatus = "input_invalid",
                FailureCode = exception.Code,
                FailureDetails = exception.Message,
            };
            _uiItems.Add(modelItem.Id, uiItem);
            DiagnosticLog.Write("input_rejected", new { item_id = modelItem.Id, code = exception.Code, message = exception.Message });
            return uiItem;
        }
    }

    private void QueuePreview(UiWorkspaceItem item, WorkspaceAnalysisPriority priority)
    {
        if (item.Input is null) return;
        var request = AnalysisRequest.CreatePreview(
            item.Input.Path, item.Input.Sha256,
            Path.Combine(Path.GetTempPath(), "SpotAnalysis", "derived", item.Item.Id));
        var requestId = item.Item.Presentation.StartPreview(request);
        if (requestId is null) return;
        item.InFlightRequest = request;
        _ = RunScheduledAsync(item, request, requestId, priority, isPreview: true);
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
            ["advanced_settings"] = configuration.AdvancedSettings?.ToPayload(),
            ["advanced_settings_status"] = configuration.AdvancedSettings is null || configuration.AdvancedSettings.IsDefault
                ? "using_recommended_defaults"
                : "deviated_from_recommended_defaults",
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
            Path.Combine(Path.GetTempPath(), "SpotAnalysis", "derived"),
            configuration.AdvancedSettings);
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
        RefreshImageItemsList();
    }

    private void RefreshImageItemsList()
    {
        if (ImageItemsList is null) return;
        var selectedId = _currentItem?.Item.Id;
        ImageItemsList.ItemsSource = null;
        ImageItemsList.ItemsSource = _workspaceItems.Items;
        if (selectedId is not null)
            ImageItemsList.SelectedItem = _workspaceItems.Items.FirstOrDefault(item => item.Id == selectedId);
    }

    private void ImageItemsList_SelectionChanged(object sender, SelectionChangedEventArgs e)
    {
        if (ImageItemsList.SelectedItem is not MultiImageWorkspaceItem selected
            || !_uiItems.TryGetValue(selected.Id, out var item)) return;
        SelectItem(item);
        _scheduler.Promote(item.Item.Id);
    }

    private void SelectItem(UiWorkspaceItem item)
    {
        if (ReferenceEquals(item, _currentItem)) return;
        _currentItem = item;
        _workspaceItems.Select(item.Item.Id);
        ProjectCurrentItem();
    }

    private void ProjectCurrentItem()
    {
        var item = _currentItem;
        if (item is null)
        {
            InputPreview.Source = null;
            ImageEmptyState.Visibility = Visibility.Visible;
            InputSummaryText.Text = "No input selected";
            return;
        }

        InputSummaryText.Text = item.Input?.Summary ?? item.Item.Input.Summary;
        InputPreview.Source = item.Input?.Preview;
        ImageEmptyState.Visibility = item.Input is null ? Visibility.Visible : Visibility.Collapsed;
        RenderSelectedDisplay();
        if (item.Item.Presentation.Draft is { } draft)
            PopulateConfiguration(draft);
        item.PendingRequest = item.Item.Presentation.Draft is null || item.Input is null
            ? null
            : BuildRequestForItem(item, ToConfiguration(item.Item.Presentation.Draft));
        SummaryText.Text = item.PendingRequest?.Summary ?? "No valid pending request for this image.";
        LockRoiCheck.IsChecked = _workspaceItems.IsRoiLocked;

        var outcome = item.LastSuccessfulOutcome;
        if (outcome is not null)
        {
            item.HasResult = item.Item.Presentation.CurrentRecord is not null;
            item.ResultStale = item.Item.Presentation.CurrentRecord?.IsStale == true;
            RecordText.Text = FormatRecordSummary(outcome);
            MetricsText.Text = FormatMetrics(outcome);
            RenderCurves(outcome, item.ResultStale);
        }
        else
        {
            RecordText.Text = item.FailureDetails ?? "No analysis record";
            MetricsText.Text = item.LastOutcome is null ? "No result available" : FormatDiagnostics(item.LastOutcome);
            RenderCurves(null, stale: false);
        }
        StatusText.Text = item.Item.Presentation.State.WorkflowStatus switch
        {
            WorkspaceWorkflowStatus.PreviewProcessing => "Preview processing",
            WorkspaceWorkflowStatus.PreviewAvailable => "Preview available — review ROI, then confirm formal result",
            WorkspaceWorkflowStatus.Processing or WorkspaceWorkflowStatus.Formalizing => "Processing formal result",
            WorkspaceWorkflowStatus.NeedsRecalculation => "Needs recalculation",
            WorkspaceWorkflowStatus.Exported => "Report exported",
            WorkspaceWorkflowStatus.Completed => "Formal result available",
            WorkspaceWorkflowStatus.Cancelled => "Cancelled",
            WorkspaceWorkflowStatus.TimedOut => "Timed out",
            WorkspaceWorkflowStatus.InputInvalid => $"Invalid input ({item.FailureCode}): {item.FailureDetails}",
            WorkspaceWorkflowStatus.Failed or WorkspaceWorkflowStatus.AnalysisFailed
                or WorkspaceWorkflowStatus.WorkerError or WorkspaceWorkflowStatus.ProtocolError
                => $"Failed ({item.FailureCode}): {item.FailureDetails}",
            _ => item.Input is null ? "Input failed" : "Input loaded",
        };
        ExportResultButton.IsEnabled = item.Item.Presentation.CanExportReport;
        UpdateConfigurationAvailability();
        UpdateProgressVisual();
        RefreshDiagnosticsSummary();
        UpdateRunAvailability();
    }

    private void PopulateConfiguration(AnalysisDraft draft)
    {
        _configurationReady = false;
        try
        {
            RoiXText.Text = draft.RegionX.ToString(CultureInfo.InvariantCulture);
            RoiYText.Text = draft.RegionY.ToString(CultureInfo.InvariantCulture);
            RoiWidthText.Text = draft.RegionWidth.ToString(CultureInfo.InvariantCulture);
            RoiHeightText.Text = draft.RegionHeight.ToString(CultureInfo.InvariantCulture);
            BackgroundXText.Text = draft.BackgroundX?.ToString(CultureInfo.InvariantCulture) ?? "";
            BackgroundYText.Text = draft.BackgroundY?.ToString(CultureInfo.InvariantCulture) ?? "";
            BackgroundWidthText.Text = draft.BackgroundWidth?.ToString(CultureInfo.InvariantCulture) ?? "";
            BackgroundHeightText.Text = draft.BackgroundHeight?.ToString(CultureInfo.InvariantCulture) ?? "";
            CalibrationXText.Text = draft.CalibrationX?.ToString(CultureInfo.InvariantCulture) ?? "";
            CalibrationYText.Text = draft.CalibrationY?.ToString(CultureInfo.InvariantCulture) ?? "";
            CalibrationUnitsText.Text = draft.CalibrationUnits;
            CalibrationSourceText.Text = draft.CalibrationSource;
            CalibrationStatus.SelectedItem = CalibrationStatus.Items.Cast<ComboBoxItem>()
                .FirstOrDefault(option => string.Equals(option.Content?.ToString(), draft.CalibrationStatus, StringComparison.Ordinal));
            SelectComboBoxValue(AdvancedFiltering, draft.AdvancedSettings?.Filtering ?? "none");
            SelectComboBoxValue(AdvancedDpc, draft.AdvancedSettings?.Dpc ?? "none");
            AdvancedSettingsStatus.Text = draft.AdvancedSettings is null || draft.AdvancedSettings.IsDefault
                ? "使用推荐默认值" : "已偏离推荐默认值";
        }
        finally { _configurationReady = true; }
    }

    private static void SelectComboBoxValue(ComboBox comboBox, string value) =>
        comboBox.SelectedItem = comboBox.Items.Cast<ComboBoxItem>()
            .FirstOrDefault(option => string.Equals(option.Content?.ToString(), value, StringComparison.Ordinal));

    private AnalysisRequest BuildRequestForItem(UiWorkspaceItem item, ConfigurationValues configuration)
    {
        if (item.Input is null)
            throw new ConfigurationValidationException("input_required", "The selected work item has no valid decoded input.");
        return AnalysisRequest.Create(
            item.Input.Path, item.Input.Sha256,
            configuration.RegionX, configuration.RegionY, configuration.RegionWidth, configuration.RegionHeight,
            configuration.BackgroundX, configuration.BackgroundY, configuration.BackgroundWidth, configuration.BackgroundHeight,
            configuration.CalibrationStatus, configuration.CalibrationX, configuration.CalibrationY,
            configuration.CalibrationUnits, configuration.CalibrationSource,
            Path.Combine(Path.GetTempPath(), "SpotAnalysis", "derived", item.Item.Id),
            configuration.AdvancedSettings);
    }

    private void LockRoiCheck_Click(object sender, RoutedEventArgs e)
    {
        if (LockRoiCheck.IsChecked == true && _currentItem is not null)
        {
            if (!_workspaceItems.LockRoiForSubsequent(_currentItem.Item.Id))
            {
                LockRoiCheck.IsChecked = false;
                StatusText.Text = "A valid ROI is required before locking it for later images.";
            }
        }
        else
        {
            _workspaceItems.UnlockRoi();
        }
    }

    private void ApplyCalibrationBatch_Click(object sender, RoutedEventArgs e)
    {
        if (_currentItem is null || !_workspaceItems.ApplyCalibrationToBatch(_currentItem.Item.Id))
        {
            StatusText.Text = "Only an explicitly confirmed calibration with values, units, and source can be applied to the batch.";
            return;
        }
        RefreshImageItemsList();
        ProjectCurrentItem();
        StatusText.Text = "Confirmed calibration applied to this temporary workspace; affected formal records need recalculation.";
    }

    private void StatusText_Changed(object sender, TextChangedEventArgs e) => UpdateStatusVisual();

    private void UpdateStatusVisual()
    {
        if (WorkflowStatusBadge is null || StatusText is null)
            return;

        var status = _workspace.State.WorkflowStatus;
        var (surface, border, foreground) = status switch
        {
            WorkspaceWorkflowStatus.InputInvalid or WorkspaceWorkflowStatus.ConfigurationInvalid
                or WorkspaceWorkflowStatus.Failed or WorkspaceWorkflowStatus.AnalysisFailed
                or WorkspaceWorkflowStatus.WorkerError or WorkspaceWorkflowStatus.ProtocolError
                => ("ColorInvalidSurface", "ColorInvalid", "ColorInvalid"),
            WorkspaceWorkflowStatus.NeedsRecalculation or WorkspaceWorkflowStatus.Stale
                or WorkspaceWorkflowStatus.TimedOut
                => ("ColorCautionSurface", "ColorCaution", "ColorCaution"),
            WorkspaceWorkflowStatus.Completed or WorkspaceWorkflowStatus.Exported
                or WorkspaceWorkflowStatus.PreviewAvailable
                => ("ColorSuccessSurface", "ColorSuccess", "ColorSuccess"),
            WorkspaceWorkflowStatus.Processing or WorkspaceWorkflowStatus.PreviewProcessing
                or WorkspaceWorkflowStatus.Formalizing or WorkspaceWorkflowStatus.Cancelling
                or WorkspaceWorkflowStatus.Cancelled
                => ("ColorInfoSurface", "ColorInfo", "ColorInfo"),
            _ => ("ColorPanelSubtle", "ColorBorder", "ColorInk"),
        };
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
            WorkspaceWorkflowStatus.Completed or WorkspaceWorkflowStatus.Exported => "Current formal analysis record is ready.",
            WorkspaceWorkflowStatus.PreviewAvailable => "Preview is ready; review ROI and confirm the formal result.",
            WorkspaceWorkflowStatus.PreviewProcessing => "Generating a non-exportable preview.",
            _ => "Review the configuration to continue.",
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
                draft.CalibrationUnits, draft.CalibrationSource, draft.AdvancedSettings));
        }
        catch (ConfigurationValidationException)
        {
            _workspace.StageInvalidDraft();
        }
        _flowStatus = _hasResult ? "needs_recalculation" : "configuration_changed";
        MarkResultStale();
        if (_inFlightRequest is null)
        {
            RefreshDraftSummary();
            StartPreviewForCurrentDraft();
        }
        UpdateRunAvailability();
    }

    private async void StartPreviewForCurrentDraft()
    {
        var item = _currentItem;
        if (item?.Input is null || item.Item.Presentation.IsProcessing) return;
        try
        {
            var draft = ReadConfiguration();
            var formal = BuildRequestForItem(item, draft);
            var preview = formal.AsPreview();
            var requestId = item.Item.Presentation.StartPreview(preview);
            if (requestId is null) return;
            item.InFlightRequest = preview;
            await RunScheduledAsync(item, preview, requestId, WorkspaceAnalysisPriority.Current, isPreview: true);
        }
        catch (ConfigurationValidationException) { }
    }

    private void DisplaySettingsChanged(object sender, SelectionChangedEventArgs e)
    {
        if (!_configurationReady || DisplayLayerCombo is null || DisplayColorCombo is null || DisplayRangeCombo is null) return;
        var layer = Enum.TryParse<DisplayLayer>((DisplayLayerCombo.SelectedItem as ComboBoxItem)?.Tag?.ToString(), out var parsedLayer)
            ? parsedLayer : DisplayLayer.Input;
        var colorMode = Enum.TryParse<DisplayColorMode>((DisplayColorCombo.SelectedItem as ComboBoxItem)?.Tag?.ToString(), out var parsedColor)
            ? parsedColor : DisplayColorMode.Grayscale;
        var rangeMode = Enum.TryParse<DisplayRangeMode>((DisplayRangeCombo.SelectedItem as ComboBoxItem)?.Tag?.ToString(), out var parsedRange)
            ? parsedRange : DisplayRangeMode.Percentile;
        _workspace.SetDisplaySettings(_workspace.Display with { Layer = layer, ColorMode = colorMode, RangeMode = rangeMode });
        RenderSelectedDisplay();
    }

    private void HideOverlays_Click(object sender, RoutedEventArgs e)
    {
        _workspace.SetDisplaySettings(_workspace.Display.HideOverlays());
        RenderSelectedDisplay();
    }

    private void RenderSelectedDisplay()
    {
        DisplayOverlayCanvas?.Children.Clear();
        var item = _currentItem;
        var outcome = item?.LastSuccessfulOutcome;
        var selectedLayer = _workspace.Display.Layer;
        if (item?.Input is null)
        {
            if (DisplayLayerStatusText is not null) DisplayLayerStatusText.Text = "显示图像 · N/A（未加载输入）";
            return;
        }
        if (selectedLayer == DisplayLayer.Input)
        {
            var inputValues = item.Input.IntensitySamples.Select(value => (double)value).ToArray();
            var renderedInput = inputValues.Length == item.Input.Width * item.Input.Height
                ? DisplayRenderer.Render((inputValues, item.Input.Width, item.Input.Height), _workspace.Display)
                : new DisplayRenderResult(item.Input.Preview, "input preview", true);
            InputPreview.Source = renderedInput.Image ?? item.Input.Preview;
            if (DisplayLayerStatusText is not null) DisplayLayerStatusText.Text = $"显示图像 · 输入图像 · {renderedInput.Message}";
            DrawDisplayOverlays(null, item.Input.Width, item.Input.Height);
            return;
        }
        if (outcome?.Result is not JsonElement result
            || DisplayProjectionReader.FromResult(result, item.Item.Presentation.CurrentRecord?.RecordId) is not { } projection)
        {
            InputPreview.Source = null;
            if (DisplayLayerStatusText is not null) DisplayLayerStatusText.Text = "显示图像 · N/A（当前记录尚未提供显示投影）";
            return;
        }

        var kind = selectedLayer switch
        {
            DisplayLayer.CorrectedIntensity => "corrected_intensity",
            DisplayLayer.PositiveSignal => "positive_intensity",
            DisplayLayer.Fit => "gaussian_fit",
            DisplayLayer.Residual => "fit_residual",
            DisplayLayer.MeasurementMask => "measurement_mask",
            DisplayLayer.CoreMask => "core_mask",
            _ => "input_image",
        };
        if (!projection.Assets.TryGetValue(kind, out var asset))
        {
            InputPreview.Source = null;
            if (DisplayLayerStatusText is not null) DisplayLayerStatusText.Text = $"显示图像 · N/A（{kind} 不可用）";
            DrawDisplayOverlays(projection.Record, item.Input.Width, item.Input.Height);
            return;
        }
        try
        {
            var path = new Uri(asset.Uri).LocalPath;
            var data = NpyDisplayReader.Read(path);
            if (data is null) throw new InvalidDataException("display_asset_invalid");
            var rendered = DisplayRenderer.Render(data.Value, _workspace.Display);
            InputPreview.Source = rendered.Image;
            if (DisplayLayerStatusText is not null)
                DisplayLayerStatusText.Text = rendered.IsAvailable ? $"显示图像 · {kind} · {rendered.Message}" : $"显示图像 · N/A（{rendered.Message}）";
            DrawDisplayOverlays(projection.Record, data.Value.Width, data.Value.Height);
        }
        catch (Exception exception) when (exception is IOException or InvalidDataException or UriFormatException)
        {
            InputPreview.Source = null;
            if (DisplayLayerStatusText is not null) DisplayLayerStatusText.Text = $"显示图像 · N/A（{exception.Message}）";
        }
    }

    private void DrawDisplayOverlays(JsonElement? record, int width, int height)
    {
        if (DisplayOverlayCanvas is null || !_workspace.Display.ShowOverlays || record is not { } node) return;
        var scaleX = DisplayOverlayCanvas.ActualWidth / Math.Max(1, width);
        var scaleY = DisplayOverlayCanvas.ActualHeight / Math.Max(1, height);
        if (_workspace.Display.ShowRoi && node.TryGetProperty("configuration", out var configuration)
            && configuration.TryGetProperty("region", out var roi) && roi.ValueKind == JsonValueKind.Object)
        {
            var rectangle = new Shapes.Rectangle { Stroke = Brushes.Gold, StrokeThickness = 2, StrokeDashArray = new DoubleCollection { 5, 3 } };
            Canvas.SetLeft(rectangle, Number(roi, "x") * scaleX); Canvas.SetTop(rectangle, Number(roi, "y") * scaleY);
            rectangle.Width = Number(roi, "width") * scaleX; rectangle.Height = Number(roi, "height") * scaleY;
            DisplayOverlayCanvas.Children.Add(rectangle);
        }
        if (_workspace.Display.ShowCenter && node.TryGetProperty("display_projection", out var projection)
            && projection.TryGetProperty("curves", out var curves) && curves.TryGetProperty("center_pixel", out var center)
            && center.ValueKind == JsonValueKind.Object)
        {
            var x = Number(center, "x") * scaleX; var y = Number(center, "y") * scaleY;
            var vertical = new Shapes.Line { X1 = x, X2 = x, Y1 = 0, Y2 = DisplayOverlayCanvas.ActualHeight, Stroke = Brushes.Cyan, StrokeThickness = 1 };
            var horizontal = new Shapes.Line { X1 = 0, X2 = DisplayOverlayCanvas.ActualWidth, Y1 = y, Y2 = y, Stroke = Brushes.Cyan, StrokeThickness = 1 };
            DisplayOverlayCanvas.Children.Add(vertical); DisplayOverlayCanvas.Children.Add(horizontal);
        }
    }

    private static double Number(JsonElement node, string property) =>
        node.TryGetProperty(property, out var value) && value.TryGetDouble(out var number) ? number : 0;

    private void CurveCanvas_SizeChanged(object sender, SizeChangedEventArgs e)
    {
        RenderSelectedDisplay();
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
        var recordKind = GetString(record, "record_kind") ?? "formal";
        var semantics = GetString(record, "measurement_semantics") ?? "relative_intensity_code";
        var configuration = record.TryGetProperty("configuration", out var configurationNode) ? configurationNode : default;
        var region = configuration.ValueKind == JsonValueKind.Object && configuration.TryGetProperty("region", out var regionNode)
            ? $"({GetNumber(regionNode, "x")}, {GetNumber(regionNode, "y")}, {GetNumber(regionNode, "width")}, {GetNumber(regionNode, "height")})"
            : "unknown";
        var calibration = configuration.ValueKind == JsonValueKind.Object && configuration.TryGetProperty("calibration", out var calibrationNode)
            ? $"{GetNumber(calibrationNode, "x_unit_per_pixel")} × {GetNumber(calibrationNode, "y_unit_per_pixel")} {GetString(calibrationNode, "physical_unit") ?? ""} ({GetString(calibrationNode, "confirmation") ?? "missing"})"
            : "unknown";
        return $"Input: {inputIdentity}\nRecord: {GetString(record, "record_id") ?? outcome.RecordId}\nKind: {recordKind}; semantics: {semantics}\nFingerprint: {GetString(record, "analysis_fingerprint") ?? outcome.AnalysisFingerprint}\nFlow status: {flowStatus}; measurement validity: {summaryStatus}\nCalibration: {calibration}\nAnalysis region: {region}";
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
        var dialog = new SaveFileDialog
        {
            Filter = "PNG report (*.png)|*.png|PDF report (*.pdf)|*.pdf",
            DefaultExt = ".png",
            AddExtension = true,
            OverwritePrompt = false,
            FileName = (requestedName.Length == 0 ? "焦斑分析报告" : ReportExporter.SanitizeName(requestedName)) + ".png",
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

    private void ResetAdvancedSettings_Click(object sender, RoutedEventArgs e)
    {
        AdvancedFiltering.SelectedValue = "none";
        AdvancedDpc.SelectedValue = "none";
        AdvancedSettingsStatus.Text = "Using recommended defaults";
        ConfigurationChanged(sender, e);
    }

    protected override void OnClosed(EventArgs e)
    {
        _scheduler.Dispose();
        base.OnClosed(e);
    }

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
