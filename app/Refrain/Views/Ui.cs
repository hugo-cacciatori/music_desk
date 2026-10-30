using Microsoft.UI.Windowing;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Media;
using Microsoft.UI.Xaml.Media.Imaging;
using Windows.Graphics;
using Windows.Storage.Streams;

namespace Refrain.Views;

/// <summary>Small shared pieces of the code-built UI.</summary>
static class Ui
{
    public static Brush Brush(string key) => Resource<Brush>(key);

    /// <summary>An app or theme resource as its type. Under Native AOT a resource can come back as a bare
    /// DependencyObject (CsWinRT doesn't map its runtime class), which a C# cast refuses; As&lt;T&gt; asks the object
    /// itself (measured, 2026-10-30: the published app closed at start on the first list's template).</summary>
    public static T Resource<T>(string key) where T : class
    {
        var value = Application.Current.Resources[key];
        return value as T ?? WinRT.CastExtensions.As<T>(value);
    }

    const double ReadingWidth = 800;  // wrapped text is laid out at most this wide (px at 100%)
    static readonly System.Runtime.CompilerServices.ConditionalWeakTable<Window, object> Fitted = new();

    /// <summary>A window of its own (SPEC §35: own taskbar button): title, backdrop, icon and a small first size
    /// until <see cref="Fit"/> sizes it to its content. In an agent's off-screen check it sits beyond every monitor and
    /// out of Alt+Tab.</summary>
    public static void Prepare(Window window, string title)
    {
        window.Title = title;
        window.SystemBackdrop = new MicaBackdrop();
        window.AppWindow.SetIcon(System.IO.Path.Combine(AppContext.BaseDirectory, "Assets", "refrain.ico"));
        window.AppWindow.Resize(new SizeInt32(560, 240));
        if (!SearchWindow.Offscreen) return;
        var areas = DisplayArea.FindAll();
        var right = 0;
        for (var i = 0; i < areas.Count; i++) right = Math.Max(right, areas[i].OuterBounds.X + areas[i].OuterBounds.Width);
        window.AppWindow.Move(new PointInt32(right + 200, 0));
        window.AppWindow.IsShownInSwitchers = false;
    }

    /// <summary>Sizes the window to the smallest size that fits its content (SPEC §36 "Window sizes"), once: call it
    /// when the first real content shows; later calls do nothing, so a window is never resized under the user. Wrapped
    /// text is laid out at most <see cref="ReadingWidth"/> wide (wider only for parts that can't wrap), the window stays
    /// within 90% of its screen's work area, and it is moved to stay on that screen.</summary>
    public static void Fit(Window window)
    {
        if (Fitted.TryGetValue(window, out _) || window.Content is not FrameworkElement root) return;
        Fitted.Add(window, new object());
        // After the content's first layout pass, so templates and text are in place.
        root.DispatcherQueue.TryEnqueue(Microsoft.UI.Dispatching.DispatcherQueuePriority.Low, () =>
        {
            if (root.XamlRoot is null) return;
            var scale = root.XamlRoot.RasterizationScale;
            var area = DisplayArea.GetFromWindowId(window.AppWindow.Id, DisplayAreaFallback.Nearest).WorkArea;
            var (maxWidth, maxHeight) = (area.Width * 0.9 / scale, area.Height * 0.9 / scale);
            root.Measure(new Windows.Foundation.Size(double.PositiveInfinity, double.PositiveInfinity));
            var width = Math.Min(root.DesiredSize.Width, maxWidth);
            if (width > ReadingWidth)
            {
                root.Measure(new Windows.Foundation.Size(ReadingWidth, double.PositiveInfinity));
                width = Math.Min(Math.Max(ReadingWidth, root.DesiredSize.Width), maxWidth);
            }
            root.Measure(new Windows.Foundation.Size(width, double.PositiveInfinity));
            var height = Math.Min(root.DesiredSize.Height, maxHeight);
            window.AppWindow.ResizeClient(new SizeInt32((int)Math.Ceiling(width * scale), (int)Math.Ceiling(height * scale)));
            root.InvalidateMeasure();
            if (SearchWindow.Offscreen) return;
            var (position, size) = (window.AppWindow.Position, window.AppWindow.Size);
            var x = Math.Clamp(position.X, area.X, Math.Max(area.X, area.X + area.Width - size.Width));
            var y = Math.Clamp(position.Y, area.Y, Math.Max(area.Y, area.Y + area.Height - size.Height));
            if (x != position.X || y != position.Y) window.AppWindow.Move(new PointInt32(x, y));
        });
    }

