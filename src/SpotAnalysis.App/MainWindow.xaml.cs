using System.Globalization;
using System.Security.Cryptography;
using System.Windows;
using System.Windows.Controls;
using Microsoft.Win32;

namespace SpotAnalysis.App;

public partial class MainWindow : Window
{
    private readonly WorkerClient _workerClient = new();
    private bool _configurationConfirmed;

    public MainWindow() => InitializeComponent();

    private static int Integer(TextBox field) => int.Parse(field.Text, CultureInfo.InvariantCulture);
    private static double Number(TextBox field) => double.Parse(field.Text, CultureInfo.InvariantCulture);

    private (object calibration, object roi, object? background) ReadConfiguration()
    {
        var status = ((System.Windows.Controls.ComboBoxItem)CalibrationStatus.SelectedItem).Content.ToString()!;
        object calibration = status == "missing"
            ? new { status }
            : new { status, x_units_per_pixel = Number(CalibrationXText), y_units_per_pixel = Number(CalibrationYText), units = CalibrationUnitsText.Text, source = CalibrationSourceText.Text };
        object roi = new { x = Integer(RoiXText), y = Integer(RoiYText), width = Integer(RoiWidthText), height = Integer(RoiHeightText) };
        object? background = string.IsNullOrWhiteSpace(BackgroundWidthText.Text) ? null : new { x = Integer(BackgroundXText), y = Integer(BackgroundYText), width = Integer(BackgroundWidthText), height = Integer(BackgroundHeightText) };
        return (calibration, roi, background);
    }

    private void ConfirmConfiguration_Click(object sender, RoutedEventArgs e)
    {
        try { _ = ReadConfiguration(); _configurationConfirmed = true; StatusText.Text = "Configuration confirmed"; }
        catch (Exception exception) { _configurationConfirmed = false; StatusText.Text = $"Invalid configuration: {exception.Message}"; }
    }

    private async void RunAnalysis_Click(object sender, RoutedEventArgs e)
    {
        StatusText.Text = "Processing";
        try
        {
            var outcome = await _workerClient.RunSyntheticAsync(CancellationToken.None);
            StatusText.Text = outcome.Status == "success" ? "Completed" : $"Failed: {outcome.ErrorMessage}";
        }
        catch (Exception exception) { StatusText.Text = $"Failed: {exception.Message}"; }
    }

    private async void OpenPng_Click(object sender, RoutedEventArgs e)
    {
        var dialog = new OpenFileDialog { Filter = "PNG files (*.png)|*.png", CheckFileExists = true, Multiselect = false };
        if (dialog.ShowDialog() != true) return;
        StatusText.Text = "Processing";
        try
        {
            if (!_configurationConfirmed) throw new InvalidOperationException("Confirm calibration and analysis region before running analysis.");
            await using var stream = File.OpenRead(dialog.FileName);
            var hash = Convert.ToHexString(await SHA256.HashDataAsync(stream));
            var configuration = ReadConfiguration();
            var outcome = await _workerClient.RunPngAsync(dialog.FileName, hash, true, configuration.calibration, configuration.roi, configuration.background, CancellationToken.None);
            StatusText.Text = outcome.Status == "success" ? "Completed" : $"Failed: {outcome.ErrorMessage}";
            RecordText.Text = outcome.Status == "success"
                ? $"Input: {outcome.InputSummary}\nRecord: {outcome.RecordId}\nFingerprint: {outcome.AnalysisFingerprint}"
                : "";
        }
        catch (Exception exception) { StatusText.Text = $"Failed: {exception.Message}"; RecordText.Text = ""; }
    }
}
