using System.Security.Cryptography;
using System.Windows;
using Microsoft.Win32;

namespace SpotAnalysis.App;

public partial class MainWindow : Window
{
    private readonly WorkerClient _workerClient = new();
    private CancellationTokenSource? _analysisCancellation;

    public MainWindow() => InitializeComponent();

    private async void RunAnalysis_Click(object sender, RoutedEventArgs e)
    {
        await RunAnalysisAsync(() => _workerClient.RunSyntheticAsync(_analysisCancellation!.Token, TimeSpan.FromSeconds(30)));
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
        RecordText.Text = "";
        try
        {
            var outcome = await operation();
            StatusText.Text = outcome.Status switch
            {
                "success" => "Completed",
                "cancelled" => "Cancelled",
                "timeout" => "Timed out",
                _ => $"Failed ({outcome.FailureCode ?? "worker_failure"}): {outcome.ErrorMessage}",
            };
        }
        finally
        {
            _analysisCancellation.Dispose();
            _analysisCancellation = null;
        }
    }

    private async void OpenPng_Click(object sender, RoutedEventArgs e)
    {
        var dialog = new OpenFileDialog { Filter = "PNG files (*.png)|*.png", CheckFileExists = true, Multiselect = false };
        if (dialog.ShowDialog() != true) return;
        await RunAnalysisAsync(async () =>
        {
            await using var stream = File.OpenRead(dialog.FileName);
            var hash = Convert.ToHexString(await SHA256.HashDataAsync(stream));
            var outcome = await _workerClient.RunPngAsync(dialog.FileName, hash, true, _analysisCancellation!.Token, TimeSpan.FromSeconds(30));
            RecordText.Text = outcome.Status == "success"
                ? $"Input: {outcome.InputSummary}\nRecord: {outcome.RecordId}\nFingerprint: {outcome.AnalysisFingerprint}"
                : "";
            return outcome;
        });
    }
}
