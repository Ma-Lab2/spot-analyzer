using System.Buffers.Binary;
using System.IO.Compression;
using System.IO;
using System.Security.Cryptography;
using System.Text;
using System.Windows.Media;
using System.Windows.Media.Imaging;

namespace SpotAnalysis.App;

public sealed record PngInputInfo(
    string Path,
    string Sha256,
    string UriHint,
    int Width,
    int Height,
    int BitDepth,
    int ColorType,
    int Channels,
    bool ChannelsIdentical,
    IReadOnlyList<ushort> IntensitySamples,
    string EncodingSemantics,
    string? ByteOrder,
    IReadOnlyDictionary<string, object?> Metadata,
    BitmapSource Preview)
{
    public string Summary => Channels == 3
        ? $"{Width}×{Height}, {BitDepth}-bit RGB (R=G=B), SHA-256 {Sha256}"
        : $"{Width}×{Height}, {BitDepth}-bit grayscale PNG, SHA-256 {Sha256}";
}

public static class PngInput
{
    private static readonly byte[] Signature = [0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a];

    public static async Task<PngInputInfo> ReadAsync(string path, CancellationToken cancellationToken = default)
    {
        if (string.IsNullOrWhiteSpace(path))
            throw new InputValidationException("input_unreadable", "无法读取输入 PNG：路径为空。");

        byte[] data;
        try
        {
            data = await File.ReadAllBytesAsync(ToExtendedPath(path), cancellationToken);
        }
        catch (Exception exception) when (exception is IOException or UnauthorizedAccessException or ArgumentException)
        {
            throw new InputValidationException("input_unreadable", $"无法读取输入 PNG：{exception.Message}");
        }

        return await Task.Run(() =>
        {
            cancellationToken.ThrowIfCancellationRequested();
            var digest = Convert.ToHexString(SHA256.HashData(data)).ToLowerInvariant();
            var decoded = DecodePixels(data);
            cancellationToken.ThrowIfCancellationRequested();
            var preview = BuildPreview(decoded);
            var uriHint = new Uri(System.IO.Path.GetFullPath(path)).AbsoluteUri;
            decoded.Metadata["uri_hint"] = uriHint;
            return new PngInputInfo(
                path,
                digest,
                uriHint,
                decoded.Width,
                decoded.Height,
                decoded.BitDepth,
                decoded.ColorType,
                decoded.Channels,
                decoded.ChannelsIdentical,
                decoded.Samples,
                "relative_intensity_code",
                decoded.BitDepth == 16 ? "big" : null,
                decoded.Metadata,
                preview);
        }, cancellationToken);
    }

