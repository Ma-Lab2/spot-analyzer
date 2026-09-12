using System.Globalization;
using System.Text;
using System.Text.Json;
using System.Text.RegularExpressions;
using System.Windows;
using System.Windows.Media;
using System.Windows.Media.Imaging;

namespace SpotAnalysis.App;

public enum DisplayLayer
{
    Input,
    CorrectedIntensity,
    PositiveSignal,
    Fit,
    Residual,
    MeasurementMask,
    CoreMask,
}

public enum DisplayColorMode { Grayscale, Pseudocolor }
public enum DisplayRangeMode { Percentile, Full, Fixed }

public sealed record DisplaySettings(
    DisplayLayer Layer = DisplayLayer.Input,
    DisplayColorMode ColorMode = DisplayColorMode.Grayscale,
    DisplayRangeMode RangeMode = DisplayRangeMode.Percentile,
    double FixedMinimum = 0,
    double FixedMaximum = 1,
    bool ShowCenter = true,
    bool ShowRoi = true,
    bool ShowAxes = true,
    bool ShowUnits = true,
    bool ShowLegend = true)
{
    public DisplaySettings HideOverlays() => this with
    {
        ShowCenter = false,
        ShowRoi = false,
        ShowAxes = false,
        ShowUnits = false,
        ShowLegend = false,
    };
}

public sealed record DisplayAsset(string Kind, string RecordId, string Uri, string Sha256, string Format);

public sealed record DisplayProjectionSnapshot(
    string RecordId,
    string AnalysisFingerprint,
    IReadOnlyDictionary<string, DisplayAsset> Assets,
    JsonElement Record);

public sealed record DisplayRenderResult(BitmapSource? Image, string Message, bool IsAvailable);

public static class DisplayProjectionReader
{
    public static DisplayProjectionSnapshot? FromResult(JsonElement result, string? expectedRecordId = null)
    {
        if (!result.TryGetProperty("record", out var record) || record.ValueKind != JsonValueKind.Object)
            return null;
        var recordId = ReadString(record, "record_id");
        var fingerprint = ReadString(record, "analysis_fingerprint");
        if (string.IsNullOrWhiteSpace(recordId) || string.IsNullOrWhiteSpace(fingerprint)) return null;
        if (expectedRecordId is not null && !string.Equals(expectedRecordId, recordId, StringComparison.Ordinal)) return null;
        if (record.TryGetProperty("display_projection", out var projection)
            && projection.ValueKind == JsonValueKind.Object
            && (ReadString(projection, "schema") != "display-projection-v1"
                || ReadString(projection, "record_id") != recordId))
            return null;
        var assets = new Dictionary<string, DisplayAsset>(StringComparer.Ordinal);
        if (record.TryGetProperty("derived_assets", out var nodes) && nodes.ValueKind == JsonValueKind.Array)
        {
            foreach (var node in nodes.EnumerateArray())
            {
                var kind = ReadString(node, "kind");
                var assetRecordId = ReadString(node, "record_id");
                var uri = ReadString(node, "uri");
                var hash = ReadString(node, "sha256");
                var format = ReadString(node, "format");
                if (kind is null || assetRecordId is null || uri is null || hash is null || format is null) continue;
                if (!string.Equals(recordId, assetRecordId, StringComparison.Ordinal)) continue;
                assets[kind] = new DisplayAsset(kind, assetRecordId, uri, hash, format);
            }
        }
        return new DisplayProjectionSnapshot(recordId, fingerprint, assets, record.Clone());
    }

    private static string? ReadString(JsonElement node, string property) =>
        node.ValueKind == JsonValueKind.Object && node.TryGetProperty(property, out var value)
        && value.ValueKind == JsonValueKind.String ? value.GetString() : null;
}

public static class NpyDisplayReader
{
    private static readonly Regex HeaderShape = new(@"'shape'\s*:\s*\(([^)]*)\)", RegexOptions.Compiled);
    private static readonly Regex HeaderDescr = new(@"'descr'\s*:\s*'([^']+)'", RegexOptions.Compiled);

