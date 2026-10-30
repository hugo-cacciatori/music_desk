using Microsoft.UI.Text;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Input;
using Microsoft.UI.Xaml.Media;
using Microsoft.UI.Xaml.Media.Animation;
using Microsoft.UI.Xaml.Shapes;
using Refrain.Core.Search;
using Windows.Foundation;
using Windows.System;

namespace Refrain.Views;

/// <summary>The discography view (SPEC §36 "11.", Hugo 2026-10-30), opened by a double click on a lyrics result: the
/// artist's bubble on the left, the albums' bubbles branching to its right, earliest first, all on one screen, each
/// with its songs as small bubbles around it. A double click on an album zooms into it: its songs listed inside a big
/// bubble, a small bubble before each title; a click on one shows its lyrics beside the album, among rising bubbles.
/// The arrows on the edges move to the previous or next album. Everything floats slowly; Esc zooms out, then closes.
/// The view only reads what the search already indexed: nothing is looked up or written.</summary>
public sealed partial class DiscographyView : UserControl
{
    const double ZoomSeconds = 0.5, CycleSeconds = 0.26;
    static readonly double[] Stagger = [-0.22, 0.14, -0.16, 0.19, -0.2, 0.12];  // the albums' heights, as sketched

    readonly Grid stage = new();
    readonly Canvas overview = new();
    readonly Grid albumLayer = new();
    readonly Grid albumContent = new();
    readonly Grid bubbleHost = new();
    readonly StackPanel albumText = new() { HorizontalAlignment = HorizontalAlignment.Center, VerticalAlignment = VerticalAlignment.Center };
    readonly TextBlock albumTitle = new() { FontSize = 18, FontWeight = FontWeights.SemiBold, TextWrapping = TextWrapping.Wrap, TextAlignment = TextAlignment.Center };
    readonly TextBlock albumYear = new() { FontSize = 12, TextAlignment = TextAlignment.Center, Margin = new Thickness(0, 2, 0, 10) };
    readonly StackPanel songList = new() { Spacing = 2 };
    readonly ScrollViewer songScroll = new() { HorizontalScrollMode = ScrollMode.Disabled, VerticalScrollBarVisibility = ScrollBarVisibility.Hidden };
    readonly Grid lyricsArea = new();
    readonly Canvas risers = new() { IsHitTestVisible = false };
    readonly StackPanel lyricsBlock = new() { Spacing = 10, Margin = new Thickness(8, 0, 8, 0) };
    readonly TextBlock lyricsTitle = new() { FontSize = 12, CharacterSpacing = 40 };
    readonly TextBlock lyricsText = new()
    {
        FontSize = 16, FontStyle = Windows.UI.Text.FontStyle.Italic, FontFamily = new FontFamily("Georgia"), LineHeight = 30,
        TextWrapping = TextWrapping.Wrap, IsTextSelectionEnabled = true,
    };
    readonly ScrollViewer lyricsScroll = new() { HorizontalScrollMode = ScrollMode.Disabled, VerticalScrollBarVisibility = ScrollBarVisibility.Hidden };
    readonly Button previous = Arrow("", "Earlier album (Left)");
    readonly Button next = Arrow("", "Later album (Right)");
    readonly HyperlinkButton back = new() { Content = "‹ Results", HorizontalAlignment = HorizontalAlignment.Left, VerticalAlignment = VerticalAlignment.Top, Margin = new Thickness(8, 4, 0, 0) };
    readonly HyperlinkButton allAlbums = new() { Content = "All albums (Esc)", HorizontalAlignment = HorizontalAlignment.Right, VerticalAlignment = VerticalAlignment.Top, Margin = new Thickness(0, 4, 8, 0) };

    Bubbles.Palette p = Bubbles.For(ElementTheme.Light);
    ArtistAlbums? data;
    int album;
    int song;
    bool zoomed;
    bool moving;
    readonly List<(Point Center, double Radius)> albumSpots = [];

    /// <summary>Esc on the albums, or ‹ Results: back to the search's results, as they were.</summary>
    public event Action? Closed;

