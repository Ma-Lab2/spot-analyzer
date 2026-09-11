using System.Buffers.Binary;
using System.Security.Cryptography;
using System.Windows.Media.Imaging;

namespace SpotAnalysis.App;

public sealed record PngInputInfo(
    string Path,
    string Sha256,
    int Width,
    int Height,
    int BitDepth,
    BitmapImage Preview)
{
    public string Summary => $"{Width}×{Height}, {BitDepth}-bit grayscale PNG, SHA-256 {Sha256}";
}

public static class PngInput
{
    private static readonly byte[] Signature = [0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a];

    public static async Task<PngInputInfo> ReadAsync(string path, CancellationToken cancellationToken = default)
    {
        byte[] data;
        try
        {
            data = await File.ReadAllBytesAsync(path, cancellationToken);
        }
        catch (Exception exception) when (exception is IOException or UnauthorizedAccessException)
        {
            throw new InputValidationException("input_unreadable", exception.Message);
        }

        if (data.Length < 33 || !data.AsSpan(0, Signature.Length).SequenceEqual(Signature))
            throw new InputValidationException("unsupported_png", "Input must be a PNG file.");
        var ihdrLength = BinaryPrimitives.ReadUInt32BigEndian(data.AsSpan(8, 4));
        if (ihdrLength != 13 || !data.AsSpan(12, 4).SequenceEqual("IHDR"u8))
            throw new InputValidationException("invalid_png", "PNG IHDR is missing or invalid.");
        var width = BinaryPrimitives.ReadUInt32BigEndian(data.AsSpan(16, 4));
        var height = BinaryPrimitives.ReadUInt32BigEndian(data.AsSpan(20, 4));
        var bitDepth = data[24];
        var colorType = data[25];
        var compression = data[26];
        var filtering = data[27];
        var interlace = data[28];
        if (width == 0 || height == 0 || width > int.MaxValue || height > int.MaxValue
            || compression != 0 || filtering != 0 || interlace != 0)
            throw new InputValidationException("unsupported_png", "PNG dimensions or encoding are unsupported.");
        if (colorType != 0 || (bitDepth != 8 && bitDepth != 16))
            throw new InputValidationException("unsupported_png", "Only 8-bit and 16-bit grayscale PNG files are supported.");

        var preview = new BitmapImage();
        using (var stream = new MemoryStream(data, writable: false))
        {
            preview.BeginInit();
            preview.CacheOption = BitmapCacheOption.OnLoad;
            preview.StreamSource = stream;
            preview.EndInit();
        }
        preview.Freeze();
        return new PngInputInfo(
            path,
            Convert.ToHexString(SHA256.HashData(data)),
            (int)width,
            (int)height,
            bitDepth,
            preview);
    }
}

public sealed class InputValidationException(string code, string message) : Exception(message)
{
    public string Code { get; } = code;
}
