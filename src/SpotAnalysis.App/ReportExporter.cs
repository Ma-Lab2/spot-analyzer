using System.Globalization;
using System.Text;
using System.Text.Json;
using System.Windows;
using System.Windows.Media;
using System.Windows.Media.Imaging;

namespace SpotAnalysis.App;

public sealed record ReportSpecification(
    string Format,
    string ReportName,
    string OutputDirectory,
    bool AppendTimestamp = false);

public sealed record ReportExportOutcome(
    string? Path,
    string RecordId,
    string FlowStatus,
    string? ErrorCode = null,
    string? ErrorMessage = null);

/// <summary>
/// Client-side adapter for the report seam. It creates one immutable text snapshot,
/// then renders that snapshot as PNG or PDF; it never serializes the terminal JSON.
/// </summary>
public static class ReportExporter
{
    public static ReportExportOutcome Write(JsonElement terminalResult, ReportSpecification specification)
    {
        var format = specification.Format.Trim().TrimStart('.').ToLowerInvariant();
        if (format is not ("png" or "pdf"))
            throw new ArgumentException("Report format must be PDF or PNG.", nameof(specification));

        var reportName = specification.ReportName.Trim();
        if (reportName.Length == 0)
            throw new ArgumentException("Report name must not be empty.", nameof(specification));

        if (!terminalResult.TryGetProperty("record", out var record) || record.ValueKind != JsonValueKind.Object)
            return new ReportExportOutcome(null, "", "export_failed", "report_record_missing", "The analysis result has no reportable record.");

        var recordId = StringValue(record, "record_id") ?? "unknown-record";
        var flowStatus = StringValue(record, "flow_status") ?? "unknown";
        var generatedAt = DateTimeOffset.UtcNow.ToString("O", CultureInfo.InvariantCulture);
        var lines = BuildReportLines(record, reportName, generatedAt);
        var payload = format == "png" ? RenderPng(lines) : RenderPdf(lines);
        var directory = string.IsNullOrWhiteSpace(specification.OutputDirectory)
            ? AppContext.BaseDirectory
            : specification.OutputDirectory;
        try
        {
            Directory.CreateDirectory(directory);
            var stem = SanitizeName(reportName);
            if (specification.AppendTimestamp)
                stem += "-" + DateTimeOffset.UtcNow.ToString("yyyyMMdd'T'HHmmss'Z'", CultureInfo.InvariantCulture);
            var extension = "." + format;
            var target = WriteCollisionSafe(directory, stem, extension, payload);
            return new ReportExportOutcome(target, recordId, "exported");
        }
        catch (Exception exception) when (exception is IOException or UnauthorizedAccessException)
        {
            return new ReportExportOutcome(null, recordId, "export_failed", "report_write_failed", exception.Message);
        }
    }

    private static IReadOnlyList<string> BuildReportLines(JsonElement record, string reportName, string generatedAt)
    {
        var lines = new List<string>
        {
            $"Spot analysis report — {reportName}",
            $"record_id: {StringValue(record, "record_id") ?? "unknown"}",
            $"generated_at: {generatedAt}",
            $"flow_status: {StringValue(record, "flow_status") ?? "unknown"}",
            $"summary_status: {StringValue(record, "summary_status") ?? "unknown"}",
        };
        if (record.TryGetProperty("input", out var input) && input.ValueKind == JsonValueKind.Object)
        {
            lines.Add("input:");
            foreach (var property in input.EnumerateObject())
                lines.Add($"  {property.Name}: {Display(property.Value)}");
        }
        AddObjectSection(lines, record, "configuration");
        AddObjectSection(lines, record, "diagnostics");
        lines.Add("metrics:");
        if (record.TryGetProperty("metrics", out var metrics) && metrics.ValueKind == JsonValueKind.Object)
            AppendMetrics(lines, metrics, "  ");
        else
            lines.Add("  N/A");
        lines.Add("provenance:");
        foreach (var name in new[] { "analysis_fingerprint", "input_shape" })
            if (record.TryGetProperty(name, out var value)) lines.Add($"  {name}: {Display(value)}");
        return lines;
    }

    private static void AddObjectSection(List<string> lines, JsonElement record, string name)
    {
        lines.Add(name + ":");
        if (record.TryGetProperty(name, out var section) && section.ValueKind == JsonValueKind.Object)
            AppendObject(lines, section, "  ");
        else
            lines.Add("  N/A");
    }

    private static void AppendObject(List<string> lines, JsonElement value, string indent)
    {
        foreach (var property in value.EnumerateObject())
        {
            if (property.Value.ValueKind == JsonValueKind.Object)
            {
                lines.Add($"{indent}{property.Name}:");
                AppendObject(lines, property.Value, indent + "  ");
            }
            else if (property.Value.ValueKind == JsonValueKind.Array)
            {
                lines.Add($"{indent}{property.Name}: {Display(property.Value)}");
            }
            else
            {
                lines.Add($"{indent}{property.Name}: {Display(property.Value)}");
            }
        }
    }

    private static void AppendMetrics(List<string> lines, JsonElement metrics, string indent)
    {
        foreach (var metric in metrics.EnumerateObject())
        {
            if (metric.Value.ValueKind == JsonValueKind.Object && metric.Value.TryGetProperty("domains", out var domains))
            {
                foreach (var domain in domains.EnumerateObject())
                    AppendMetric(lines, $"{metric.Name} ({domain.Name})", domain.Value, indent);
            }
            else
            {
                AppendMetric(lines, metric.Name, metric.Value, indent);
            }
        }
    }

