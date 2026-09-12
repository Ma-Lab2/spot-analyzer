using System.IO;
using System.IO.Compression;
using System.Reflection;
using System.Text.Json;
using System.Text.Json.Serialization;

namespace SpotAnalysis.App;

public sealed record DiagnosticSnapshot(
    string FlowStatus,
    string? FailureCode,
    string? FailureDetails,
    string? RecordId,
    string? AnalysisFingerprint,
    string? InputSummary,
    string? InputSha256,
    string? InputPath,
    object? Configuration,
    object? Result,
    IReadOnlyList<JsonElement> Diagnostics,
    string? MeasurementValidity,
    IReadOnlyList<string> QualityReasonCodes);

public static class DiagnosticLog
{
    private static readonly object Gate = new();

    public static string DirectoryPath => Path.Combine(
        Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
        "SpotAnalysis", "logs");

    public static string FilePath => Path.Combine(DirectoryPath, "app.log");

    public static void Write(string eventName, object? details = null)
    {
        try
        {
            var entry = new Dictionary<string, object?>
            {
                ["timestamp_utc"] = DateTimeOffset.UtcNow,
                ["event"] = eventName,
                ["client_version"] = DiagnosticPackage.ClientVersion,
                ["details"] = details,
            };
            var line = JsonSerializer.Serialize(entry, DiagnosticPackage.JsonOptions);
            lock (Gate)
            {
                Directory.CreateDirectory(DirectoryPath);
                File.AppendAllText(FilePath, line + Environment.NewLine);
            }
        }
        catch
        {
            // Diagnostics must never prevent the analysis workflow from continuing.
        }
    }
}

public static class BuildIdentity
{
    private static readonly JsonDocument Document = Load();
    private static readonly string[] RequiredDependencies = ["numpy", "scipy", "Pillow", "rfc8785"];

    static BuildIdentity()
    {
        if (PackageVersion != ClientVersion)
            throw new InvalidOperationException("Build identity package and client versions do not match.");
    }

    public static string PackageVersion => RequiredString("package_version");
    public static string ClientVersion => RequiredString("client_version");
    public static string WorkerVersion => RequiredString("worker_version");
    public static string WorkerPackaging => RequiredString("worker_packaging");
    public static string PyInstallerVersion => RequiredString("pyinstaller_version");
    public static string AnalysisCoreVersion => RequiredString("analysis_core_version");
    public static string StandardProfileVersion => RequiredString("standard_profile_version");
    public static string QualityProfileVersion => RequiredString("quality_profile_version");
    public static IReadOnlyDictionary<string, string> WorkerDependencies =>
        RequiredDependencies.ToDictionary(name => name, name => RequiredDependency(name));

    private static JsonDocument Load()
    {
        var path = Path.Combine(AppContext.BaseDirectory, "build-identity.json");
        if (!File.Exists(path))
            throw new InvalidOperationException($"Build identity is missing: {path}");
        return JsonDocument.Parse(File.ReadAllText(path));
    }

    private static string RequiredString(string name) =>
        Document.RootElement.TryGetProperty(name, out var value) && value.ValueKind == JsonValueKind.String
            && !string.IsNullOrWhiteSpace(value.GetString())
            ? value.GetString()!
            : throw new InvalidOperationException($"Build identity field '{name}' is missing.");

    private static string RequiredDependency(string name)
    {
        if (!Document.RootElement.TryGetProperty("worker_dependencies", out var dependencies)
            || dependencies.ValueKind != JsonValueKind.Object
            || !dependencies.TryGetProperty(name, out var value)
            || value.ValueKind != JsonValueKind.String || string.IsNullOrWhiteSpace(value.GetString()))
            throw new InvalidOperationException($"Build identity worker dependency '{name}' is missing.");
        return value.GetString()!;
    }
}

public static class DiagnosticPackage
{
    public const string Schema = "spot-analysis-diagnostic-v1";
    public static string ClientVersion => BuildIdentity.ClientVersion;
    public static string WorkerVersion => BuildIdentity.WorkerVersion;
    public static string AnalysisCoreVersion => BuildIdentity.AnalysisCoreVersion;
    public static string StandardProfileVersion => BuildIdentity.StandardProfileVersion;
    public static string QualityProfileVersion => BuildIdentity.QualityProfileVersion;

    internal static readonly JsonSerializerOptions JsonOptions = new()
    {
        WriteIndented = true,
        DefaultIgnoreCondition = JsonIgnoreCondition.WhenWritingNull,
    };

    public static string OutputCapability => "JSON diagnostic package; optional ZIP attachment";

