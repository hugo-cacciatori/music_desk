using Microsoft.Data.Sqlite;
using Microsoft.UI.Text;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Controls.Primitives;
using Microsoft.UI.Xaml.Input;
using Microsoft.UI.Xaml.Media;
using Microsoft.UI.Xaml.Media.Animation;
using Microsoft.UI.Xaml.Shapes;
using Refrain.Core;
using Refrain.Core.Playback;
using Refrain.Core.Search;
using Windows.Foundation;
using Windows.System;

namespace Refrain.Views;

/// <summary>The discography view (SPEC §36 "11.", §37 "The library by tags"), at three levels the mouse wheel moves
/// through: the <b>library</b>, every artist as a bubble on a map (dragged to pan, the wheel zooms toward the pointer
/// and, close enough, into the artist under it); an <b>artist</b>, its bubble on the left and the albums branching to its
/// right, earliest first, all on one screen; an <b>album</b>, its songs listed inside a big bubble, a click on one
/// showing its lyrics beside the album, among rising bubbles. Albums and artists come from the tags of the whole library,
/// wherever the files are (<see cref="ArtistAlbums"/>). The info panel on the right shows what is open; a right-click
/// opens File Explorer on it. Everything floats slowly. The view only reads the index: nothing is looked up or written.</summary>
public sealed partial class DiscographyView : UserControl
{
    enum Level { Library, Artist, Album }

    const double ZoomSeconds = 0.5, CycleSeconds = 0.26;
    const double Cell = 150, MapStep = 1.18, MaxMapScale = 2.6, EnterRadius = 150;
    static readonly double[] Stagger = [-0.22, 0.14, -0.16, 0.19, -0.2, 0.12];  // the albums' heights, as sketched

    readonly Core.Library index;
    readonly Settings settings;
    readonly Player player;
    // The shown song's synced lyrics, one block per line, and the line lit as it is sung (SPEC §37).
    readonly StackPanel lyricsLines = new() { Spacing = 6 };
    List<SyncedLine> synced = [];
    int sungLine = -1;
    readonly Grid stage = new();
    readonly Grid view = new() { Background = new SolidColorBrush(Microsoft.UI.Colors.Transparent) };
    readonly Grid mapLayer = new() { Background = new SolidColorBrush(Microsoft.UI.Colors.Transparent) };
    readonly Canvas map = new();
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
    readonly Button previous = Arrow("", "Earlier album (Left)");
    readonly Button next = Arrow("", "Later album (Right)");
    readonly StackPanel links = new() { Orientation = Orientation.Horizontal, HorizontalAlignment = HorizontalAlignment.Left, VerticalAlignment = VerticalAlignment.Top, Margin = new Thickness(8, 4, 0, 0) };
    readonly HyperlinkButton back = new() { Content = "‹ Results" };
    readonly HyperlinkButton toLibrary = new() { Content = "Back to library" };
    readonly HyperlinkButton toArtist = new();
    readonly Button infoToggle = new()
    {
        Content = new FontIcon { Glyph = "", FontSize = 14 }, Width = 34, Height = 34, Padding = new Thickness(0),
        CornerRadius = new CornerRadius(17), HorizontalAlignment = HorizontalAlignment.Right, VerticalAlignment = VerticalAlignment.Top,
        Margin = new Thickness(0, 6, 10, 0), Background = new SolidColorBrush(Microsoft.UI.Colors.Transparent), BorderThickness = new Thickness(0),
    };
    readonly Border panel = new() { Width = 290, BorderThickness = new Thickness(1, 0, 0, 0) };
    readonly StackPanel panelBody = new() { Spacing = 2, Padding = new Thickness(18, 14, 18, 18) };

    Theme.Palette p = Theme.For(ElementTheme.Light);
    Level level = Level.Album;
    ArtistAlbums? data;
    int album;
    int song;
    bool moving;
    long quietUntil;
    int panelVersion;
    readonly List<(Point Center, double Radius)> albumSpots = [];

    // The library level: its artists, their places on the map (world units), the lit ones (Open in Refrain on a folder
    // of several artists), and the map's zoom and pan; only the bubbles on screen exist.
    List<LibraryArtist> artists = [];
    Point[] spots = [];
    double[] radii = [];
    Size world;
    HashSet<string> lit = [];
    double scale = 1, panX, panY;
    bool mapPlaced;
    readonly Dictionary<int, FrameworkElement> shown = [];
    Point? pressedAt;
    (double X, double Y) pressedPan;
    bool dragging;

    /// <summary>Esc on the library, or ‹ Results: back to the search's results, as they were.</summary>
    public event Action? Closed;

    double W => view.ActualWidth;
    double H => view.ActualHeight;
    bool ZoomedIn => level == Level.Album;