    private static void AppendMetric(List<string> lines, string name, JsonElement metric, string indent)
    {
        var value = metric.ValueKind == JsonValueKind.Object && metric.TryGetProperty("value", out var valueNode)
            ? Display(valueNode)
            : "N/A";
        var status = StringValue(metric, "status") ?? "unknown";
        var unit = StringValue(metric, "unit") ?? "";
        var reasons = metric.ValueKind == JsonValueKind.Object && metric.TryGetProperty("reason_codes", out var reasonNode)
            ? Display(reasonNode)
            : "[]";
        lines.Add($"{indent}{name}: {value} {unit}; validity={status}; reasons={reasons}");
    }

    private static string Display(JsonElement value) => value.ValueKind switch
    {
        JsonValueKind.Null or JsonValueKind.Undefined => "N/A",
        JsonValueKind.String => value.GetString() ?? "N/A",
        _ => value.ToString(),
    };

    private static string? StringValue(JsonElement value, string property) =>
        value.ValueKind == JsonValueKind.Object && value.TryGetProperty(property, out var child)
            && child.ValueKind != JsonValueKind.Null ? child.ToString() : null;

    public static string SanitizeName(string name)
    {
        var builder = new StringBuilder(name.Length);
        foreach (var character in name)
            builder.Append(char.IsLetterOrDigit(character) || character is '.' or '_' or '-' ? character : '_');
        var result = builder.ToString().Trim('.', '_');
        return result.Length == 0 ? "analysis-report" : result;
    }

    private static string WriteCollisionSafe(string directory, string stem, string extension, byte[] payload)
    {
        for (var index = 1; ; index++)
        {
            var suffix = index == 1 ? "" : $"-{index}";
            var target = Path.Combine(directory, stem + suffix + extension);
            var temporary = Path.Combine(directory, $".report-{Guid.NewGuid():N}.tmp");
            try
            {
                File.WriteAllBytes(temporary, payload);
                File.Move(temporary, target);
                return target;
            }
            catch (IOException) when (File.Exists(target))
            {
                TryDelete(temporary);
            }
            catch
            {
                TryDelete(temporary);
                throw;
            }
        }
    }

    private static void TryDelete(string path)
    {
        try { File.Delete(path); } catch (IOException) { }
    }

    private static byte[] RenderPng(IReadOnlyList<string> lines)
    {
        const double width = 1400;
        var height = Math.Max(800, 40 + lines.Count * 24);
        var visual = new DrawingVisual();
        using (var context = visual.RenderOpen())
        {
            context.DrawRectangle(Brushes.White, null, new Rect(0, 0, width, height));
            var typeface = new Typeface("Segoe UI");
            for (var index = 0; index < lines.Count; index++)
            {
                var text = new FormattedText(lines[index], CultureInfo.InvariantCulture,
                    FlowDirection.LeftToRight, typeface, 16, Brushes.Black, 1.0);
                context.DrawText(text, new Point(24, 20 + index * 24));
            }
        }
        var bitmap = new RenderTargetBitmap((int)width, (int)height, 96, 96, PixelFormats.Pbgra32);
        bitmap.Render(visual);
        var encoder = new PngBitmapEncoder();
        encoder.Frames.Add(BitmapFrame.Create(bitmap));
        using var output = new MemoryStream();
        encoder.Save(output);
        return output.ToArray();
    }

    private static byte[] RenderPdf(IReadOnlyList<string> lines)
    {
        var content = new StringBuilder("BT\n/F1 11 Tf\n50 780 Td\n");
        foreach (var line in lines)
        {
            content.Append('(').Append(EscapePdf(line)).Append(") Tj\n0 -15 Td\n");
        }
        content.Append("ET");
        var stream = Encoding.ASCII.GetBytes(content.ToString());
        using var output = new MemoryStream();
        using var writer = new StreamWriter(output, Encoding.ASCII, leaveOpen: true);
        writer.Write("%PDF-1.4\n");
        writer.Flush();
        var offsets = new List<long> { 0 };
        WriteObject(writer, output, offsets, 1, "<< /Type /Catalog /Pages 2 0 R >>");
        WriteObject(writer, output, offsets, 2, "<< /Type /Pages /Kids [3 0 R] /Count 1 >>");
        WriteObject(writer, output, offsets, 3, "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>");
        WriteObject(writer, output, offsets, 4, "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>");
        offsets.Add(output.Position);
        writer.Write($"5 0 obj\n<< /Length {stream.Length} >>\nstream\n");
        writer.Flush();
        output.Write(stream);
        writer.Write("\nendstream\nendobj\n");
        writer.Flush();
        var xref = output.Position;
        writer.Write("xref\n0 6\n0000000000 65535 f \n");
        for (var index = 1; index < offsets.Count; index++) writer.Write($"{offsets[index]:D10} 00000 n \n");
        writer.Write($"trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n");
        writer.Flush();
        return output.ToArray();
    }

    private static void WriteObject(StreamWriter writer, MemoryStream output, List<long> offsets, int number, string body)
    {
        offsets.Add(output.Position);
        writer.Write($"{number} 0 obj\n{body}\nendobj\n");
        writer.Flush();
    }

    private static string EscapePdf(string value) => value.Replace("\\", "\\\\").Replace("(", "\\(").Replace(")", "\\)");
}
