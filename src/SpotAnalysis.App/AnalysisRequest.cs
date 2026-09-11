using System.Text.Json;

namespace SpotAnalysis.App;

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
        string outputDirectory)
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
                ["preprocessing"] = new Dictionary<string, object?>
                {
                    ["background_source"] = "confirmed_region_affine",
                    ["bad_pixel_policy"] = "mask_only",
                    ["negative_value_policy"] = "preserve_signed",
                    ["filtering"] = "none",
                    ["dpc"] = "none",
                    ["advanced_processing_enabled"] = false,
                    ["background_signal_sigma_threshold"] = 3.0,
                    ["background_signal_peak_fraction"] = 0.10,
                    ["background_mask_dilation_pixels"] = 1,
                    ["background_huber_delta"] = 1.345,
                    ["background_max_iterations"] = 50,
                    ["convergence_tolerance"] = 1e-8,
                    ["localization_sigma_pixels"] = 1.0,
                    ["localization_truncate_sigma"] = 3.0,
                    ["core_threshold_fraction"] = 0.5,
                    ["core_invalid_fraction"] = 0.8,
                    ["core_caution_fraction"] = 0.95,
                    ["snr_invalid_threshold"] = 5.0,
                    ["snr_caution_threshold"] = 10.0,
                    ["multiple_peak_relative_threshold"] = 0.20,
                    ["multiple_peak_noise_threshold"] = 5.0,
                    ["multiple_peak_min_support_pixels"] = 9,
                    ["multiple_peak_min_separation_pixels"] = 3.0,
                    ["advanced_interpolation_sigma_pixels"] = 1.0,
                    ["advanced_interpolation_radius_pixels"] = 2,
                    ["advanced_filter_sigma_pixels"] = 1.0,
                    ["advanced_filter_radius_pixels"] = 3,
                    ["advanced_dpc_sigma_pixels"] = 2.0,
                    ["advanced_dpc_radius_pixels"] = 6,
                    ["version"] = "preprocessing-v1",
                },
                ["model"] = new Dictionary<string, object?>
                {
                    ["name"] = "rotated_elliptical_gaussian",
                    ["sigma_min_pixels"] = 0.5,
                    ["sigma_max_roi_fraction"] = 0.5,
                    ["fallback_sigma_roi_fraction"] = 1.0 / 6.0,
                    ["optimizer"] = "bounded-least-squares-trf",
                    ["optimizer_tolerance"] = 1e-12,
                    ["optimizer_max_evaluations"] = 1000,
                    ["version"] = "gaussian-model-v1",
                },
                ["rref_pixels"] = null,
                ["analysis_contract"] = "analysis-contract-v1",
                ["standard_profile"] = "standard-profile-v1",
                ["quality_profile"] = "quality-profile-v1",
                ["profile_validation"] = "provisional",
                ["algorithm_version"] = "analysis-core-v1",
                ["bad_pixel_coordinates"] = Array.Empty<object>(),
                ["bad_pixel_mask_version"] = "bad-pixel-mask-v1",
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

    public Dictionary<string, object?> ToWorkerPayload()
    {
        var payload = JsonSerializer.Deserialize<Dictionary<string, object?>>(_payloadJson, JsonOptions);
        return payload ?? throw new InvalidOperationException("Analysis request snapshot could not be deserialized.");
    }
}