    public DiscographyView()
    {
        IsTabStop = true;
        stage.Children.Add(overview);
        BuildAlbumLayer();
        stage.Children.Add(albumLayer);
        stage.Children.Add(back);
        stage.Children.Add(allAlbums);
        Content = stage;
        overview.RenderTransform = new CompositeTransform();
        albumLayer.RenderTransform = new CompositeTransform();
        albumContent.RenderTransform = new CompositeTransform();
        lyricsBlock.RenderTransform = new CompositeTransform();
        ToolTipService.SetToolTip(back, "Back to the results (Esc on the albums)");
        ToolTipService.SetToolTip(allAlbums, "Every album of this artist");

        back.Click += (_, _) => Closed?.Invoke();
        allAlbums.Click += (_, _) => ZoomOut();
        previous.Click += (_, _) => Cycle(-1);
        next.Click += (_, _) => Cycle(1);
        SizeChanged += (_, _) => Layout();
        ActualThemeChanged += (_, _) => Paint();
        KeyDown += OnKey;
    }

    static Button Arrow(string glyph, string tip)
    {
        var button = new Button
        {
            Content = new FontIcon { Glyph = glyph, FontSize = 14 },
            Width = 40,
            Height = 40,
            CornerRadius = new CornerRadius(20),
            Padding = new Thickness(0),
            VerticalAlignment = VerticalAlignment.Center,
            HorizontalAlignment = HorizontalAlignment.Center,
        };
        ToolTipService.SetToolTip(button, tip);
        return button;
    }

