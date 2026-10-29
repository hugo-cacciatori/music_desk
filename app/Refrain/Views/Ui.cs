using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Media;
using Microsoft.UI.Xaml.Media.Imaging;
using Windows.Storage.Streams;

namespace Refrain.Views;

/// <summary>Small shared pieces of the code-built UI.</summary>
static class Ui
{
    public static Brush Brush(string key) => (Brush)Application.Current.Resources[key];

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
        Style = (Style)Application.Current.Resources["AccentButtonStyle"],
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
