using System.Text.Json;
using System.Text.Json.Nodes;

namespace SpotAnalysis.App;

/// <summary>
/// Optional, explicitly supported exploratory settings. Null means use the
/// Python profile recommendation; it is deliberately not populated with
/// scientific defaults in the WPF client.
/// </summary>
public sealed record AdvancedAnalysisSettings(
    string? BadPixelPolicy = null,
    string? Filtering = null,
    string? Dpc = null,
    double? InterpolationSigmaPixels = null,
    int? InterpolationRadiusPixels = null,
    double? FilterSigmaPixels = null,
    int? FilterRadiusPixels = null,
    double? DpcSigmaPixels = null,
    int? DpcRadiusPixels = null)
{
    public bool IsDefault => BadPixelPolicy is null && Filtering is null && Dpc is null
        && InterpolationSigmaPixels is null && InterpolationRadiusPixels is null
        && FilterSigmaPixels is null && FilterRadiusPixels is null
        && DpcSigmaPixels is null && DpcRadiusPixels is null;

    public Dictionary<string, object?> ToPayload() => new Dictionary<string, object?>
    {
        ["bad_pixel_policy"] = BadPixelPolicy,
        ["filtering"] = Filtering,
        ["dpc"] = Dpc,
        ["advanced_interpolation_sigma_pixels"] = InterpolationSigmaPixels,
        ["advanced_interpolation_radius_pixels"] = InterpolationRadiusPixels,
        ["advanced_filter_sigma_pixels"] = FilterSigmaPixels,
        ["advanced_filter_radius_pixels"] = FilterRadiusPixels,
        ["advanced_dpc_sigma_pixels"] = DpcSigmaPixels,
        ["advanced_dpc_radius_pixels"] = DpcRadiusPixels,
    }.Where(pair => pair.Value is not null).ToDictionary(pair => pair.Key, pair => pair.Value);
}

/// <summary>
/// Immutable snapshot of everything that is sent to the analysis worker.
/// The summary is serialized from the same payload used by WorkerClient.
/// </summary>
public sealed record AnalysisRequest
{
    private static readonly JsonSerializerOptions JsonOptions = new()
    {
        PropertyNamingPolicy = JsonNamingPolicy.SnakeCaseLower,
        WriteIndented = true,
    };

    private readonly string _payloadJson;

    private AnalysisRequest(string payloadJson)
    {
        _payloadJson = payloadJson;
    }

    public string Summary => _payloadJson;

    public bool IsValid { get; init; } = true;

    public string? ValidationError { get; init; }

