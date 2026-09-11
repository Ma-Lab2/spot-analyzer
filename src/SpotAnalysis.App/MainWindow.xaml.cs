using System.Globalization;
using System.Windows;
using Microsoft.Win32;

namespace SpotAnalysis.App;

public partial class MainWindow : Window
{
    private readonly WorkerClient _workerClient = new();
    private PngInputInfo? _selectedInput;
    private bool _configurationConfirmed;
    private bool _hasResult;
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
                    RecordText.Text = $"Input: {_selectedInput?.Summary ?? outcome.InputSummary}\nRecord: {outcome.RecordId}\nFingerprint: {outcome.AnalysisFingerprint}";
                    _hasResult = true;
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

    private void ConfigurationSelectionChanged(object sender, System.Windows.Controls.SelectionChangedEventArgs e) =>
        ConfigurationChanged(sender, e);

    private void MarkResultStale()
    {
        if (_hasResult)
            RecordText.Text = "Previous result is stale; run analysis again for the current input and configuration.";
    }
}

public sealed class ConfigurationValidationException(string code, string message) : Exception(message)
{
    public string Code { get; } = code;
}
