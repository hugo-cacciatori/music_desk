using System.Globalization;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Refrain.Core;
using Refrain.Core.Browser;

namespace Refrain.Views;

/// <summary>A read-only look at a selected text or image file (SPEC §20, §36 "5."): rip logs, cue sheets, notes,
/// scans. Nothing on it writes; Open hands the file to its default app.</summary>
public sealed partial class PreviewPage : Grid
{
    const int ImageWidth = 1200;

    readonly TextBlock name = Ui.Title();
    readonly TextBlock info = Ui.Secondary();
    readonly TextBlock note = Ui.Secondary();
    readonly Button open = Ui.Button("Open", "Open it with its default app");
    readonly TextBox text = new()
    {
        IsReadOnly = true,
        AcceptsReturn = true,
        FontFamily = new Microsoft.UI.Xaml.Media.FontFamily("Cascadia Mono, Consolas"),
        FontSize = 13,
    };
    readonly ScrollViewer imageScroll = new() { HorizontalScrollMode = ScrollMode.Disabled };
    readonly Image image = new() { Stretch = Microsoft.UI.Xaml.Media.Stretch.Uniform, HorizontalAlignment = HorizontalAlignment.Left };
    string? path;
    int generation;

    public (long Size, double Mtime)? Stamp { get; private set; }

    public PreviewPage()
    {
        Padding = new Thickness(16);
        RowSpacing = 8;
        RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });
        RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });
        RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });
        RowDefinitions.Add(new RowDefinition { Height = new GridLength(1, GridUnitType.Star) });
        var head = new Grid { ColumnSpacing = 8 };
        head.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
        head.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });
        head.Children.Add(name);
        SetColumn(open, 1);
        head.Children.Add(open);
        Children.Add(head);
        SetRow(info, 1);
        Children.Add(info);
        SetRow(note, 2);
        Children.Add(note);
        ScrollViewer.SetHorizontalScrollBarVisibility(text, ScrollBarVisibility.Auto);
        ScrollViewer.SetVerticalScrollBarVisibility(text, ScrollBarVisibility.Auto);
        SetRow(text, 3);
        Children.Add(text);
        imageScroll.Content = image;
        SetRow(imageScroll, 3);
        Children.Add(imageScroll);
        open.Click += (_, _) =>
        {
            if (path is not null) Shell.Open(path);
        };
    }

    /// <summary>Reads the file off the UI thread; <paramref name="stamp"/> (size, mtime) lets a change reload it.</summary>
    public async Task Load(string file, (long, double)? stamp)
    {
        var mine = ++generation;
        var reload = path == file;
        path = file;
        Stamp = stamp;
        name.Text = Path.GetFileName(file);
        var modified = stamp is { } s ? DateTime.UnixEpoch.AddSeconds(s.Item2).ToLocalTime().ToString("yyyy-MM-dd HH:mm", CultureInfo.InvariantCulture) : "";
        if (Preview.Of(file) == Preview.Kind.Text)
        {
            var offset = reload ? FindScroll(text)?.VerticalOffset : null;
            TextPreview? read = null;
            string? error = null;
            try
            {
                read = await Task.Run(() => Preview.ReadText(file));
            }
            catch (Exception e) when (e is IOException or UnauthorizedAccessException)
            {
                error = e.Message;
            }
            if (mine != generation) return;
            imageScroll.Visibility = Visibility.Collapsed;
            text.Visibility = Visibility.Visible;
            text.Text = read?.Text ?? "";
            text.TextWrapping = read?.Wrap == true ? TextWrapping.Wrap : TextWrapping.NoWrap;
            info.Text = read is null ? "" : $"{Columns.Size(read.Size)} · modified {modified} · {read.Encoding}";
            note.Text = error is not null ? $"Can't read it: {error}"
                : read!.Truncated ? $"Showing the first {Columns.Size(Preview.TextLimit)} of {Columns.Size(read.Size)}. Open it to see the rest."
                : "";
            if (offset is { } y) FindScroll(text)?.ChangeView(null, y, null, disableAnimation: true);
        }
        else
        {
            text.Visibility = Visibility.Collapsed;
            imageScroll.Visibility = Visibility.Visible;
            var size = stamp?.Item1 ?? 0;
            image.Source = null;
            if (size > Preview.ImageLimit)
            {
                info.Text = $"{Columns.Size(size)} · modified {modified}";
                note.Text = $"Too large to preview ({Columns.Size(size)}). Open it instead.";
                note.Visibility = Visibility.Visible;
                return;
            }
            var data = await Task.Run(() => File.ReadAllBytes(file));
            var bitmap = await Ui.Image(data, ImageWidth);
            if (mine != generation) return;
            image.Source = bitmap;
            info.Text = bitmap is null ? $"{Columns.Size(data.Length)} · modified {modified}"
                : $"{bitmap.PixelWidth} × {bitmap.PixelHeight} px · {Columns.Size(data.Length)} · modified {modified}";
            note.Text = bitmap is null ? "Can't show this image: it couldn't be decoded." : "";
        }
        note.Visibility = note.Text.Length > 0 ? Visibility.Visible : Visibility.Collapsed;
    }

    static ScrollViewer? FindScroll(DependencyObject parent)
    {
        for (var i = 0; i < Microsoft.UI.Xaml.Media.VisualTreeHelper.GetChildrenCount(parent); i++)
        {
            var child = Microsoft.UI.Xaml.Media.VisualTreeHelper.GetChild(parent, i);
            if (child is ScrollViewer found) return found;
            if (FindScroll(child) is { } deeper) return deeper;
        }
        return null;
    }
}
