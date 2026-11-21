// SPDX-License-Identifier: AGPL-3.0-only
// poe-tv: bản sửa riêng của CouchSubs (v0.1.0-beta.70) để tra giá món đồ Path of Exile.
// Khung hình Remote Play được gửi tới máy chủ tra giá chạy trên cùng máy (python -m poe_tv.serve, mặc định
// http://127.0.0.1:8788/frame). Máy chủ trả về các ô chữ (id, text, box, line); ô nào cũng chuyển nguyên sang app TV
// bằng SubtitleServer.SendRegion. Mọi quyết định (khi nào tra, viết gì, đặt ở đâu) nằm ở máy chủ Python.
// Bật mặc định. Máy chủ giữ trạng thái bật/tắt: "price" (tra giá), "translate" (dịch phụ đề tiếng Việt), "screen"
// (dịch cả màn hình một lần). Người dùng bấm 3 nút trong cửa sổ CouchSubs (MainView.PoeTv.cs → SetAsync). Dịch tắt
// thì phần đọc phụ đề không chạy (đỡ tốn máy).
// Đặt biến môi trường POE_TV=0 để chạy như CouchSubs gốc.

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
    private volatile bool translate;
    private volatile bool price = true;
    private volatile string game = "poe1";
    private long updatedAt;
    private int screenRequests;
    private SubtitleServer? lastServer;

    /// <summary>Đã nối được máy chủ tra giá (cửa sổ PoE TV) chưa.</summary>
    public bool Connected => connected;

    /// <summary>Tra giá có đang bật không (theo máy chủ).</summary>
    public bool PriceOn => price;

    /// <summary>Dịch phụ đề có đang bật không (theo cờ "translate" của máy chủ).</summary>
    public bool TranslateOn => translate;

    /// <summary>Game đang tra giá: "poe1" hoặc "poe2" (theo máy chủ; nút Game trong cửa sổ CouchSubs để đổi).</summary>
    public string Game => game;

    /// <summary>Bao lâu (ms) chưa nhận trạng thái từ máy chủ; nút trong cửa sổ hỏi lại khi lâu không có khung hình.</summary>
    public long SinceUpdateMs => Environment.TickCount64 - Interlocked.Read(ref updatedAt);

    /// <summary>Máy chủ vừa yêu cầu "dịch cả màn hình" chưa (mỗi yêu cầu chỉ trả true một lần).</summary>
    public bool TakeScreenRequest() => Interlocked.Exchange(ref screenRequests, 0) > 0;

    /// <summary>Bản poe-tv dịch sang tiếng Việt bằng AI; không dùng bộ dịch Anh→Thổ trên máy làm dự phòng.</summary>
    public static bool Vietnamese => true;

    /// <summary>Chưa có khóa AI (hoặc bộ dịch không đổi được sang tiếng Việt): giữ nguyên tiếng Anh.</summary>
    public static ITextTranslator KeepEnglish { get; } = new KeepEnglishTranslator();

    private sealed class KeepEnglishTranslator : ITextTranslator
    {
        public Task<string> TranslateAsync(string text, CancellationToken cancellationToken = default) => Task.FromResult(text);
    }

    /// <summary>
    /// Gọi với mỗi khung hình. Trả true khi CouchSubs nên bỏ qua phần đọc phụ đề (bản poe-tv đang chạy và dịch đang tắt).
    /// Không chặn luồng khung hình: mỗi lúc chỉ có một yêu cầu, khung đến khi đang bận thì bỏ qua.
    /// </summary>
    public bool Offer(Frame frame, SubtitleServer server)
    {
        if (!Enabled) return false;
        if (Interlocked.Exchange(ref announced, 1) == 0)
            SubtitleLog.Write($"poe-tv: chế độ tra giá PoE bật, gửi khung hình tới {Endpoint}");
        if (frame.IsConsistent && frame.Width > 0 && frame.Height > 0
            && Environment.TickCount64 >= Interlocked.Read(ref nextAt)
            && Interlocked.Exchange(ref busy, 1) == 0)
            _ = SendAsync(frame, server);
        return !translate;
    }

    /// <summary>Nút trong cửa sổ CouchSubs: đổi trạng thái trên máy chủ rồi nhận lại trạng thái mới. Không bao giờ ném lỗi.</summary>
    public async Task SetAsync(bool? price = null, bool? translate = null, bool screen = false, string? game = null)
    {
        var query = new List<string> { "json=1" };
        if (price is { } p) query.Add("price=" + (p ? "1" : "0"));
        if (translate is { } t) query.Add("translate=" + (t ? "1" : "0"));
        if (screen) query.Add("screen=1");
        if (game is "poe1" or "poe2") query.Add("game=" + game);
        try
        {
            using var response = await http.GetAsync(new Uri(Endpoint, "/set?" + string.Join("&", query))).ConfigureAwait(false);
            var json = await response.Content.ReadAsStringAsync().ConfigureAwait(false);
            using var document = JsonDocument.Parse(json);
            ApplyFlags(document.RootElement);
            connected = true;
        }
        catch (Exception error)
        {
            connected = false;
            if (price is not null || translate is not null || screen || game is not null)
                SubtitleLog.Write($"poe-tv: không đổi được trạng thái ({error.GetType().Name}: {error.Message})");
        }
    }

    private void ApplyFlags(JsonElement root)
    {
        if (root.TryGetProperty("price", out var pr) && pr.ValueKind is JsonValueKind.True or JsonValueKind.False)
            price = pr.ValueKind == JsonValueKind.True;
        if (root.TryGetProperty("translate", out var tr) && tr.ValueKind is JsonValueKind.True or JsonValueKind.False)
        {
            var on = tr.ValueKind == JsonValueKind.True;
            if (translate && !on) lastServer?.Clear();          // tắt dịch: xóa phụ đề đang hiện trên TV
            if (translate != on) SubtitleLog.Write($"poe-tv: dịch phụ đề {(on ? "bật" : "tắt")}");
            translate = on;
        }
        if (root.TryGetProperty("screen", out var sc) && sc.ValueKind == JsonValueKind.True)
            Interlocked.Increment(ref screenRequests);
        if (root.TryGetProperty("game", out var gm) && gm.ValueKind == JsonValueKind.String && gm.GetString() is "poe1" or "poe2")
            game = gm.GetString()!;
        Interlocked.Exchange(ref updatedAt, Environment.TickCount64);
    }

    private async Task SendAsync(Frame frame, SubtitleServer server)
    {
        lastServer = server;
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
            ApplyFlags(root);
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