    public static string BuildAboutText()
    {
        var worker = Environment.GetEnvironmentVariable("SPOT_ANALYSIS_WORKER");
        var workerLocation = string.IsNullOrWhiteSpace(worker) ? "beside client (packaged)" : worker;
        var dependencies = string.Join(", ", BuildIdentity.WorkerDependencies.Select(item => $"{item.Key} {item.Value}"));
        return $"Client {ClientVersion} (package {BuildIdentity.PackageVersion})\nWorker {WorkerVersion}, PyInstaller {BuildIdentity.PyInstallerVersion}: {workerLocation}\n" +
               $"Worker dependencies: {dependencies}\nAnalysis core {AnalysisCoreVersion}\nProfiles: {StandardProfileVersion}, {QualityProfileVersion} (provisional)\n" +
               $"Logs: {DiagnosticLog.DirectoryPath}\nOutput: {OutputCapability}";
    }

    public static void Write(string path, DiagnosticSnapshot snapshot, bool includeOriginalImage)
    {
        if (string.IsNullOrWhiteSpace(path))
            throw new ArgumentException("A diagnostic output path is required.", nameof(path));

        var directory = Path.GetDirectoryName(Path.GetFullPath(path));
        if (!string.IsNullOrEmpty(directory)) Directory.CreateDirectory(directory);
        var payload = BuildPayload(snapshot, includeOriginalImage);
        if (includeOriginalImage)
        {
            if (string.IsNullOrWhiteSpace(snapshot.InputPath) || !File.Exists(snapshot.InputPath))
                throw new FileNotFoundException("The selected original input image is not available.", snapshot.InputPath);
            WriteZip(path, payload, snapshot.InputPath);
        }
        else
        {
            File.WriteAllText(path, JsonSerializer.Serialize(payload, JsonOptions));
        }
    }

    private static Dictionary<string, object?> BuildPayload(DiagnosticSnapshot snapshot, bool includeOriginalImage) =>
        new()
        {
            ["schema"] = Schema,
            ["created_at_utc"] = DateTimeOffset.UtcNow,
            ["client"] = new Dictionary<string, object?>
            {
                ["version"] = ClientVersion,
                ["assembly"] = Assembly.GetEntryAssembly()?.GetName().Name,
            },
            ["worker"] = new Dictionary<string, object?>
            {
                ["version"] = WorkerVersion,
                ["packaging"] = BuildIdentity.WorkerPackaging,
                ["pyinstaller_version"] = BuildIdentity.PyInstallerVersion,
                ["dependencies"] = BuildIdentity.WorkerDependencies,
                ["executable"] = Environment.GetEnvironmentVariable("SPOT_ANALYSIS_WORKER") ?? "packaged beside client",
            },
            ["analysis"] = new Dictionary<string, object?>
            {
                ["core_version"] = AnalysisCoreVersion,
                ["standard_profile"] = StandardProfileVersion,
                ["quality_profile"] = QualityProfileVersion,
                ["profile_validation"] = "provisional",
            },
            ["input"] = new Dictionary<string, object?>
            {
                ["summary"] = snapshot.InputSummary,
                ["sha256"] = snapshot.InputSha256,
                ["image_attached"] = includeOriginalImage,
            },
            ["configuration"] = snapshot.Configuration,
            ["flow_status"] = snapshot.FlowStatus,
            ["measurement_validity"] = snapshot.MeasurementValidity,
            ["quality_reason_codes"] = snapshot.QualityReasonCodes,
            ["record_id"] = snapshot.RecordId,
            ["analysis_fingerprint"] = snapshot.AnalysisFingerprint,
            ["result"] = snapshot.Result,
            ["diagnostics"] = snapshot.Diagnostics,
            ["failure"] = string.IsNullOrWhiteSpace(snapshot.FailureCode) && string.IsNullOrWhiteSpace(snapshot.FailureDetails)
                ? null
                : new Dictionary<string, object?> { ["code"] = snapshot.FailureCode, ["details"] = snapshot.FailureDetails },
            ["limitations"] = new[]
            {
                "Diagnostic context does not establish physical-accuracy validation.",
                "standard-profile-v1 and quality-profile-v1 remain provisional.",
            },
        };

    private static void WriteZip(string path, Dictionary<string, object?> payload, string inputPath)
    {
        if (File.Exists(path)) File.Delete(path);
        using var archive = ZipFile.Open(path, ZipArchiveMode.Create);
        var diagnostics = archive.CreateEntry("diagnostics.json", CompressionLevel.Fastest);
        using (var writer = new StreamWriter(diagnostics.Open()))
            writer.Write(JsonSerializer.Serialize(payload, JsonOptions));
        var image = archive.CreateEntry("input/" + Path.GetFileName(inputPath), CompressionLevel.NoCompression);
        using var source = File.OpenRead(inputPath);
        using var destination = image.Open();
        source.CopyTo(destination);
    }
}