    public static AnalysisRequest Create(
        string path,
        string sha256,
        int regionX,
        int regionY,
        int regionWidth,
        int regionHeight,
        int? backgroundX,
        int? backgroundY,
        int? backgroundWidth,
        int? backgroundHeight,
        string calibrationStatus,
        double? calibrationX,
        double? calibrationY,
        string calibrationUnits,
        string calibrationSource,
        string outputDirectory,
        AdvancedAnalysisSettings? advancedSettings = null)
    {
        Dictionary<string, object?>? background = null;
        if (backgroundX.HasValue && backgroundY.HasValue && backgroundWidth.HasValue && backgroundHeight.HasValue)
        {
            background = new Dictionary<string, object?>
            {
                ["x"] = backgroundX.Value,
                ["y"] = backgroundY.Value,
                ["width"] = backgroundWidth.Value,
                ["height"] = backgroundHeight.Value,
            };
        }

        var payload = new Dictionary<string, object?>
        {
            ["workflow"] = new Dictionary<string, object?> { ["kind"] = "formal" },
            ["record_kind"] = "formal",
            ["input"] = new Dictionary<string, object?>
            {
                ["asset"] = new Dictionary<string, object?>
                {
                    ["path"] = path,
                    ["expected_sha256"] = sha256,
                },
                ["confirm_relative_intensity"] = true,
            },
            ["configuration"] = new Dictionary<string, object?>
            {
                ["region"] = new Dictionary<string, object?>
                {
                    ["x"] = regionX, ["y"] = regionY, ["width"] = regionWidth, ["height"] = regionHeight,
                },
                ["background_region"] = background,
                ["calibration"] = new Dictionary<string, object?>
                {
                    ["x_unit_per_pixel"] = calibrationX,
                    ["y_unit_per_pixel"] = calibrationY,
                    ["physical_unit"] = calibrationUnits,
                    ["source"] = calibrationSource,
                    ["confirmation"] = calibrationStatus,
                },
                // Profile defaults and all fixed scientific parameters are
                // resolved by the Python worker. WPF only sends user intent.
                ["preprocessing"] = advancedSettings?.ToPayload(),
                ["model"] = null,
                ["rref_pixels"] = null,
                ["analysis_contract"] = "analysis-contract-v1",
                ["standard_profile"] = "standard-profile-v1",
                ["quality_profile"] = "quality-profile-v1",
                ["profile_validation"] = "provisional",
                ["algorithm_version"] = "analysis-core-v1",
                ["bad_pixel_coordinates"] = Array.Empty<object>(),
                ["bad_pixel_mask_version"] = "bad-pixel-mask-v1",
                ["automatic_background"] = true,
                ["record_kind"] = "formal",
                ["measurement_semantics"] = "relative_intensity_code",
                ["measurement_semantics_confirmed"] = true,
                ["advanced_settings_status"] = advancedSettings is null
                    ? "using_recommended_defaults"
                    : advancedSettings.IsDefault ? "using_recommended_defaults" : "deviated_from_recommended_defaults",
            },
            ["output_strategy"] = new Dictionary<string, object?>
            {
                ["work_directory"] = outputDirectory,
                ["derived_format"] = "npy",
            },
        };

        var json = JsonSerializer.Serialize(payload, JsonOptions);
        return new AnalysisRequest(json);
    }

    public static AnalysisRequest CreatePreview(
        string path,
        string sha256,
        string outputDirectory)
    {
        var payload = new Dictionary<string, object?>
        {
            ["workflow"] = new Dictionary<string, object?> { ["kind"] = "preview" },
            ["record_kind"] = "preview",
            ["input"] = new Dictionary<string, object?>
            {
                ["asset"] = new Dictionary<string, object?> { ["path"] = path, ["expected_sha256"] = sha256 },
                // The preview uses the adapter's supported PNG interpretation;
                // the final request repeats this as an explicit confirmation.
                ["confirm_relative_intensity"] = false,
            },
            ["configuration"] = new Dictionary<string, object?>
            {
                ["region"] = null,
                ["background_region"] = null,
                ["calibration"] = new Dictionary<string, object?>
                {
                    ["x_unit_per_pixel"] = null, ["y_unit_per_pixel"] = null,
                    ["physical_unit"] = null, ["source"] = null, ["confirmation"] = "missing",
                },
                ["preprocessing"] = null, ["model"] = null, ["automatic_background"] = true,
                ["record_kind"] = "preview", ["measurement_semantics"] = "relative_intensity_code",
                ["measurement_semantics_confirmed"] = false,
            },
            ["output_strategy"] = new Dictionary<string, object?>
            {
                ["work_directory"] = outputDirectory, ["derived_format"] = "npy",
            },
        };
        return new AnalysisRequest(JsonSerializer.Serialize(payload, JsonOptions));
    }

    public AnalysisRequest AsPreview()
    {
        var root = JsonNode.Parse(_payloadJson)?.AsObject()
            ?? throw new InvalidOperationException("Analysis request snapshot could not be parsed.");
        root["workflow"] = new JsonObject { ["kind"] = "preview" };
        root["record_kind"] = "preview";
        if (root["input"] is JsonObject input)
            input["confirm_relative_intensity"] = false;
        if (root["configuration"] is JsonObject configuration)
        {
            configuration["record_kind"] = "preview";
            configuration["measurement_semantics_confirmed"] = false;
        }
        return new AnalysisRequest(root.ToJsonString(JsonOptions));
    }

    public Dictionary<string, object?> ToWorkerPayload()
    {
        var payload = JsonSerializer.Deserialize<Dictionary<string, object?>>(_payloadJson, JsonOptions);
        return payload ?? throw new InvalidOperationException("Analysis request snapshot could not be deserialized.");
    }
}