    public static (double[] Values, int Width, int Height)? Read(string path)
    {
        using var stream = File.OpenRead(path);
        using var reader = new BinaryReader(stream, Encoding.ASCII, leaveOpen: false);
        if (reader.ReadByte() != 0x93 || Encoding.ASCII.GetString(reader.ReadBytes(5)) != "NUMPY")
            throw new InvalidDataException("display_asset_invalid_npy");
        var major = reader.ReadByte();
        var minor = reader.ReadByte();
        var headerLength = major == 1 ? reader.ReadUInt16() : reader.ReadUInt32();
        var header = Encoding.ASCII.GetString(reader.ReadBytes(checked((int)headerLength)));
        var descr = HeaderDescr.Match(header).Groups[1].Value;
        var shapeText = HeaderShape.Match(header).Groups[1].Value.Split(',', StringSplitOptions.RemoveEmptyEntries | StringSplitOptions.TrimEntries);
        if (shapeText.Length != 2 || !int.TryParse(shapeText[0], out var height) || !int.TryParse(shapeText[1], out var width) || height <= 0 || width <= 0)
            throw new InvalidDataException("display_asset_shape_invalid");
        if (descr is not "<f8" and not "<f4" and not "|b1" and not "|u1")
            throw new InvalidDataException($"display_asset_dtype_unsupported:{descr}");
        var values = new double[checked(width * height)];
        for (var i = 0; i < values.Length; i++)
            values[i] = descr switch
            {
                "<f8" => reader.ReadDouble(),
                "<f4" => reader.ReadSingle(),
                "|b1" => reader.ReadBoolean() ? 1 : 0,
                "|u1" => reader.ReadByte(),
                _ => throw new InvalidDataException("display_asset_dtype_unsupported"),
            };
        return (values, width, height);
    }
}

public static class DisplayRenderer
{
    public static DisplayRenderResult Render((double[] Values, int Width, int Height) data, DisplaySettings settings)
    {
        var finite = data.Values.Where(double.IsFinite).OrderBy(value => value).ToArray();
        if (finite.Length == 0) return new(null, "Layer unavailable (no finite display values).", false);
        var minimum = settings.RangeMode switch
        {
            DisplayRangeMode.Full => finite[0],
            DisplayRangeMode.Percentile => Percentile(finite, .02),
            DisplayRangeMode.Fixed => settings.FixedMinimum,
            _ => finite[0],
        };
        var maximum = settings.RangeMode switch
        {
            DisplayRangeMode.Full => finite[^1],
            DisplayRangeMode.Percentile => Percentile(finite, .98),
            DisplayRangeMode.Fixed => settings.FixedMaximum,
            _ => finite[^1],
        };
        if (!double.IsFinite(minimum) || !double.IsFinite(maximum) || maximum <= minimum)
            return new(null, "Invalid fixed display range: maximum must be greater than minimum.", false);
        var bitmap = new WriteableBitmap(data.Width, data.Height, 96, 96, PixelFormats.Bgra32, null);
        var pixels = new byte[data.Values.Length * 4];
        for (var i = 0; i < data.Values.Length; i++)
        {
            var normalized = Math.Clamp((data.Values[i] - minimum) / (maximum - minimum), 0, 1);
            var color = settings.ColorMode == DisplayColorMode.Grayscale ? Gray(normalized) : Viridis(normalized);
            pixels[i * 4] = color.B;
            pixels[i * 4 + 1] = color.G;
            pixels[i * 4 + 2] = color.R;
            pixels[i * 4 + 3] = 255;
        }
        bitmap.WritePixels(new Int32Rect(0, 0, data.Width, data.Height), pixels, data.Width * 4, 0);
        bitmap.Freeze();
        return new(bitmap, $"Range {minimum.ToString("G5", CultureInfo.InvariantCulture)} … {maximum.ToString("G5", CultureInfo.InvariantCulture)}", true);
    }

    private static double Percentile(double[] values, double fraction)
    {
        var position = fraction * (values.Length - 1);
        var lower = (int)Math.Floor(position);
        var upper = Math.Min(values.Length - 1, lower + 1);
        return values[lower] + (values[upper] - values[lower]) * (position - lower);
    }

    private static Color Gray(double value) { var c = (byte)Math.Round(value * 255); return Color.FromRgb(c, c, c); }

    private static Color Viridis(double value)
    {
        var stops = new[] { Color.FromRgb(68, 1, 84), Color.FromRgb(59, 82, 139), Color.FromRgb(33, 145, 140), Color.FromRgb(94, 201, 98), Color.FromRgb(253, 231, 37) };
        var scaled = value * (stops.Length - 1);
        var index = Math.Min(stops.Length - 2, (int)scaled);
        var t = scaled - index;
        var a = stops[index]; var b = stops[index + 1];
        return Color.FromRgb((byte)(a.R + (b.R - a.R) * t), (byte)(a.G + (b.G - a.G) * t), (byte)(a.B + (b.B - a.B) * t));
    }
}