    private static string ToExtendedPath(string path)
    {
        if (!OperatingSystem.IsWindows() || path.StartsWith(@"\\?\", StringComparison.Ordinal)) return path;
        var full = System.IO.Path.GetFullPath(path);
        if (full.Length < 240) return full;
        return full.StartsWith(@"\\", StringComparison.Ordinal)
            ? @"\\?\UNC\" + full[2..]
            : @"\\?\" + full;
    }

    private sealed record Decoded(
        int Width,
        int Height,
        int BitDepth,
        int ColorType,
        int Channels,
        bool ChannelsIdentical,
        ushort[] Samples,
        Dictionary<string, object?> Metadata);

    private static Decoded DecodePixels(byte[] data)
    {
        if (data.Length < Signature.Length || !data.AsSpan(0, Signature.Length).SequenceEqual(Signature))
            throw new InputValidationException("not_png", "输入文件不是 PNG。");

        var offset = Signature.Length;
        byte[]? ihdr = null;
        using var compressed = new MemoryStream();
        var metadata = new Dictionary<string, object?>(StringComparer.Ordinal)
        {
            ["format"] = "PNG",
            ["icc_profile_present"] = false,
        };
        var ended = false;
        while (offset + 12 <= data.Length)
        {
            var length = BinaryPrimitives.ReadUInt32BigEndian(data.AsSpan(offset, 4));
            if (length > int.MaxValue || offset > data.Length - 12 - (int)length)
                throw new InputValidationException("png_decode_failed", "PNG 数据块已截断。");
            var kind = data.AsSpan(offset + 4, 4);
            var payloadOffset = offset + 8;
            var payload = data.AsSpan(payloadOffset, (int)length);
            var expectedCrc = BinaryPrimitives.ReadUInt32BigEndian(data.AsSpan(payloadOffset + (int)length, 4));
            if (Crc32(kind, payload) != expectedCrc)
                throw new InputValidationException("png_decode_failed", "PNG 数据块校验失败。");
            offset = payloadOffset + (int)length + 4;

            if (kind.SequenceEqual("IHDR"u8))
            {
                if (ihdr is not null || payload.Length != 13)
                    throw new InputValidationException("png_decode_failed", "PNG IHDR 无效。");
                ihdr = payload.ToArray();
            }
            else if (kind.SequenceEqual("IDAT"u8))
            {
                compressed.Write(payload);
            }
            else if (kind.SequenceEqual("gAMA"u8) && payload.Length == 4)
            {
                metadata["gamma"] = BinaryPrimitives.ReadUInt32BigEndian(payload) / 100000.0;
            }
            else if (kind.SequenceEqual("sRGB"u8) && payload.Length == 1)
            {
                metadata["srgb"] = payload[0];
            }
            else if (kind.SequenceEqual("iCCP"u8))
            {
                metadata["icc_profile_present"] = true;
            }
            else if (kind.SequenceEqual("IEND"u8))
            {
                ended = true;
                break;
            }
        }

        if (ihdr is null)
            throw new InputValidationException("png_decode_failed", "PNG 缺少 IHDR。");
        if (!ended)
            throw new InputValidationException("png_decode_failed", "PNG 缺少 IEND。");

        var width = BinaryPrimitives.ReadUInt32BigEndian(ihdr.AsSpan(0, 4));
        var height = BinaryPrimitives.ReadUInt32BigEndian(ihdr.AsSpan(4, 4));
        var bitDepth = ihdr[8];
        var colorType = ihdr[9];
        if (width == 0 || height == 0 || width > int.MaxValue || height > int.MaxValue
            || ihdr[10] != 0 || ihdr[11] != 0 || ihdr[12] != 0)
            throw new InputValidationException("png_encoding_unsupported", "不支持的 PNG 尺寸或编码（仅支持非隔行 PNG）。");

        var channels = colorType switch
        {
            0 when bitDepth is 8 or 16 => 1,
            2 when bitDepth == 8 => 3,
            2 => throw new InputValidationException("png_encoding_unsupported", "仅支持 8 位且 R=G=B 的 RGB PNG。"),
            _ => throw new InputValidationException("png_encoding_unsupported", "仅支持 8/16 位灰度 PNG；不支持 RGBA、调色板或其他颜色编码。"),
        };

        var rowBytes = checked((int)width * channels * (bitDepth / 8));
        byte[] inflated;
        try
        {
            compressed.Position = 0;
            using var zlib = new ZLibStream(compressed, CompressionMode.Decompress, leaveOpen: true);
            using var unpacked = new MemoryStream();
            zlib.CopyTo(unpacked);
            inflated = unpacked.ToArray();
        }
        catch (InvalidDataException exception)
        {
            throw new InputValidationException("png_decode_failed", $"PNG 图像数据无法解压：{exception.Message}");
        }

        var expectedLength = checked((long)height * (rowBytes + 1));
        if (inflated.LongLength != expectedLength)
            throw new InputValidationException("png_decode_failed", "PNG 扫描行长度无效。");
        var pixels = Unfilter(inflated, rowBytes, (int)height, channels * (bitDepth / 8));
        var samples = new ushort[checked((int)width * (int)height)];
        var sampleBytes = bitDepth / 8;
        var channelsIdentical = channels == 3;
        var sourceIndex = 0;
        for (var pixel = 0; pixel < samples.Length; pixel++)
        {
            ushort first = sampleBytes == 1 ? pixels[sourceIndex] : BinaryPrimitives.ReadUInt16BigEndian(pixels.AsSpan(sourceIndex, 2));
            if (channels == 3)
            {
                var secondOffset = sourceIndex + sampleBytes;
                var thirdOffset = secondOffset + sampleBytes;
                ushort second = sampleBytes == 1 ? pixels[secondOffset] : BinaryPrimitives.ReadUInt16BigEndian(pixels.AsSpan(secondOffset, 2));
                ushort third = sampleBytes == 1 ? pixels[thirdOffset] : BinaryPrimitives.ReadUInt16BigEndian(pixels.AsSpan(thirdOffset, 2));
                if (first != second || second != third) channelsIdentical = false;
            }
            samples[pixel] = first;
            sourceIndex += channels * sampleBytes;
        }
        if (channels == 3 && !channelsIdentical)
            throw new InputValidationException("rgb_channels_not_identical", "RGB 三个通道不完全一致，不能安全地按灰度处理。");

        metadata["mode"] = channels == 1 ? (bitDepth == 8 ? "L" : "I;16") : "RGB";
        metadata["png_bit_depth"] = bitDepth;
        metadata["png_color_type"] = colorType;
        metadata["channels"] = channels;
        metadata["channels_identical"] = channelsIdentical;
        metadata["gamma_present"] = metadata.ContainsKey("gamma");
        metadata["srgb_present"] = metadata.ContainsKey("srgb");
        metadata["byte_order"] = bitDepth == 16 ? "big" : null;
        return new Decoded((int)width, (int)height, bitDepth, colorType, channels, channelsIdentical, samples, metadata);
    }

    private static byte[] Unfilter(byte[] raw, int rowBytes, int height, int bytesPerPixel)
    {
        var pixels = new byte[checked(rowBytes * height)];
        var previous = new byte[rowBytes];
        var source = 0;
        for (var row = 0; row < height; row++)
        {
            var filter = raw[source++];
            var destination = row * rowBytes;
            raw.AsSpan(source, rowBytes).CopyTo(pixels.AsSpan(destination, rowBytes));
            source += rowBytes;
            for (var index = 0; index < rowBytes; index++)
            {
                var left = index >= bytesPerPixel ? pixels[destination + index - bytesPerPixel] : (byte)0;
                var up = previous[index];
                var upperLeft = index >= bytesPerPixel ? previous[index - bytesPerPixel] : (byte)0;
                var value = pixels[destination + index];
                pixels[destination + index] = filter switch
                {
                    0 => value,
                    1 => (byte)(value + left),
                    2 => (byte)(value + up),
                    3 => (byte)(value + ((left + up) / 2)),
                    4 => (byte)(value + Paeth(left, up, upperLeft)),
                    _ => throw new InputValidationException("png_encoding_unsupported", $"不支持的 PNG 过滤器 {filter}。"),
                };
            }
            pixels.AsSpan(destination, rowBytes).CopyTo(previous);
        }
        return pixels;
    }

    private static byte Paeth(byte left, byte up, byte upperLeft)
    {
        var estimate = left + up - upperLeft;
        var leftDistance = Math.Abs(estimate - left);
        var upDistance = Math.Abs(estimate - up);
        var upperLeftDistance = Math.Abs(estimate - upperLeft);
        return leftDistance <= upDistance && leftDistance <= upperLeftDistance ? left
            : upDistance <= upperLeftDistance ? up : upperLeft;
    }

    private static uint Crc32(ReadOnlySpan<byte> kind, ReadOnlySpan<byte> payload)
    {
        uint crc = 0xffffffff;
        foreach (var value in kind) crc = CrcByte(crc, value);
        foreach (var value in payload) crc = CrcByte(crc, value);
        return ~crc;
    }

    private static uint CrcByte(uint crc, byte value)
    {
        crc ^= value;
        for (var bit = 0; bit < 8; bit++)
            crc = (crc & 1) != 0 ? (crc >> 1) ^ 0xedb88320 : crc >> 1;
        return crc;
    }

    private static BitmapSource BuildPreview(Decoded decoded)
    {
        // The measurement decoder above is the source of truth for supported PNGs.
        // Do not run the input through a second WPF PNG codec: codec support varies
        // by bit depth, metadata, and OS imaging components, which could reject an
        // otherwise valid measurement input immediately on import. Build a frozen
        // display-only bitmap from the already decoded intensity samples instead.
        var pixels = new byte[checked(decoded.Width * decoded.Height * 4)];
        for (var index = 0; index < decoded.Samples.Length; index++)
        {
            var value = decoded.BitDepth == 16
                ? (byte)Math.Round(decoded.Samples[index] / 257.0, MidpointRounding.ToEven)
                : (byte)decoded.Samples[index];
            var destination = index * 4;
            pixels[destination] = value;
            pixels[destination + 1] = value;
            pixels[destination + 2] = value;
            pixels[destination + 3] = 255;
        }

        var source = BitmapSource.Create(
            decoded.Width, decoded.Height, 96, 96,
            PixelFormats.Bgra32, null, pixels, decoded.Width * 4);
        source.Freeze();
        return source;
    }
}

public sealed class InputValidationException(string code, string message) : Exception(message)
{
    public string Code { get; } = code;
}
