using System.Windows;

namespace SpotAnalysis.App;

public partial class MainWindow : Window
{
    private readonly WorkerClient _workerClient = new();

    public MainWindow()
    {
        InitializeComponent();
    }

    private async void RunAnalysis_Click(object sender, RoutedEventArgs e)
    {
        StatusText.Text = "Processing";
        try
        {
            var outcome = await _workerClient.RunSyntheticAsync(CancellationToken.None);
            StatusText.Text = outcome.Status == "success"
                ? "Completed"
                : $"Failed: {outcome.ErrorMessage}";
        }
        catch (Exception exception)
        {
            StatusText.Text = $"Failed: {exception.Message}";
        }
    }
}