    public DiscographyView(Core.Library index, Settings settings, Player player)
    {
        this.index = index;
        this.settings = settings;
        this.player = player;
        player.Changed += FollowPlayer;
        player.Ticked += ShowSungLine;
        lyricsLines.RenderTransform = null;
        IsTabStop = true;
        mapLayer.Children.Add(map);
        mapLayer.Clip = null;
        map.RenderTransform = new CompositeTransform();
        view.Children.Add(mapLayer);
        view.Children.Add(overview);
        BuildAlbumLayer();
        view.Children.Add(albumLayer);
        links.Children.Add(back);
        links.Children.Add(toLibrary);
        links.Children.Add(toArtist);
        view.Children.Add(links);
        view.Children.Add(infoToggle);
        stage.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
        stage.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });
        stage.Children.Add(view);
        panel.Child = new ScrollViewer { Content = panelBody, HorizontalScrollMode = ScrollMode.Disabled };
        Grid.SetColumn(panel, 1);
        stage.Children.Add(panel);
        Content = stage;
        foreach (var layer in new UIElement[] { mapLayer, overview, albumLayer }) layer.RenderTransform = new CompositeTransform();
        albumContent.RenderTransform = new CompositeTransform();
        lyricsBlock.RenderTransform = new CompositeTransform();
        view.SizeChanged += (_, _) => Layout();
        // The panel's closing is remembered between sessions, not between installs (SPEC §37): it counts only for the
        // version that recorded it.
        panel.Visibility = settings.InfoPanelClosed && settings.InfoPanelClosedIn == Version ? Visibility.Collapsed : Visibility.Visible;
        ToolTipService.SetToolTip(back, "Back to the results (Esc on the library)");
        ToolTipService.SetToolTip(toLibrary, "Every artist of the library (zoom out with the mouse wheel)");
        ToolTipService.SetToolTip(infoToggle, "Show or hide the info panel");

        back.Click += (_, _) => Closed?.Invoke();
        toLibrary.Click += (_, _) => _ = ToLibrary();
        toArtist.Click += (_, _) => ZoomOut();
        previous.Click += (_, _) => Cycle(-1);
        next.Click += (_, _) => Cycle(1);
        infoToggle.Click += (_, _) => TogglePanel();
        view.AddHandler(PointerWheelChangedEvent, new PointerEventHandler(OnWheel), false);
        view.RightTapped += OnRightTapped;
        mapLayer.PointerPressed += OnMapPressed;
        mapLayer.PointerMoved += OnMapMoved;
        mapLayer.PointerReleased += OnMapReleased;
        mapLayer.PointerCaptureLost += (_, _) => (pressedAt, dragging) = (null, false);
        ActualThemeChanged += (_, _) => Paint();
        KeyDown += OnKey;
    }

    static string Version => typeof(DiscographyView).Assembly.GetName().Version?.ToString() ?? "";

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

    /// <summary>Opens on the start song's album, zoomed in, with that song's lyrics; or, with
    /// <paramref name="allAlbums"/>, on the artist's albums.</summary>
    public void Show(ArtistAlbums albums, bool allAlbums = false)
    {
        data = albums;
        album = albums.StartAlbum;
        song = Math.Max(0, albums.StartSong);
        Paint();
        SetLevel(allAlbums ? Level.Artist : Level.Album);
        if (!SearchWindow.Offscreen) Focus(FocusState.Programmatic);
    }

    /// <summary>Opens on the library level, with <paramref name="highlight"/>'s artists lit and in view (Open in Refrain
    /// on a folder of several artists); the whole library when none.</summary>
    public async Task ShowLibrary(IReadOnlyCollection<string> highlight)
    {
        lit = [.. highlight];
        await LoadArtists();
        Paint();
        FitMap(highlight.Count > 0 ? Enumerable.Range(0, artists.Count).Where(i => lit.Contains(artists[i].Key)) : null);
        SetLevel(Level.Library);
        if (!SearchWindow.Offscreen) Focus(FocusState.Programmatic);
    }

    Task<T> Read<T>(Func<SqliteConnection, T> query) => Task.Run(() =>
    {
        using var conn = index.Db.Read();
        return query(conn);
    });

    // -- drawing --

    void Paint()
    {
        p = Theme.For(ActualTheme);
        stage.Background = new SolidColorBrush(p.Background);
        var text = new SolidColorBrush(p.Text);
        var secondary = new SolidColorBrush(p.Secondary);
        albumTitle.Foreground = lyricsText.Foreground = text;
        albumYear.Foreground = lyricsTitle.Foreground = secondary;
        foreach (var link in new[] { back, toLibrary, toArtist }) link.Foreground = secondary;
        infoToggle.Foreground = secondary;
        foreach (var arrow in new[] { previous, next })
        {
            arrow.Background = new SolidColorBrush(p.Fill);
            arrow.BorderBrush = new SolidColorBrush(p.Rim);
            arrow.Foreground = text;
        }
        panel.Background = new SolidColorBrush(p.Fill);
        panel.BorderBrush = new SolidColorBrush(p.Line);
        foreach (var bubble in shown.Values) map.Children.Remove(bubble);
        shown.Clear();
        Layout();
        _ = RefreshPanel();
    }

    void Layout()
    {
        if (W <= 0 || H <= 0) return;
        if (artists.Count > 0) ApplyMap();
        if (data is null) return;
        DrawOverview();
        DrawAlbum();
    }

    /// <summary>The artist on the left, the albums to its right along soft branches, staggered, every one on screen.</summary>
    void DrawOverview()
    {
        overview.Children.Clear();
        albumSpots.Clear();
        double width = W, height = H, middle = height / 2;
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
        Place(overview, artist, artistCenter, artistSize);
        Bubbles.Drift(artist, 4, 1, 9);
        for (var i = 0; i < count; i++)
        {
            var (center, radius2) = albumSpots[i];
            var each = data.Albums[i];
            var inside = radius2 >= 38;
            var bubble = Labelled(radius2 * 2, inside ? each.Name : null, inside ? each.Year : null, 12);
            bubble.Tag = each;
            Place(overview, bubble, center, radius2 * 2);
            ToolTipService.SetToolTip(bubble, $"{each.Name}{(each.Year is null ? "" : $" ({each.Year})")} · {each.Songs.Count} songs · double-click to open");
            var index2 = i;
            bubble.DoubleTapped += (_, _) => ZoomIn(index2);
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
            var shownSongs = Math.Min(each.Songs.Count, 18);
            for (var j = 0; j < shownSongs; j++)
            {
                var angle = -Math.PI / 2 + j * 2 * Math.PI / shownSongs;
                var dot = Bubbles.Make(9, p);
                dot.IsHitTestVisible = false;
                Place(overview, dot, new Point(center.X + Math.Cos(angle) * (radius2 + 12), center.Y + Math.Sin(angle) * (radius2 + 12)), 9);
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

    Grid Labelled(double size, string? title, string? year, double fontSize, bool isLit = false)
    {
        var bubble = Bubbles.Make(size, p, isLit);
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

    static void Place(Canvas canvas, FrameworkElement element, Point center, double size)
    {
        Canvas.SetLeft(element, center.X - size / 2);
        Canvas.SetTop(element, center.Y - size / 2);
        canvas.Children.Add(element);
    }

    /// <summary>The open album: its big bubble with the songs, and the chosen song's lyrics.</summary>
    void DrawAlbum()
    {
        var each = data!.Albums[album];
        var columnWidth = Math.Max(160, (W - 112) * 1.1 / 2.1);
        var size = Math.Max(160, Math.Min(H - 60, columnWidth - 12));
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
            var row = new Grid { ColumnSpacing = 8, Padding = new Thickness(2, 3, 2, 3), Background = new SolidColorBrush(Microsoft.UI.Colors.Transparent), Tag = item };
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
            var index2 = j;
            row.Tapped += (_, _) => Choose(index2);
            songList.Children.Add(row);
            Bubbles.Drift(row, 1.5f, 200 + j, 10);
        }
    }

    /// <summary>Tiny bubbles rising behind the lyrics, slowly, out of step.</summary>
    void DrawRisers()
    {
        risers.Children.Clear();
        if (!Bubbles.Animate) return;
        var height = Math.Max(200, H - 60);
        var width = Math.Max(160, (W - 112) / 2.1);
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

    /// <summary>A song chosen in the album (a click, Up or Down) plays at once (SPEC §37).</summary>
    void Choose(int index2)
    {
        if (index2 != song)
        {
            song = index2;
            DrawSongs();
            ShowLyrics(fade: true);
            _ = RefreshPanel();
        }
        if (player.Current?.Id != data!.Albums[album].Songs[song].Id) _ = PlayShown(TimeSpan.Zero);
    }

    /// <summary>The shown song from <paramref name="at"/>, its album as the player's queue.</summary>
    async Task PlayShown(TimeSpan at)
    {
        if (data is not { } shownData) return;
        var (shownAlbum, shownSong) = (album, song);
        var (queue, start) = await Read(conn => PlayerSong.QueueOf(conn, shownData, shownAlbum, shownSong));
        if (queue.Count > 0) player.Play(queue, start, at);
    }

    /// <summary>The player moved to another song of the album on screen (the album going on): the view follows it.</summary>
    void FollowPlayer()
    {
        if (player.Current is not { } current || data is null || current.ArtistKey != data.Key) return;
        var songs = data.Albums[album].Songs;
        var at = songs.ToList().FindIndex(s => s.Id == current.Id);
        if (at >= 0 && at != song)
        {
            song = at;
            DrawSongs();
            ShowLyrics(fade: level == Level.Album);
            _ = RefreshPanel();
        }
        ShowSungLine();
    }

    /// <summary>Opens the player's song in its album (a click on the bar's title, SPEC §37).</summary>
    public async Task ShowSong(PlayerSong current)
    {
        if (await Read(conn => ArtistAlbums.ForArtist(conn, current.ArtistKey, current.Id)) is { } found) Show(found);
    }

    /// <summary>The sung line lights up and comes to the middle, while the shown song is the one playing.</summary>
    void ShowSungLine()
    {
        if (synced.Count == 0 || data is null) return;
        var playing = player.Current?.Id == data.Albums[album].Songs[song].Id;
        var now = playing ? SyncedLyrics.IndexAt(synced, player.Position) : -1;
        if (now == sungLine) return;
        for (var i = 0; i < lyricsLines.Children.Count; i++)
            if (lyricsLines.Children[i] is TextBlock line)
            {
                line.Foreground = new SolidColorBrush(!playing || i == now ? p.Text : p.Secondary);
                line.FontWeight = i == now ? FontWeights.SemiBold : FontWeights.Normal;
            }
        sungLine = now;
        if (now >= 0 && lyricsLines.Children[now] is TextBlock sung && level == Level.Album)
        {
            var top = sung.TransformToVisual(lyricsLines).TransformPoint(new Point(0, 0)).Y;
            lyricsScroll.ChangeView(null, Math.Max(0, top - lyricsScroll.ViewportHeight / 2 + sung.ActualHeight / 2), null, disableAnimation: !Bubbles.Animate);
        }
    }

    void ShowLyrics(bool fade)
    {
        var item = data!.Albums[album].Songs[song];
        void Fill()
        {
            lyricsTitle.Text = item.Title.ToUpperInvariant();
            // Synced lyrics show line by line, each a click away from its moment (SPEC §37); plain lyrics as a text.
            synced = SyncedLyrics.Parse(item.Synced).Where(l => l.Text.Length > 0).ToList();
            sungLine = -2;
            lyricsLines.Children.Clear();
            foreach (var line in synced)
            {
                var block = new TextBlock
                {
                    Text = line.Text, FontSize = 16, FontStyle = Windows.UI.Text.FontStyle.Italic, FontFamily = lyricsText.FontFamily,
                    TextWrapping = TextWrapping.Wrap, Foreground = new SolidColorBrush(p.Text),
                };
                ToolTipService.SetToolTip(block, $"Play from {SongInfo.Length(line.At.TotalSeconds)}");
                var at = line.At;
                block.Tapped += (_, _) =>
                {
                    if (player.Current?.Id == item.Id) player.Seek(at);
                    else _ = PlayShown(at);
                };
                lyricsLines.Children.Add(block);
            }
            lyricsScroll.Content = synced.Count > 0 ? lyricsLines : lyricsText;
            lyricsText.Text = item.Lyrics ?? item.Note;
            lyricsText.FontStyle = item.Lyrics is null ? Windows.UI.Text.FontStyle.Normal : Windows.UI.Text.FontStyle.Italic;
            lyricsText.Foreground = new SolidColorBrush(item.Lyrics is null ? p.Secondary : p.Text);
            lyricsScroll.MaxHeight = Math.Max(120, H - 140);
            lyricsScroll.ChangeView(null, 0, null, disableAnimation: true);
            ShowSungLine();
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

    // -- the library map --

    async Task LoadArtists()
    {
        var loaded = await Read(ArtistAlbums.Library);
        if (loaded.Select(a => (a.Key, a.Albums)).SequenceEqual(artists.Select(a => (a.Key, a.Albums))) && spots.Length > 0)
        {
            artists = loaded;
            return;
        }
        artists = loaded;
        foreach (var bubble in shown.Values) map.Children.Remove(bubble);
        shown.Clear();
        // A hexagonal grid as wide as the view's shape, each bubble nudged off its cell and sized by its albums.
        var count = Math.Max(1, artists.Count);
        var aspect = W > 0 && H > 0 ? W / H : 1.5;
        var columns = Math.Max(1, (int)Math.Ceiling(Math.Sqrt(count * aspect / 0.87)));
        var rows = (int)Math.Ceiling(count / (double)columns);
        spots = new Point[artists.Count];
        radii = new double[artists.Count];
        for (var i = 0; i < artists.Count; i++)
        {
            var random = new Random(artists[i].Key.GetHashCode(StringComparison.Ordinal));
            int row = i / columns, column = i % columns;
            spots[i] = new Point(Cell * (column + 0.5 + (row % 2) * 0.5) + (random.NextDouble() - 0.5) * 24,
                Cell * 0.87 * (row + 0.5) + Cell * 0.1 + (random.NextDouble() - 0.5) * 24);
            radii[i] = Math.Clamp(26 + 11 * Math.Sqrt(artists[i].Albums), 28, 66);
        }
        world = new Size(Cell * (columns + 0.5), Cell * 0.87 * rows + Cell * 0.2);
        mapPlaced = false;
    }

    double MinScale => Math.Min(1.4, Math.Min(W / Math.Max(1, world.Width), H / Math.Max(1, world.Height)));
    double MaxScale => Math.Max(MinScale, MaxMapScale);

    /// <summary>The map zoomed and panned to show <paramref name="which"/> (all artists when null), whole.</summary>
    void FitMap(IEnumerable<int>? which)
    {
        if (W <= 0 || H <= 0 || artists.Count == 0) return;
        var list = (which ?? Enumerable.Range(0, artists.Count)).ToList();
        if (list.Count == 0) list = [.. Enumerable.Range(0, artists.Count)];
        double left = list.Min(i => spots[i].X - radii[i]), right = list.Max(i => spots[i].X + radii[i]);
        double top = list.Min(i => spots[i].Y - radii[i]), bottom = list.Max(i => spots[i].Y + radii[i]);
        scale = Math.Clamp(Math.Min(W / (right - left + 80), H / (bottom - top + 100)), MinScale, Math.Min(1.4, MaxScale));
        panX = W / 2 - (left + right) / 2 * scale;
        panY = H / 2 - (top + bottom) / 2 * scale;
        mapPlaced = true;
        ApplyMap();
    }

    /// <summary>The map's zoom and pan within bounds (a map smaller than the view is centred), then only the bubbles on
    /// screen (and a cell around it) exist.</summary>
    void ApplyMap()
    {
        if (W <= 0 || H <= 0 || artists.Count == 0) return;
        if (!mapPlaced)
        {
            FitMap(null);
            return;
        }
        scale = Math.Clamp(scale, MinScale, MaxScale);
        panX = world.Width * scale <= W ? (W - world.Width * scale) / 2 : Math.Clamp(panX, W - world.Width * scale, 0);
        panY = world.Height * scale <= H ? (H - world.Height * scale) / 2 : Math.Clamp(panY, H - world.Height * scale, 0);
        var transform = (CompositeTransform)map.RenderTransform;
        transform.ScaleX = transform.ScaleY = scale;
        transform.TranslateX = panX;
        transform.TranslateY = panY;
        double left = -panX / scale - Cell, top = -panY / scale - Cell, right = left + W / scale + 2 * Cell, bottom = top + H / scale + 2 * Cell;
        for (var i = 0; i < artists.Count; i++)
        {
            var visible = spots[i].X >= left && spots[i].X <= right && spots[i].Y >= top && spots[i].Y <= bottom;
            if (visible && !shown.ContainsKey(i)) shown[i] = MakeArtist(i);
            else if (!visible && shown.Remove(i, out var gone)) map.Children.Remove(gone);
        }
    }

    FrameworkElement MakeArtist(int i)
    {
        var artist = artists[i];
        var size = radii[i] * 2;
        var bubble = Labelled(size, artist.Name, artist.Albums == 1 ? "1 album" : $"{artist.Albums} albums", 12, lit.Contains(artist.Key));
        bubble.Tag = artist;
        ToolTipService.SetToolTip(bubble, $"{artist.Name} · {artist.Albums} albums · {artist.Songs} songs · double-click to open");
        var which = i;
        bubble.DoubleTapped += (_, _) => _ = EnterArtist(which);
        Place(map, bubble, spots[i], size);
        Bubbles.Drift(bubble, 3, i, 8);
        return bubble;
    }

    int? ArtistAt(Point at)
    {
        var x = (at.X - panX) / scale;
        var y = (at.Y - panY) / scale;
        for (var i = 0; i < artists.Count; i++)
            if (Math.Pow(spots[i].X - x, 2) + Math.Pow(spots[i].Y - y, 2) <= radii[i] * radii[i]) return i;
        return null;
    }

    int? AlbumAt(Point at)
    {
        for (var i = 0; i < albumSpots.Count; i++)
        {
            var (center, radius) = albumSpots[i];
            if (Math.Pow(center.X - at.X, 2) + Math.Pow(center.Y - at.Y, 2) <= Math.Pow(radius + 14, 2)) return i;
        }
        return null;
    }

    void OnMapPressed(object sender, PointerRoutedEventArgs e)
    {
        if (!e.GetCurrentPoint(mapLayer).Properties.IsLeftButtonPressed) return;
        pressedAt = e.GetCurrentPoint(view).Position;
        pressedPan = (panX, panY);
        dragging = false;
    }

    void OnMapMoved(object sender, PointerRoutedEventArgs e)
    {
        if (pressedAt is not { } from) return;
        var at = e.GetCurrentPoint(view).Position;
        if (!dragging && Math.Abs(at.X - from.X) + Math.Abs(at.Y - from.Y) < 5) return;
        if (!dragging) dragging = mapLayer.CapturePointer(e.Pointer);
        panX = pressedPan.X + at.X - from.X;
        panY = pressedPan.Y + at.Y - from.Y;
        ApplyMap();
    }

    void OnMapReleased(object sender, PointerRoutedEventArgs e)
    {
        if (dragging) mapLayer.ReleasePointerCapture(e.Pointer);
        (pressedAt, dragging) = (null, false);
    }

    // -- moving between the levels --

    /// <summary>The wheel zooms through the levels (SPEC §37): on the library it zooms the map toward the pointer, and
    /// into the artist under it once that bubble is big; on an artist, up over an album opens it, down goes back to the
    /// library; on an album, down goes back to the artist. One level per wheel gesture.</summary>
    void OnWheel(object sender, PointerRoutedEventArgs e)
    {
        var point = e.GetCurrentPoint(view);
        var delta = point.Properties.MouseWheelDelta;
        if (delta == 0 || point.Properties.IsHorizontalMouseWheel) return;
        e.Handled = true;
        if (moving || Environment.TickCount64 < quietUntil) return;
        var at = point.Position;
        switch (level)
        {
            case Level.Library:
                if (delta > 0 && ArtistAt(at) is { } i && (radii[i] * scale * MapStep >= EnterRadius || scale >= MaxScale - 1e-6))
                {
                    _ = EnterArtist(i);
                    return;
                }
                var before = scale;
                scale = Math.Clamp(scale * Math.Pow(MapStep, delta / 120.0), MinScale, MaxScale);
                panX = at.X - (at.X - panX) * scale / before;
                panY = at.Y - (at.Y - panY) * scale / before;
                ApplyMap();
                break;
            case Level.Artist when delta > 0:
                if (AlbumAt(at) is { } a) ZoomIn(a);
                break;
            case Level.Artist:
                _ = ToLibrary();
                break;
            case Level.Album when delta < 0:
                ZoomOut();
                break;
        }
    }

    void Quiet() => quietUntil = Environment.TickCount64 + 450;

    async Task EnterArtist(int i)
    {
        if (moving) return;
        Quiet();
        var center = new Point(spots[i].X * scale + panX, spots[i].Y * scale + panY);
        moving = true;
        var found = await Read(conn => ArtistAlbums.ForArtist(conn, artists[i].Key));
        moving = false;
        if (found is null) return;
        data = found;
        album = song = 0;
        Layout();
        Transition(mapLayer, overview, center, zoomingIn: true, () => SetLevel(Level.Artist));
    }

    /// <summary>Out to the library, from the artist or the album: the map comes back with the artist's bubble where the
    /// view shrinks into.</summary>
    async Task ToLibrary()
    {
        if (moving || level == Level.Library) return;
        Quiet();
        moving = true;
        await LoadArtists();
        moving = false;
        var from = level == Level.Album ? albumLayer : (FrameworkElement)overview;
        var center = new Point(W / 2, H / 2);
        if (data is not null && artists.FindIndex(a => a.Key == data.Key) is var i and >= 0)
        {
            if (!mapPlaced) FitMap(null);
            scale = Math.Max(scale, Math.Min(1, MaxScale));
            panX = W / 2 - spots[i].X * scale;
            panY = H / 2 - spots[i].Y * scale;
            ApplyMap();
            center = new Point(spots[i].X * scale + panX, spots[i].Y * scale + panY);
        }
        else ApplyMap();
        Transition(mapLayer, from, center, zoomingIn: false, () => SetLevel(Level.Library));
    }

    void ZoomIn(int index2)
    {
        if (moving || data is null) return;
        Quiet();
        album = index2;
        song = 0;
        DrawAlbum();
        var (center, _) = albumSpots[index2];
        Transition(overview, albumLayer, center, zoomingIn: true, () => SetLevel(Level.Album));
    }

    void ZoomOut()
    {
        if (moving || level != Level.Album || data is null) return;
        Quiet();
        DrawOverview();
        var (center, _) = albumSpots[album];
        Transition(overview, albumLayer, center, zoomingIn: false, () => SetLevel(Level.Artist));
    }

    /// <summary>Into a level: the outer one swells from the point and fades while the inner one grows out of it; out, the
    /// reverse. Instant when animations are off.</summary>
    void Transition(FrameworkElement outer, FrameworkElement inner, Point center, bool zoomingIn, Action done)
    {
        foreach (var layer in new[] { outer, inner })
        {
            var transform = (CompositeTransform)layer.RenderTransform;
            transform.CenterX = center.X;
            transform.CenterY = center.Y;
        }
        outer.Visibility = inner.Visibility = Visibility.Visible;
        links.Visibility = Visibility.Collapsed;
        moving = true;
        var board = new Storyboard();
        Add(board, outer, zoomingIn ? 1 : 2.4, zoomingIn ? 2.4 : 1, zoomingIn ? 1 : 0, zoomingIn ? 0 : 1);
        Add(board, inner, zoomingIn ? 0.35 : 1, zoomingIn ? 1 : 0.35, zoomingIn ? 0 : 1, zoomingIn ? 1 : 0);
        Run(board, () =>
        {
            moving = false;
            done();
        });
    }

    static void Add(Storyboard board, UIElement target, double fromScale, double toScale, double fromOpacity, double toOpacity)
    {
        foreach (var axis in new[] { "ScaleX", "ScaleY" })
        {
            var animation = new DoubleAnimation
            {
                From = fromScale, To = toScale, Duration = Seconds(ZoomSeconds),
                EasingFunction = new CubicEase { EasingMode = EasingMode.EaseInOut },
            };
            Storyboard.SetTarget(animation, target);
            Storyboard.SetTargetProperty(animation, $"(UIElement.RenderTransform).(CompositeTransform.{axis})");
            board.Children.Add(animation);
        }
        var opacity = new DoubleAnimation { From = fromOpacity, To = toOpacity, Duration = Seconds(ZoomSeconds) };
        Storyboard.SetTarget(opacity, target);
        Storyboard.SetTargetProperty(opacity, "Opacity");
        board.Children.Add(opacity);
    }

    /// <summary>The previous or next album: the open one slides out on its side and the other slides in.</summary>
    void Cycle(int step)
    {
        if (moving || !ZoomedIn || data is null) return;
        var target = album + step;
        if (target < 0 || target >= data.Albums.Count) return;
        moving = true;
        Run(Slide(albumContent, 0, -step * 60, 1, 0), () =>
        {
            album = target;
            song = 0;
            DrawAlbum();
            _ = RefreshPanel();
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

    /// <summary>One level at rest: its layer alone, at its normal size, with its links and its info.</summary>
    void SetLevel(Level now)
    {
        level = now;
        foreach (var layer in new FrameworkElement[] { mapLayer, overview, albumLayer })
        {
            var transform = (CompositeTransform)layer.RenderTransform;
            transform.ScaleX = transform.ScaleY = 1;
            layer.Opacity = 1;
        }
        mapLayer.Visibility = now == Level.Library ? Visibility.Visible : Visibility.Collapsed;
        overview.Visibility = now == Level.Artist ? Visibility.Visible : Visibility.Collapsed;
        albumLayer.Visibility = now == Level.Album ? Visibility.Visible : Visibility.Collapsed;
        links.Visibility = Visibility.Visible;
        toLibrary.Visibility = now == Level.Library ? Visibility.Collapsed : Visibility.Visible;
        toArtist.Visibility = now == Level.Album && data is not null ? Visibility.Visible : Visibility.Collapsed;
        toArtist.Content = data is null ? "" : $"Back to {data.Artist}";
        ToolTipService.SetToolTip(toArtist, data is null ? null : $"Every album of {data.Artist} (Esc, or zoom out with the mouse wheel)");
        _ = RefreshPanel();
    }

    void OnKey(object sender, KeyRoutedEventArgs e)
    {
        switch (e.Key)
        {
            case VirtualKey.Escape when level == Level.Album:
                ZoomOut();
                break;
            case VirtualKey.Escape when level == Level.Artist:
                _ = ToLibrary();
                break;
            case VirtualKey.Escape:
                Closed?.Invoke();
                break;
            case VirtualKey.Left when ZoomedIn:
                Cycle(-1);
                break;
            case VirtualKey.Right when ZoomedIn:
                Cycle(1);
                break;
            case VirtualKey.Up when ZoomedIn && song > 0:
                Choose(song - 1);
                break;
            case VirtualKey.Down when ZoomedIn && data is not null && song < data.Albums[album].Songs.Count - 1:
                Choose(song + 1);
                break;
            default:
                return;
        }
        e.Handled = true;
    }

    // -- the info panel --

    void TogglePanel()
    {
        var closing = panel.Visibility == Visibility.Visible;
        panel.Visibility = closing ? Visibility.Collapsed : Visibility.Visible;
        settings.InfoPanelClosed = closing;
        settings.InfoPanelClosedIn = closing ? Version : null;
        _ = RefreshPanel();
    }

    /// <summary>What is open: the library's counts, the artist's albums and folder, or the album and its chosen song's
    /// tags and file information (SPEC §37). A later refresh replaces one still reading.</summary>
    async Task RefreshPanel()
    {
        if (panel.Visibility != Visibility.Visible) return;
        var version = ++panelVersion;
        var fields = new List<(string, string)>();
        string title;
        switch (level)
        {
            case Level.Library:
                title = "Library";
                fields.Add(("Artists", $"{artists.Count:N0}"));
                fields.Add(("Albums", $"{artists.Sum(a => a.Albums):N0}"));
                fields.Add(("Songs", $"{artists.Sum(a => a.Songs):N0}"));
                fields.Add(("", "Double-click an artist, or zoom in on it with the mouse wheel. Drag to move around."));
                break;
            case Level.Artist when data is not null:
                title = data.Artist;
                var years = data.Albums.Select(a => a.Year).Where(y => y is not null).ToList();
                fields.Add(("Albums", $"{data.Albums.Count}"));
                fields.Add(("Songs", $"{data.Albums.Sum(a => a.Songs.Count)}"));
                if (years.Count > 0) fields.Add(("Years", years.Min() == years.Max() ? years[0]! : $"{years.Min()}–{years.Max()}"));
                var ids = data.Albums.SelectMany(a => a.Songs).Select(s => s.Id).ToList();
                if (await Read(conn => ArtistAlbums.CommonFolder(conn, ids)) is { } folder) fields.Add(("Location", folder));
                break;
            case Level.Album when data is not null:
                var each = data.Albums[album];
                title = each.Name;
                fields.Add(("Artist", data.Artist));
                if (each.Year is not null) fields.Add(("Year", each.Year));
                fields.Add(("Songs", $"{each.Songs.Count}"));
                var chosen = each.Songs[song].Id;
                if (await Read(conn => SongInfo.Read(conn, chosen)) is { } info)
                {
                    fields.Add(("", ""));
                    fields.Add(("Song", ""));
                    fields.AddRange(info.Fields);
                }
                break;
            default:
                return;
        }
        if (version != panelVersion) return;
        panelBody.Children.Clear();
        panelBody.Children.Add(new TextBlock
        {
            Text = title, FontSize = 16, FontWeight = FontWeights.SemiBold, TextWrapping = TextWrapping.Wrap,
            Foreground = new SolidColorBrush(p.Text), Margin = new Thickness(0, 22, 0, 8), IsTextSelectionEnabled = true,
        });
        foreach (var (label, value) in fields)
        {
            if (label.Length > 0)
                panelBody.Children.Add(new TextBlock
                {
                    Text = label, FontSize = 11, Foreground = new SolidColorBrush(p.Secondary),
                    Margin = new Thickness(0, value.Length == 0 ? 10 : 6, 0, 0),
                    FontWeight = value.Length == 0 ? FontWeights.SemiBold : FontWeights.Normal,
                });
            if (value.Length > 0)
                panelBody.Children.Add(new TextBlock
                {
                    Text = value, FontSize = 13, TextWrapping = TextWrapping.Wrap, IsTextSelectionEnabled = true,
                    Foreground = new SolidColorBrush(label.Length == 0 ? p.Secondary : p.Text),
                });
        }
    }

    // -- an agent's check --

    /// <summary>An agent's off-screen check (SPEC §36 "7."): from the open album, out to the library and back in through
    /// the artist to its first album, as the wheel and Esc would go, logging what each level shows (an agent can't see it).</summary>
    public async Task CheckLevels()
    {
        async Task Settle()
        {
            for (var i = 0; i < 40 && moving; i++) await Task.Delay(100);
            await Task.Delay(600);
        }
        void Report(string step)
        {
            var panelText = string.Join(" | ", panelBody.Children.OfType<TextBlock>().Select(t => t.Text).Where(t => t.Length > 0));
            var links2 = string.Join(", ", links.Children.OfType<HyperlinkButton>().Where(b => b.Visibility == Visibility.Visible).Select(b => b.Content));
            Log.Info($"Check {step}: {level}; links {links2}; map {artists.Count} artists, {shown.Count} on screen, scale {scale:0.00}; " +
                     $"panel {(panel.Visibility == Visibility.Visible ? panelText : "closed")}");
        }
        await Settle();
        Report("open");
        await ToLibrary();
        await Settle();
        Report("library");
        if (data is not null && artists.FindIndex(a => a.Key == data.Key) is var i and >= 0)
        {
            await EnterArtist(i);
            await Settle();
            Report("artist");
            ZoomIn(0);
            await Settle();
            Report("album");
        }
    }

    /// <summary>An agent's off-screen check of the player (muted, SPEC §37): the open album's first song plays, then a
    /// task takes its folder; what the player does is logged.</summary>
    public async Task CheckPlayer(PathLocks locks)
    {
        if (data is null) return;
        Choose(0);
        await Task.Delay(2500);
        Log.Info($"Check player: {player.Current?.Title}, playing {player.Playing}, at {player.Position.TotalSeconds:0.0} s of {player.Duration.TotalSeconds:0.0} s, notice {player.Notice ?? "none"}");
        var folder = System.IO.Path.GetDirectoryName(player.Current?.Path ?? "") ?? "";
        var token = locks.Acquire([folder], "ReplayGain");
        await Task.Delay(500);
        Log.Info($"Check player, task on its folder: playing {player.Playing}, notice {player.Notice ?? "none"}");
        locks.Release(token);
    }

    // -- Open in File Explorer --

    /// <summary>A right-click anywhere offers Open in File Explorer on what is under it (SPEC §37): a song, its file
    /// selected; an album, its folder; an artist, the folder holding all its albums. A folder is the one its files share.</summary>
    void OnRightTapped(object sender, RightTappedRoutedEventArgs e)
    {
        object? target = null;
        for (var element = e.OriginalSource as DependencyObject; element is not null && element != view; element = VisualTreeHelper.GetParent(element))
            if (element is FrameworkElement { Tag: AlbumSong or ArtistAlbum or LibraryArtist } tagged)
            {
                target = tagged.Tag;
                break;
            }
        target ??= level switch
        {
            Level.Album when data is not null => data.Albums[album],
            Level.Artist => data,
            _ => null,
        };
        if (target is null) return;
        e.Handled = true;
        var item = new MenuFlyoutItem { Text = "Open in File Explorer", Icon = new FontIcon { Glyph = "" } };
        item.Click += (_, _) => _ = Reveal(target);
        var menu = new MenuFlyout();
        menu.Items.Add(item);
        menu.ShowAt(view, e.GetPosition(view));
    }

    async Task Reveal(object target)
    {
        var (path, select) = await Read<(string?, bool)>(conn => target switch
        {
            AlbumSong songTarget => (Core.Index.Queries.PathOf(conn, songTarget.Id), true),
            ArtistAlbum albumTarget => (ArtistAlbums.CommonFolder(conn, albumTarget.Songs.Select(s => s.Id)), false),
            ArtistAlbums artistTarget => (ArtistAlbums.CommonFolder(conn, artistTarget.Albums.SelectMany(a => a.Songs).Select(s => s.Id)), false),
            LibraryArtist artistTarget => (ArtistAlbums.ForArtist(conn, artistTarget.Key) is { } all
                ? ArtistAlbums.CommonFolder(conn, all.Albums.SelectMany(a => a.Songs).Select(s => s.Id)) : null, false),
            _ => (null, false),
        });
        if (path is not null) Shell.Reveal(path, select);
    }
}