    void BuildAlbumLayer()
    {
        albumLayer.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(56) });
        albumLayer.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
        albumLayer.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(56) });
        albumLayer.Children.Add(previous);
        Grid.SetColumn(next, 2);
        albumLayer.Children.Add(next);

        albumContent.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1.1, GridUnitType.Star) });
        albumContent.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
        albumContent.Margin = new Thickness(0, 36, 0, 16);
        Grid.SetColumn(albumContent, 1);
        albumLayer.Children.Add(albumContent);

        songScroll.Content = songList;
        albumText.Children.Add(albumTitle);
        albumText.Children.Add(albumYear);
        albumText.Children.Add(songScroll);
        bubbleHost.Children.Add(albumText);
        albumContent.Children.Add(bubbleHost);

        lyricsScroll.Content = lyricsText;
        lyricsBlock.Children.Add(lyricsTitle);
        lyricsBlock.Children.Add(lyricsScroll);
        lyricsBlock.VerticalAlignment = VerticalAlignment.Center;
        lyricsArea.Children.Add(risers);
        lyricsArea.Children.Add(lyricsBlock);
        Grid.SetColumn(lyricsArea, 1);
        albumContent.Children.Add(lyricsArea);
    }

    /// <summary>Opens on the double-clicked song's album, zoomed in, with that song's lyrics; or, with
    /// <paramref name="allAlbums"/> (an agent's check), on every album.</summary>
    public void Show(ArtistAlbums albums, bool allAlbums = false)
    {
        data = albums;
        album = albums.StartAlbum;
        song = Math.Max(0, albums.StartSong);
        zoomed = !allAlbums;
        Paint();
        SetLayers(zoomedIn: zoomed);
        if (!SearchWindow.Offscreen) Focus(FocusState.Programmatic);
    }

    // -- drawing --

    void Paint()
    {
        p = Bubbles.For(ActualTheme);
        stage.Background = new SolidColorBrush(p.Background);
        var text = new SolidColorBrush(p.Text);
        var secondary = new SolidColorBrush(p.Secondary);
        albumTitle.Foreground = lyricsText.Foreground = text;
        albumYear.Foreground = lyricsTitle.Foreground = secondary;
        foreach (var link in new[] { back, allAlbums }) link.Foreground = secondary;
        foreach (var arrow in new[] { previous, next })
        {
            arrow.Background = new SolidColorBrush(p.Fill);
            arrow.BorderBrush = new SolidColorBrush(p.Rim);
            arrow.Foreground = text;
        }
        Layout();
    }

    void Layout()
    {
        if (data is null || ActualWidth <= 0 || ActualHeight <= 0) return;
        DrawOverview();
        DrawAlbum();
    }

    /// <summary>The artist on the left, the albums to its right along soft branches, staggered, every one on screen.</summary>
    void DrawOverview()
    {
        overview.Children.Clear();
        albumSpots.Clear();
        double width = ActualWidth, height = ActualHeight, middle = height / 2;
        var artistSize = Math.Clamp(height * 0.3, 70, 130);
        var artistCenter = new Point(28 + artistSize / 2, middle);
        var count = data!.Albums.Count;
        var left = artistCenter.X + artistSize / 2 + 50;
        var spacing = Math.Max(1, (width - left - 40) / count);
        var radius = Math.Clamp(Math.Min(spacing * 0.4, height * 0.15), 18, 60);
        for (var i = 0; i < count; i++)
        {
            var center = new Point(left + spacing * (i + 0.5),
                middle + (count > 1 ? Stagger[i % Stagger.Length] * height : 0));
            center.Y = Math.Clamp(center.Y, radius + 40, height - radius - 30);
            albumSpots.Add((center, radius));
            var branch = new Microsoft.UI.Xaml.Shapes.Path
            {
                Stroke = new SolidColorBrush(p.Line),
                StrokeThickness = 1.2,
                Data = Curve(new Point(artistCenter.X + artistSize / 2, artistCenter.Y), new Point(center.X - radius, center.Y)),
                IsHitTestVisible = false,
            };
            overview.Children.Add(branch);
        }
        var artist = Labelled(artistSize, data.Artist, null, 15);
        Place(artist, artistCenter, artistSize);
        Bubbles.Drift(artist, 4, 1, 9);
        for (var i = 0; i < count; i++)
        {
            var (center, radius2) = albumSpots[i];
            var each = data.Albums[i];
            var inside = radius2 >= 38;
            var bubble = Labelled(radius2 * 2, inside ? each.Name : null, inside ? each.Year : null, 12);
            Place(bubble, center, radius2 * 2);
            ToolTipService.SetToolTip(bubble, $"{each.Name}{(each.Year is null ? "" : $" ({each.Year})")} · {each.Songs.Count} songs · double-click to open");
            var index = i;
            bubble.DoubleTapped += (_, _) => ZoomIn(index);
            Bubbles.Drift(bubble, 5, 10 + i);
            if (!inside)
            {
                var label = new TextBlock
                {
                    Text = each.Year is null ? each.Name : $"{each.Name}\n{each.Year}",
                    FontSize = 11, TextAlignment = TextAlignment.Center, TextWrapping = TextWrapping.Wrap,
                    Width = Math.Max(70, spacing - 6), Foreground = new SolidColorBrush(p.Text), IsHitTestVisible = false,
                };
                Canvas.SetLeft(label, center.X - label.Width / 2);
                Canvas.SetTop(label, center.Y + radius2 + 16);
                overview.Children.Add(label);
                Bubbles.Drift(label, 3, 40 + i);
            }
            var shown = Math.Min(each.Songs.Count, 18);
            for (var j = 0; j < shown; j++)
            {
                var angle = -Math.PI / 2 + j * 2 * Math.PI / shown;
                var dot = Bubbles.Make(9, p);
                dot.IsHitTestVisible = false;
                Place(dot, new Point(center.X + Math.Cos(angle) * (radius2 + 12), center.Y + Math.Sin(angle) * (radius2 + 12)), 9);
                Bubbles.Drift(dot, 3, 100 + i * 31 + j, 6);
            }
        }
    }

    static Geometry Curve(Point from, Point to)
    {
        var figure = new PathFigure { StartPoint = from };
        var bend = (to.X - from.X) * 0.45;
        figure.Segments.Add(new BezierSegment
        {
            Point1 = new Point(from.X + bend, from.Y),
            Point2 = new Point(to.X - bend, to.Y),
            Point3 = to,
        });
        var geometry = new PathGeometry();
        geometry.Figures.Add(figure);
        return geometry;
    }

    Grid Labelled(double size, string? title, string? year, double fontSize)
    {
        var bubble = Bubbles.Make(size, p);
        if (title is null) return bubble;
        var text = new StackPanel { VerticalAlignment = VerticalAlignment.Center, HorizontalAlignment = HorizontalAlignment.Center, MaxWidth = size * 0.78, IsHitTestVisible = false };
        text.Children.Add(new TextBlock
        {
            Text = title, FontSize = fontSize, TextAlignment = TextAlignment.Center, TextWrapping = TextWrapping.Wrap,
            MaxLines = 3, TextTrimming = TextTrimming.CharacterEllipsis, Foreground = new SolidColorBrush(p.Text),
        });
        if (year is not null)
            text.Children.Add(new TextBlock { Text = year, FontSize = 10, TextAlignment = TextAlignment.Center, Foreground = new SolidColorBrush(p.Secondary) });
        bubble.Children.Add(text);
        return bubble;
    }

    void Place(FrameworkElement element, Point center, double size)
    {
        Canvas.SetLeft(element, center.X - size / 2);
        Canvas.SetTop(element, center.Y - size / 2);
        overview.Children.Add(element);
    }

    /// <summary>The open album: its big bubble with the songs, and the chosen song's lyrics.</summary>
    void DrawAlbum()
    {
        var each = data!.Albums[album];
        var columnWidth = Math.Max(160, (ActualWidth - 112) * 1.1 / 2.1);
        var size = Math.Max(160, Math.Min(ActualHeight - 60, columnWidth - 12));
        if (bubbleHost.Children.Count > 1) bubbleHost.Children.RemoveAt(0);
        var big = Bubbles.Make(size, p);
        big.IsHitTestVisible = false;
        bubbleHost.Children.Insert(0, big);
        Bubbles.Drift(big, 3, 7, 12);
        albumText.Width = size * 0.66;
        songScroll.MaxHeight = size * 0.5;
        albumTitle.Text = each.Name;
        albumYear.Text = each.Year ?? "";
        albumYear.Visibility = each.Year is null ? Visibility.Collapsed : Visibility.Visible;
        previous.Visibility = album > 0 ? Visibility.Visible : Visibility.Collapsed;
        next.Visibility = album < data.Albums.Count - 1 ? Visibility.Visible : Visibility.Collapsed;
        ToolTipService.SetToolTip(previous, album > 0 ? $"{data.Albums[album - 1].Name} (Left)" : null);
        ToolTipService.SetToolTip(next, album < data.Albums.Count - 1 ? $"{data.Albums[album + 1].Name} (Right)" : null);
        DrawSongs();
        DrawRisers();
        ShowLyrics(fade: false);
    }

    void DrawSongs()
    {
        var each = data!.Albums[album];
        songList.Children.Clear();
        for (var j = 0; j < each.Songs.Count; j++)
        {
            var item = each.Songs[j];
            var row = new Grid { ColumnSpacing = 8, Padding = new Thickness(2, 3, 2, 3), Background = new SolidColorBrush(Microsoft.UI.Colors.Transparent) };
            row.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });
            row.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
            var dot = Bubbles.Make(15, p, lit: j == song);
            dot.VerticalAlignment = VerticalAlignment.Center;
            if (j == song) dot.Children.Add(new Ellipse { Width = 23, Height = 23, Margin = new Thickness(-4), Stroke = new SolidColorBrush(p.Sheen2), StrokeThickness = 1, Opacity = 0.7 });
            row.Children.Add(dot);
            var title = new TextBlock
            {
                Text = item.Track is { } n ? $"{n}. {item.Title}" : item.Title, FontSize = 13, TextTrimming = TextTrimming.CharacterEllipsis,
                Foreground = new SolidColorBrush(p.Text), FontWeight = j == song ? FontWeights.SemiBold : FontWeights.Normal,
                VerticalAlignment = VerticalAlignment.Center,
            };
            Grid.SetColumn(title, 1);
            row.Children.Add(title);
            row.Opacity = item.Lyrics is null ? 0.55 : 1;
            ToolTipService.SetToolTip(row, item.Lyrics is null ? $"{item.Title}: {item.Note.ToLowerInvariant()}" : $"{item.Title}: show its lyrics");
            var index = j;
            row.Tapped += (_, _) => Choose(index);
            songList.Children.Add(row);
            Bubbles.Drift(row, 1.5f, 200 + j, 10);
        }
    }

    /// <summary>Tiny bubbles rising behind the lyrics, slowly, out of step.</summary>
    void DrawRisers()
    {
        risers.Children.Clear();
        if (!Bubbles.Animate) return;
        var height = Math.Max(200, ActualHeight - 60);
        var width = Math.Max(160, (ActualWidth - 112) / 2.1);
        var random = new Random(album * 17 + 3);
        for (var k = 0; k < 9; k++)
        {
            var size = 4 + random.NextDouble() * 9;
            var dot = Bubbles.Make(size, p);
            Canvas.SetLeft(dot, random.NextDouble() * (width - size));
            Canvas.SetTop(dot, height - size);
            risers.Children.Add(dot);
            Bubbles.Rise(dot, (float)height, album * 50 + k);
        }
    }

    void Choose(int index)
    {
        if (index == song) return;
        song = index;
        DrawSongs();
        ShowLyrics(fade: true);
    }

    void ShowLyrics(bool fade)
    {
        var item = data!.Albums[album].Songs[song];
        void Fill()
        {
            lyricsTitle.Text = item.Title.ToUpperInvariant();
            lyricsText.Text = item.Lyrics ?? item.Note;
            lyricsText.FontStyle = item.Lyrics is null ? Windows.UI.Text.FontStyle.Normal : Windows.UI.Text.FontStyle.Italic;
            lyricsText.Foreground = new SolidColorBrush(item.Lyrics is null ? p.Secondary : p.Text);
            lyricsScroll.MaxHeight = Math.Max(120, ActualHeight - 140);
            lyricsScroll.ChangeView(null, 0, null, disableAnimation: true);
        }
        Bubbles.Drift(lyricsBlock, 2.5f, 300, 12);
        if (!fade || !Bubbles.Animate)
        {
            Fill();
            return;
        }
        Run(Fade(lyricsBlock, 1, 0, 0.14), () =>
        {
            Fill();
            Run(Fade(lyricsBlock, 0, 1, 0.3), null);
        });
    }

    // -- moving between the views --

    void ZoomIn(int index)
    {
        if (moving || data is null) return;
        album = index;
        song = 0;
        zoomed = true;
        DrawAlbum();
        var (center, _) = albumSpots[index];
        Transition(center, zoomingIn: true);
    }

    void ZoomOut()
    {
        if (moving || !zoomed) return;
        zoomed = false;
        var (center, _) = albumSpots[album];
        Transition(center, zoomingIn: false);
    }

    /// <summary>Into an album: the albums swell from its bubble and fade while the album grows out of it; out, the
    /// reverse. Instant when animations are off.</summary>
    void Transition(Point center, bool zoomingIn)
    {
        foreach (var layer in new FrameworkElement[] { overview, albumLayer })
        {
            var transform = (CompositeTransform)layer.RenderTransform;
            transform.CenterX = center.X;
            transform.CenterY = center.Y;
        }
        overview.Visibility = albumLayer.Visibility = Visibility.Visible;
        allAlbums.Visibility = Visibility.Collapsed;
        moving = true;
        var board = new Storyboard();
        Add(board, overview, zoomingIn ? 1 : 2.4, zoomingIn ? 2.4 : 1, zoomingIn ? 1 : 0, zoomingIn ? 0 : 1);
        Add(board, albumLayer, zoomingIn ? 0.35 : 1, zoomingIn ? 1 : 0.35, zoomingIn ? 0 : 1, zoomingIn ? 1 : 0);
        Run(board, () =>
        {
            moving = false;
            SetLayers(zoomingIn);
        });
    }

    static void Add(Storyboard board, UIElement target, double fromScale, double toScale, double fromOpacity, double toOpacity)
    {
        foreach (var axis in new[] { "ScaleX", "ScaleY" })
        {
            var scale = new DoubleAnimation
            {
                From = fromScale, To = toScale, Duration = Seconds(ZoomSeconds),
                EasingFunction = new CubicEase { EasingMode = EasingMode.EaseInOut },
            };
            Storyboard.SetTarget(scale, target);
            Storyboard.SetTargetProperty(scale, $"(UIElement.RenderTransform).(CompositeTransform.{axis})");
            board.Children.Add(scale);
        }
        var opacity = new DoubleAnimation { From = fromOpacity, To = toOpacity, Duration = Seconds(ZoomSeconds) };
        Storyboard.SetTarget(opacity, target);
        Storyboard.SetTargetProperty(opacity, "Opacity");
        board.Children.Add(opacity);
    }

    /// <summary>The previous or next album: the open one slides out on its side and the other slides in.</summary>
    void Cycle(int step)
    {
        if (moving || !zoomed || data is null) return;
        var target = album + step;
        if (target < 0 || target >= data.Albums.Count) return;
        moving = true;
        Run(Slide(albumContent, 0, -step * 60, 1, 0), () =>
        {
            album = target;
            song = 0;
            DrawAlbum();
            Run(Slide(albumContent, step * 60, 0, 0, 1), () => moving = false);
        });
    }

    static Storyboard Slide(UIElement target, double from, double to, double fromOpacity, double toOpacity)
    {
        var board = new Storyboard();
        var move = new DoubleAnimation
        {
            From = from, To = to, Duration = Seconds(CycleSeconds), EasingFunction = new CubicEase { EasingMode = EasingMode.EaseOut },
        };
        Storyboard.SetTarget(move, target);
        Storyboard.SetTargetProperty(move, "(UIElement.RenderTransform).(CompositeTransform.TranslateX)");
        board.Children.Add(move);
        var fade = new DoubleAnimation { From = fromOpacity, To = toOpacity, Duration = Seconds(CycleSeconds) };
        Storyboard.SetTarget(fade, target);
        Storyboard.SetTargetProperty(fade, "Opacity");
        board.Children.Add(fade);
        return board;
    }

    static Storyboard Fade(UIElement target, double from, double to, double seconds)
    {
        var board = new Storyboard();
        var fade = new DoubleAnimation { From = from, To = to, Duration = Seconds(seconds) };
        Storyboard.SetTarget(fade, target);
        Storyboard.SetTargetProperty(fade, "Opacity");
        board.Children.Add(fade);
        return board;
    }

    static Duration Seconds(double seconds) => new(TimeSpan.FromSeconds(Bubbles.Animate ? seconds : 0));

    static void Run(Storyboard board, Action? done)
    {
        if (done is not null) board.Completed += (_, _) => done();
        board.Begin();
    }

    /// <summary>One view at rest: the album's or the albums', the other hidden, both at their normal size.</summary>
    void SetLayers(bool zoomedIn)
    {
        foreach (var layer in new FrameworkElement[] { overview, albumLayer })
        {
            var transform = (CompositeTransform)layer.RenderTransform;
            transform.ScaleX = transform.ScaleY = 1;
        }
        overview.Opacity = albumLayer.Opacity = 1;
        overview.Visibility = zoomedIn ? Visibility.Collapsed : Visibility.Visible;
        albumLayer.Visibility = zoomedIn ? Visibility.Visible : Visibility.Collapsed;
        allAlbums.Visibility = zoomedIn && data?.Albums.Count > 0 ? Visibility.Visible : Visibility.Collapsed;
    }

    void OnKey(object sender, KeyRoutedEventArgs e)
    {
        switch (e.Key)
        {
            case VirtualKey.Escape when zoomed:
                ZoomOut();
                break;
            case VirtualKey.Escape:
                Closed?.Invoke();
                break;
            case VirtualKey.Left when zoomed:
                Cycle(-1);
                break;
            case VirtualKey.Right when zoomed:
                Cycle(1);
                break;
            case VirtualKey.Up when zoomed && song > 0:
                Choose(song - 1);
                break;
            case VirtualKey.Down when zoomed && data is not null && song < data.Albums[album].Songs.Count - 1:
                Choose(song + 1);
                break;
            default:
                return;
        }
        e.Handled = true;
    }
}
