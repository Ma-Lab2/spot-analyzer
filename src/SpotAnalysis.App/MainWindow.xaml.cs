using System.Security.Cryptography;
using System.Windows;
using Microsoft.Win32;

namespace SpotAnalysis.App;

public partial class MainWindow : Window
{
    private readonly WorkerClient _workerClient = new();

    public MainWindow() => InitializeComponent();

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
            await using var stream = File.OpenRead(dialog.FileName);
            var hash = Convert.ToHexString(await SHA256.HashDataAsync(stream));
            var outcome = await _workerClient.RunPngAsync(dialog.FileName, hash, true, CancellationToken.None);
            StatusText.Text = outcome.Status == "success" ? "Completed" : $"Failed: {outcome.ErrorMessage}";
            RecordText.Text = outcome.Status == "success"
                ? $"Input: {outcome.InputSummary}\nRecord: {outcome.RecordId}\nFingerprint: {outcome.AnalysisFingerprint}"
                : "";
        }
        catch (Exception exception) { StatusText.Text = $"Failed: {exception.Message}"; RecordText.Text = ""; }
    }
}
