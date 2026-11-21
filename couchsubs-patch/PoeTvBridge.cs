// SPDX-License-Identifier: AGPL-3.0-only
// poe-tv: bản sửa riêng của CouchSubs (v0.1.0-beta.70) để tra giá món đồ Path of Exile.
// Khung hình Remote Play được gửi tới máy chủ tra giá chạy trên cùng máy (python -m poe_tv.serve, mặc định
// http://127.0.0.1:8788/frame). Máy chủ trả về các ô chữ (id, text, box, line); ô nào cũng chuyển nguyên sang app TV
// bằng SubtitleServer.SendRegion. Mọi quyết định (khi nào tra, viết gì, đặt ở đâu) nằm ở máy chủ Python.
// Bật mặc định và thay cho phần đọc phụ đề; đặt biến môi trường POE_TV=0 để chạy như CouchSubs gốc.

using System.Globalization;
using System.Net.Http.Headers;
using System.Text.Json;
using CouchSubs.Core;

namespace CouchSubs.Shell;

public sealed class PoeTvBridge : IDisposable
{
    public static bool Enabled { get; } = Environment.GetEnvironmentVariable("POE_TV") != "0";

    private static readonly Uri Endpoint = new(
        Environment.GetEnvironmentVariable("POE_TV_URL") is { Length: > 0 } url ? url : "http://127.0.0.1:8788/frame");

    private const int DefaultIntervalMs = 300;
    private const int RetryMs = 3000;

    private readonly HttpClient http = new() { Timeout = TimeSpan.FromSeconds(20) };
    private int busy;
    private long nextAt;
    private int announced;
    private volatile bool connected;

    /// <summary>
    /// Gọi với mỗi khung hình. Trả true khi chế độ tra giá đang bật (CouchSubs bỏ qua phần đọc phụ đề).
    /// Không chặn luồng khung hình: mỗi lúc chỉ có một yêu cầu, khung đến khi đang bận thì bỏ qua.
    /// </summary>
    public bool Offer(Frame frame, SubtitleServer server)
    {
        if (!Enabled) return false;
        if (Interlocked.Exchange(ref announced, 1) == 0)
            SubtitleLog.Write($"poe-tv: chế độ tra giá PoE bật, gửi khung hình tới {Endpoint}");
        if (!frame.IsConsistent || frame.Width == 0 || frame.Height == 0) return true;
        if (Environment.TickCount64 < Interlocked.Read(ref nextAt)) return true;
        if (Interlocked.Exchange(ref busy, 1) == 1) return true;
        _ = SendAsync(frame, server);
        return true;
    }

    private async Task SendAsync(Frame frame, SubtitleServer server)
    {
        var wait = DefaultIntervalMs;
        try
        {
            var length = (int)Math.Min(frame.Pixels.LongLength, (long)frame.Stride * frame.Height);
            using var content = new ByteArrayContent(frame.Pixels, 0, length);
            content.Headers.ContentType = new MediaTypeHeaderValue("application/octet-stream");
            using var request = new HttpRequestMessage(HttpMethod.Post, Endpoint) { Content = content };
            request.Headers.Add("X-Width", frame.Width.ToString(CultureInfo.InvariantCulture));
            request.Headers.Add("X-Height", frame.Height.ToString(CultureInfo.InvariantCulture));
            request.Headers.Add("X-Stride", frame.Stride.ToString(CultureInfo.InvariantCulture));
            using var response = await http.SendAsync(request).ConfigureAwait(false);
            var json = await response.Content.ReadAsStringAsync().ConfigureAwait(false);
            using var document = JsonDocument.Parse(json);
            var root = document.RootElement;
            if (root.TryGetProperty("next_ms", out var next) && next.ValueKind == JsonValueKind.Number && next.TryGetInt32(out var ms))
                wait = Math.Clamp(ms, 50, 10000);
            if (root.TryGetProperty("regions", out var regions) && regions.ValueKind == JsonValueKind.Array)
                foreach (var region in regions.EnumerateArray())
                    Apply(region, server);
            if (!connected)
            {
                connected = true;
                SubtitleLog.Write("poe-tv: đã nối máy chủ tra giá");
            }
        }
        catch (Exception error)
        {
            wait = RetryMs;
            if (connected)
            {
                connected = false;
                SubtitleLog.Write($"poe-tv: mất máy chủ tra giá ({error.GetType().Name}: {error.Message})");
            }
        }
        finally
        {
            Interlocked.Exchange(ref nextAt, Environment.TickCount64 + wait);
            Volatile.Write(ref busy, 0);
        }
    }

    private static void Apply(JsonElement region, SubtitleServer server)
    {
        if (region.ValueKind != JsonValueKind.Object) return;
        var id = region.TryGetProperty("id", out var idValue) && idValue.ValueKind == JsonValueKind.String ? idValue.GetString() : null;
        if (string.IsNullOrEmpty(id)) return;
        var text = region.TryGetProperty("text", out var textValue) && textValue.ValueKind == JsonValueKind.String
            ? textValue.GetString() ?? ""
            : "";
        TextBox? box = null;
        if (region.TryGetProperty("box", out var b) && b.ValueKind == JsonValueKind.Array && b.GetArrayLength() == 4)
            box = new TextBox(b[0].GetDouble(), b[1].GetDouble(), b[2].GetDouble(), b[3].GetDouble());
        double? line = region.TryGetProperty("line", out var l) && l.ValueKind == JsonValueKind.Number ? l.GetDouble() : null;
        server.SendRegion(id, text, box, line);
    }

    public void Dispose() => http.Dispose();
}