    /// <summary>Shows a window: activated normally, never taking the focus in an agent's off-screen check.</summary>
    public static void Show(Window window)
    {
        if (SearchWindow.Offscreen) window.AppWindow.Show(activateWindow: false);
        else window.Activate();
    }

    /// <summary>Report text in a fixed-width font, so the reports' tables line up; read-only, selectable, scrolled
    /// both ways. A minimum size keeps room for a report that fills in after the window was fitted (SPEC §36 "Window
    /// sizes").</summary>
    public static TextBox ReportBox(double minWidth = 0, double minHeight = 0)
    {
        var box = new TextBox
        {
            MinWidth = minWidth,
            MinHeight = minHeight,
            IsReadOnly = true,
            AcceptsReturn = true,
            TextWrapping = TextWrapping.NoWrap,
            FontFamily = new FontFamily("Cascadia Mono, Consolas"),
            FontSize = 13,
        };
        ScrollViewer.SetHorizontalScrollBarVisibility(box, ScrollBarVisibility.Auto);
        ScrollViewer.SetVerticalScrollBarVisibility(box, ScrollBarVisibility.Auto);
        return box;
    }

    public static TextBlock Secondary(string text = "") => new()
    {
        Text = text,
        TextWrapping = TextWrapping.Wrap,
        Foreground = Brush("TextFillColorSecondaryBrush"),
    };

    public static TextBlock Title(string text = "") => new()
    {
        Text = text,
        FontSize = 20,
        FontWeight = Microsoft.UI.Text.FontWeights.SemiBold,
        TextWrapping = TextWrapping.Wrap,
    };

    /// <summary>The main action: filled with the accent colour (SPEC §36 "1." task 4).</summary>
    public static Button Primary(string text) => new()
    {
        Content = text,
        Style = Resource<Style>("AccentButtonStyle"),
    };

    public static Button Button(string text, string tip)
    {
        var button = new Button { Content = text };
        ToolTipService.SetToolTip(button, tip);
        return button;
    }

    /// <summary>An image decoded from bytes at most <paramref name="width"/> pixels wide (never upscaled); null when
    /// it can't be decoded.</summary>
    public static async Task<BitmapImage?> Image(byte[] data, int width)
    {
        try
        {
            using var stream = new InMemoryRandomAccessStream();
            using (var writer = new DataWriter(stream.GetOutputStreamAt(0)))
            {
                writer.WriteBytes(data);
                await writer.StoreAsync();
                await writer.FlushAsync();
                writer.DetachStream();
            }
            stream.Seek(0);
            var image = new BitmapImage { DecodePixelWidth = width, DecodePixelType = DecodePixelType.Logical };
            await image.SetSourceAsync(stream);
            if (image.PixelWidth > 0 && image.PixelWidth < width)
            {
                // Smaller than the room: decode at its own size instead (never upscaled).
                stream.Seek(0);
                image = new BitmapImage();
                await image.SetSourceAsync(stream);
            }
            return image;
        }
        catch (Exception)
        {
            return null;
        }
    }

    /// <summary>A label and a value on one line of a details block.</summary>
    public static void Detail(Grid grid, string label, string? value)
    {
        if (string.IsNullOrEmpty(value)) return;
        var row = grid.RowDefinitions.Count;
        grid.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });
        var name = Secondary(label);
        Grid.SetRow(name, row);
        grid.Children.Add(name);
        var text = new TextBlock { Text = value, TextWrapping = TextWrapping.Wrap, IsTextSelectionEnabled = true };
        Grid.SetRow(text, row);
        Grid.SetColumn(text, 1);
        grid.Children.Add(text);
    }

    public static Grid DetailsGrid()
    {
        var grid = new Grid { ColumnSpacing = 12, RowSpacing = 4 };
        grid.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });
        grid.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
        return grid;
    }
}
