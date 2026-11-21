// SPDX-License-Identifier: AGPL-3.0-only
// poe-tv: 4 nút dưới nút "Start translating": Tra giá bật/tắt, Dịch tiếng Việt bật/tắt, Dịch cả màn hình (một lần),
// Game PoE1/PoE2 (tra giá theo game nào).
// Trạng thái nằm ở máy chủ poe_tv (cửa sổ PoE TV); nút chỉ gửi lệnh rồi hiện trạng thái máy chủ trả về.

using System.Windows;
using System.Windows.Threading;
using CouchSubs.Shell;

namespace CouchSubs.App.Views;

public partial class MainView
{
    private DispatcherTimer? poeTimer;
    private bool poePolling;

    private async Task PollPoeAsync()
    {
        poePolling = true;
        try { await controller.PoeTv.SetAsync(); }
        finally { poePolling = false; }
    }

    private void OnPoeLoaded(object sender, RoutedEventArgs e)
    {
        if (!PoeTvBridge.Enabled)
        {
            PoeButtons.Visibility = Visibility.Collapsed;
            return;
        }
        PoeScreenButton.Content = "Dịch cả màn hình";
        RefreshPoe();
        if (poeTimer is not null) return;
        // Trạng thái có thể đổi từ nơi khác (trang điện thoại, máy chủ khởi động lại): cập nhật mỗi giây.
        poeTimer = new DispatcherTimer { Interval = TimeSpan.FromSeconds(1) };
        poeTimer.Tick += (_, _) => RefreshPoe();
        poeTimer.Start();
    }

    private void RefreshPoe()
    {
        var bridge = controller.PoeTv;
        // Chưa bấm bắt đầu thì không có khung hình nào mang trạng thái về: hỏi máy chủ (không đổi gì) mỗi ~3 giây.
        if (bridge.SinceUpdateMs > 3000 && !poePolling) _ = PollPoeAsync();
        PoeGameButton.Content = bridge.Game == "poe2" ? "Game: PoE2" : "Game: PoE1";
        PoePriceButton.Content = bridge.PriceOn ? "Tra giá: BẬT" : "Tra giá: TẮT";
        PoeTranslateButton.Content = bridge.TranslateOn ? "Dịch: BẬT" : "Dịch: TẮT";
        PoeButtons.ToolTip = bridge.Connected ? null : "Chưa nối được máy chủ tra giá: mở PoE TV (cửa sổ đen) trước.";
    }

    private async void OnPoePrice(object sender, RoutedEventArgs e)
    {
        await controller.PoeTv.SetAsync(price: !controller.PoeTv.PriceOn);
        RefreshPoe();
    }

    private async void OnPoeTranslate(object sender, RoutedEventArgs e)
    {
        await controller.PoeTv.SetAsync(translate: !controller.PoeTv.TranslateOn);
        RefreshPoe();
    }

    private async void OnPoeScreen(object sender, RoutedEventArgs e) => await controller.PoeTv.SetAsync(screen: true);

    private async void OnPoeGame(object sender, RoutedEventArgs e)
    {
        await controller.PoeTv.SetAsync(game: controller.PoeTv.Game == "poe2" ? "poe1" : "poe2");
        RefreshPoe();
    }
}
