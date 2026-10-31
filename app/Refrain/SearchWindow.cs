using System.Runtime.InteropServices;
using Microsoft.UI;
using Microsoft.UI.Dispatching;
using Microsoft.UI.Input;
using Microsoft.UI.Windowing;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Controls.Primitives;
using Microsoft.UI.Xaml.Input;
using Microsoft.UI.Xaml.Media;
using Refrain.Core;
using Refrain.Core.Browser;
using Refrain.Core.Index;
using Refrain.Core.Playback;
using Refrain.Core.ReplayGain;
using Refrain.Core.Search;
using Refrain.Views;
using Windows.ApplicationModel.DataTransfer;
using Windows.Graphics;
using Windows.Storage;
using Windows.System;
using DispatcherQueue = Microsoft.UI.Dispatching.DispatcherQueue;
using DispatcherQueueTimer = Microsoft.UI.Dispatching.DispatcherQueueTimer;

namespace Refrain;

/// <summary>The Search window (SPEC §36 "11."): the search field with its Files | Lyrics toggle, the results, and in
/// lyrics mode the lyrics of the clicked result beside them. A result's only action is Open in File Explorer (a
/// double click in files mode, the right-click menu in both); none of them clears the search. The library grows from
/// Add a folder… (the … menu, a dropped folder, or Explorer's Open in Refrain); Options' Library page manages it.</summary>
public sealed partial class SearchWindow : Window
{
    readonly Library library;
    readonly Settings settings;
    readonly SearchService search;
    readonly DispatcherQueue ui;

    readonly TextBox searchBox = new()
    {
        FontSize = 19,  // 1.35 times the text (SPEC §6 "Direction task 5")
        Padding = new Thickness(40, 8, 12, 8),
        HorizontalAlignment = HorizontalAlignment.Stretch,
    };
    readonly TextBlock resultCount = new() { Margin = new Thickness(12, 8, 12, 4), VerticalAlignment = VerticalAlignment.Center };
    readonly TextBlock status = new() { Margin = new Thickness(16, 4, 16, 6), TextTrimming = TextTrimming.CharacterEllipsis };
    /// <summary>Turns beside the status line while the index is being built (Hugo, 2026-10-31).</summary>
    readonly ProgressRing indexing = new()
    {
        Width = 14, Height = 14, MinWidth = 14, MinHeight = 14, Margin = new Thickness(16, 4, 0, 6),
        IsActive = false, Visibility = Visibility.Collapsed, VerticalAlignment = VerticalAlignment.Center,
    };
    readonly TextBlock lyricsStatus = new() { Margin = new Thickness(16, 4, 8, 6), VerticalAlignment = VerticalAlignment.Center };
    readonly Button lyricsRetry = new() { Content = "Retry now", Visibility = Visibility.Collapsed, Margin = new Thickness(0, 2, 16, 4) };
    readonly ToggleButton filesMode = new() { Content = "Files", CornerRadius = new CornerRadius(4, 0, 0, 4) };
    readonly ToggleButton lyricsMode = new() { Content = "Lyrics", CornerRadius = new CornerRadius(0), Margin = new Thickness(-1, 0, 0, 0) };
    readonly FileTable results;
    readonly CardList cards = new();
    readonly LyricsPage lyrics;
    readonly DiscographyView discography;
    readonly Player player;
    readonly PlayerBar playerBar;
    readonly PreviewPanel preview;
    readonly Grid resultsPage = new();
    readonly Border resultsCard;
    readonly Border lyricsCard;
    readonly ColumnDefinition lyricsColumn = new();
    readonly ColumnDefinition lyricsGap = new();
    readonly FrameworkElement lyricsSplitter;
    readonly StackPanel welcome = new() { Spacing = 12, HorizontalAlignment = HorizontalAlignment.Center, VerticalAlignment = VerticalAlignment.Center, MaxWidth = 520 };
    readonly TextBlock welcomeText = new() { TextWrapping = TextWrapping.Wrap, TextAlignment = TextAlignment.Center };
    readonly Button welcomeAdd = Ui.Button("Add a folder…", "Add a music folder to the library; its files are indexed in the background");
    readonly DispatcherQueueTimer searchTimer;
    readonly DispatcherQueueTimer liveSearchTimer;
    readonly DispatcherQueueTimer statusTimer;

    SearchMode mode;
    string shownQuery = "";
    bool resultsShown;
    bool libraryEmpty;

    public SearchWindow(Library library, Settings settings)
    {
        this.library = library;
        this.settings = settings;
        ui = DispatcherQueue.GetForCurrentThread();
        search = new SearchService(library.Db);
        lyrics = new LyricsPage(library, library.Lyrics, settings);
        player = new Player(settings, App.Instance.Locks);
        playerBar = new PlayerBar(player);
        discography = new DiscographyView(library, settings, player);
        preview = new PreviewPanel(library, settings, player, lyrics, App.Instance.Ratings);
        // The last mode used is kept; an unknown value reads as Files (SPEC §6 "Direction task 5").
        mode = settings.SearchMode == "lyrics" ? SearchMode.Lyrics : SearchMode.Files;
        results = new FileTable(Columns.Search, settings.SearchHeader ?? new HeaderState { SortKey = "folder" }, searchResults: true);
        searchTimer = Timer(TimeSpan.FromMilliseconds(120), () => _ = RunSearch(inPlace: false));
        liveSearchTimer = Timer(TimeSpan.FromSeconds(1), () => _ = RunSearch(inPlace: true));
        statusTimer = Timer(TimeSpan.FromSeconds(6), () => status.Text = "");

        Title = "Refrain · Search";
        AppWindow.SetIcon(Path.Combine(AppContext.BaseDirectory, "Assets", "refrain.ico"));

        resultsPage.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });
        resultsPage.RowDefinitions.Add(new RowDefinition { Height = new GridLength(1, GridUnitType.Star) });
        resultsPage.Children.Add(resultCount);
        Grid.SetRow(results, 1);
        resultsPage.Children.Add(results);
        Grid.SetRow(cards, 1);
        resultsPage.Children.Add(cards);
        resultsCard = Card(resultsPage);
        lyricsCard = Card(preview);
        lyricsSplitter = Splitter(lyricsColumn, width => settings.PanelWidth = width);
        welcomeText.Style = Ui.Resource<Style>("BodyTextBlockStyle");
        welcome.Children.Add(welcomeText);
        welcomeAdd.HorizontalAlignment = HorizontalAlignment.Center;
        welcome.Children.Add(welcomeAdd);

        var root = new Grid { Padding = new Thickness(8, 0, 8, 0), AllowDrop = true };
        root.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });
        root.RowDefinitions.Add(new RowDefinition { Height = new GridLength(1, GridUnitType.Star) });
        root.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });
        root.Children.Add(SearchRow());
        var body = Body();
        Grid.SetRow(body, 1);
        root.Children.Add(body);
        var statusRow = new Grid();
        statusRow.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });
        statusRow.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
        statusRow.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });
        statusRow.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });
        statusRow.Children.Add(indexing);
        Grid.SetColumn(status, 1);
        statusRow.Children.Add(status);
        Grid.SetColumn(lyricsStatus, 2);
        statusRow.Children.Add(lyricsStatus);
        Grid.SetColumn(lyricsRetry, 3);
        statusRow.Children.Add(lyricsRetry);
        ToolTipService.SetToolTip(lyricsRetry, "Try LRCLIB again now");
        Grid.SetRow(statusRow, 2);
        root.Children.Add(statusRow);
        // The player's bar, below everything, always there (SPEC §37).
        root.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });
        Grid.SetRow(playerBar, 3);
        root.Children.Add(playerBar);
        Content = root;
        Ui.Background(root);

        Wire(root);
        ApplyMode();
        ShowPage();
        RestorePlacement();
        Closed += (_, _) => Shutdown();
        root.Loaded += async (_, _) =>
        {
            await LoadRoots();
            await Reopen();
            if (!Offscreen) searchBox.Focus(FocusState.Programmatic);  // the search has the focus at start (SPEC §33)
            // An agent's check opens what needs a double click by itself (SPEC §36 "7.": checks only read).
            if (Offscreen && Environment.GetEnvironmentVariable("REFRAIN_CHECK_DISCOGRAPHY") is { Length: > 0 } track) await CheckDiscography(track);
            if (Offscreen && Environment.GetEnvironmentVariable("REFRAIN_CHECK_THEME") is "light" or "dark") await CheckTheme(root);
            if (Offscreen && Environment.GetEnvironmentVariable("REFRAIN_CHECK_PREVIEW") is { Length: > 0 } previewed) await CheckPreview(previewed);
        };
    }

    DispatcherQueueTimer Timer(TimeSpan interval, Action tick)
    {
        var timer = DispatcherQueue.GetForCurrentThread().CreateTimer();
        timer.Interval = interval;
        timer.IsRepeating = false;
        timer.Tick += (_, _) => tick();
        return timer;
    }

    // -- layout --

    FrameworkElement SearchRow()
    {
        var row = new Grid { Margin = new Thickness(0, 12, 0, 8) };
        row.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
        row.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });
        // Files | Lyrics, one control joined to the field (SPEC §36 "1." task 3).
        var field = new Grid { HorizontalAlignment = HorizontalAlignment.Stretch };
        field.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });
        field.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });
        field.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
        foreach (var toggle in new[] { filesMode, lyricsMode })
        {
            toggle.VerticalAlignment = VerticalAlignment.Stretch;
            toggle.Padding = new Thickness(14, 0, 14, 0);
        }
        ToolTipService.SetToolTip(filesMode, "Search folder and file names and tags");
        ToolTipService.SetToolTip(lyricsMode, "Search the lyrics LRCLIB found");
        field.Children.Add(filesMode);
        Grid.SetColumn(lyricsMode, 1);
        field.Children.Add(lyricsMode);
        searchBox.CornerRadius = new CornerRadius(0, 4, 4, 0);
        searchBox.Margin = new Thickness(-1, 0, 0, 0);
        Grid.SetColumn(searchBox, 2);
        field.Children.Add(searchBox);
        var magnifier = new FontIcon
        {
            Glyph = "",
            FontSize = 16,
            Margin = new Thickness(14, 0, 0, 0),
            HorizontalAlignment = HorizontalAlignment.Left,
            VerticalAlignment = VerticalAlignment.Center,
            IsHitTestVisible = false,
        };
        Grid.SetColumn(magnifier, 2);
        field.Children.Add(magnifier);
        row.Children.Add(field);

        var more = new Button { Content = new FontIcon { Glyph = "", FontSize = 16 }, Padding = new Thickness(8), Margin = new Thickness(8, 0, 0, 0) };
        ToolTipService.SetToolTip(more, "More");
        var menu = new MenuFlyout { Placement = FlyoutPlacementMode.BottomEdgeAlignedRight };  // down from the button, never up (Hugo, 2026-10-29)
        void Add(string text, Action action)
        {
            var item = new MenuFlyoutItem { Text = text };
            item.Click += (_, _) => action();
            menu.Items.Add(item);
        }
        Add("Add a folder…", () => _ = PickFolder());
        Add("Options…", () => App.Instance.ShowOptions(null));
        Add("About Refrain", () => _ = About.Show(Content.XamlRoot));
        menu.Items.Add(new MenuFlyoutSeparator());
        Add("Exit", Close);
        more.Flyout = menu;
        Grid.SetColumn(more, 1);
        row.Children.Add(more);
        return row;
    }

    FrameworkElement Body()
    {
        var body = new Grid();
        body.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
        body.ColumnDefinitions.Add(lyricsGap);
        body.ColumnDefinitions.Add(lyricsColumn);
        body.Children.Add(welcome);
        body.Children.Add(resultsCard);
        Grid.SetColumn(lyricsSplitter, 1);
        body.Children.Add(lyricsSplitter);
        Grid.SetColumn(lyricsCard, 2);
        body.Children.Add(lyricsCard);
        // The discography view covers the results and the lyrics while it is open (SPEC §36 "11.").
        Grid.SetColumnSpan(discography, 3);
        discography.Visibility = Visibility.Collapsed;
        body.Children.Add(discography);
        return body;
    }

    /// <summary>A panel as a rounded card on the window's layer (SPEC §36 "1." task 3).</summary>
    static Border Card(UIElement content) => new()
    {
        Child = content,
        CornerRadius = new CornerRadius(8),
        BorderThickness = new Thickness(1),
        Padding = new Thickness(3),
        Background = Ui.Resource<Brush>("LayerFillColorDefaultBrush"),
        BorderBrush = Ui.Resource<Brush>("CardStrokeColorDefaultBrush"),
    };

    /// <summary>The lyrics panel's width, dragged to the left; kept between sessions.</summary>
    static FrameworkElement Splitter(ColumnDefinition column, Action<double> keep)
    {
        var grip = new SplitterGrip();
        double startX = 0, startWidth = 0;
        var dragging = false;
        grip.PointerPressed += (_, e) =>
        {
            dragging = true;
            startX = e.GetCurrentPoint(null).Position.X;
            startWidth = column.ActualWidth;
            grip.CapturePointer(e.Pointer);
        };
        grip.PointerMoved += (_, e) =>
        {
            if (!dragging) return;
            var width = Math.Clamp(startWidth - (e.GetCurrentPoint(null).Position.X - startX), 240, 900);
            column.Width = new GridLength(width);
            keep(width);
        };
        grip.PointerReleased += (_, e) =>
        {
            dragging = false;
            grip.ReleasePointerCapture(e.Pointer);
        };
        return grip;
    }

    sealed partial class SplitterGrip : Grid
    {
        public SplitterGrip()
        {
            Background = new SolidColorBrush(Colors.Transparent);
            ProtectedCursor = InputSystemCursor.Create(InputSystemCursorShape.SizeWestEast);
        }
    }

    // -- wiring --

    void Wire(Grid root)
    {
        library.Indexer.FolderChanged += _ => ui.TryEnqueue(Changed);
        library.Indexer.ScanFinished += () => ui.TryEnqueue(Changed);
        library.Indexer.ScanFinished += () => ui.TryEnqueue(() => _ = AskReplayGain());
        library.Indexer.Progress += (folders, read, busy) => ui.TryEnqueue(() =>
        {
            indexing.IsActive = busy;
            indexing.Visibility = busy ? Visibility.Visible : Visibility.Collapsed;
            status.Margin = new Thickness(busy ? 8 : 16, 4, 16, 6);  // the text beside the ring, or where it was
            if (busy) Status($"Indexing: {folders:N0} folders listed, {read:N0} files read", sticky: true);
            else if (status.Text.StartsWith("Indexing", StringComparison.Ordinal)) status.Text = "";
        });
        library.RootsChanged += () => ui.TryEnqueue(() => _ = LoadRoots());
        // Indexing waits while ReplayGain or Tidy reads albums from the drive (SPEC §35 task 1), from any window.
        App.Instance.Locks.Changed += () => ui.TryEnqueue(PauseForTasks);
        var service = library.Lyrics;
        service.FoldersChanged += _ => ui.TryEnqueue(Changed);
        service.EntryReady += key => ui.TryEnqueue(() =>
        {
            if (lyrics.ShownKey == key) _ = lyrics.Reload();
        });
        service.ProgressChanged += progress => ui.TryEnqueue(() => ShowLyricsProgress(progress));
        service.Unreachable += until => ui.TryEnqueue(() =>
        {
            Status($"LRCLIB unreachable, retrying at {until:HH:mm}.");
            _ = lyrics.Reload();
        });
        lyricsRetry.Click += (_, _) =>
        {
            service.RetryNow();
            _ = lyrics.Reload();
        };
        welcomeAdd.Click += (_, _) => _ = PickFolder();
        filesMode.Click += (_, _) => SetMode(SearchMode.Files);
        lyricsMode.Click += (_, _) => SetMode(SearchMode.Lyrics);

        // Both modes (SPEC §37): a click previews the result, a double click opens it in the discography view, the
        // menu opens File Explorer on it.
        results.Activated += row => _ = OpenInView(row);
        results.SelectionChanged += () => _ = PreviewOf(results.SelectedRows);
        results.MenuRequested += (rows, anchor, point) => ShowMenu(rows, anchor, point);
        results.LayoutChanged += () =>
        {
            settings.SearchHeader = results.State;
            if (resultsShown) _ = RunSearch(inPlace: true);
        };
        cards.MenuRequested += (rows, anchor, point) => ShowMenu(rows, anchor, point);
        cards.SelectionChanged += () => _ = PreviewOf(cards.SelectedRows);
        cards.Clicked += () => _ = PreviewOf(cards.SelectedRows);
        cards.Activated += row => _ = OpenInView(row);
        // The discography view covers the preview: its excerpt stops.
        discography.RegisterPropertyChangedCallback(UIElement.VisibilityProperty, (_, _) =>
        {
            if (discography.Visibility == Visibility.Visible) preview.Stop();
        });
        discography.Closed += CloseDiscography;
        playerBar.SongClicked += current =>
        {
            discography.Visibility = Visibility.Visible;
            _ = discography.ShowSong(current);
        };

        searchBox.TextChanged += (_, _) =>
        {
            if (discography.Visibility == Visibility.Visible) discography.Visibility = Visibility.Collapsed;  // a new search
            if (searchBox.Text.Trim().Length == 0)
            {
                searchTimer.Stop();
                ClearResults();
                return;
            }
            searchTimer.Start();
        };
        searchBox.KeyDown += async (_, e) =>
        {
            if (e.Key == VirtualKey.Enter)
            {
                e.Handled = true;
                searchTimer.Stop();
                await RunSearch(inPlace: false);  // Enter runs the search again (SPEC §6)
                ShownResults.FocusFirst();
            }
            else if (e.Key == VirtualKey.Escape)
            {
                e.Handled = true;
                searchBox.Text = "";
            }
            else if (e.Key == VirtualKey.Down && resultsShown)
            {
                e.Handled = true;
                ShownResults.FocusFirst();
            }
        };
        foreach (var list in new[] { results.List, cards.List })
            list.KeyDown += (_, e) =>
            {
                if (e.Key != VirtualKey.Escape) return;
                e.Handled = true;
                searchBox.Focus(FocusState.Keyboard);  // back to the field; the results stay
            };

        var focus = new KeyboardAccelerator { Key = VirtualKey.K, Modifiers = VirtualKeyModifiers.Control };
        focus.Invoked += (_, e) =>
        {
            e.Handled = true;
            searchBox.Focus(FocusState.Keyboard);
            searchBox.SelectAll();
        };
        root.KeyboardAccelerators.Add(focus);
        root.KeyboardAcceleratorPlacementMode = KeyboardAcceleratorPlacementMode.Hidden;  // no floating shortcut tips (Hugo, 2026-10-29)

        // Folders dropped on the window are added (SPEC §3).
        root.DragOver += (_, e) =>
        {
            if (!e.DataView.Contains(StandardDataFormats.StorageItems)) return;
            e.AcceptedOperation = DataPackageOperation.Link;
            e.DragUIOverride.Caption = "Add to library";
        };
        root.Drop += async (_, e) =>
        {
            if (!e.DataView.Contains(StandardDataFormats.StorageItems)) return;
            var items = await e.DataView.GetStorageItemsAsync();
            var folders = items.OfType<StorageFolder>().Select(f => f.Path).ToList();
            if (folders.Count == 0)
            {
                Status("Only folders can be added to the library; drop the folder that holds these files.");
                return;
            }
            foreach (var folder in folders) await AddFolder(folder);
        };
    }

    /// <summary>The index changed: shown results refresh in place after a second.</summary>
    void Changed()
    {
        if (resultsShown) liveSearchTimer.Start();
    }

    void ShowMenu(IReadOnlyList<FileRow> rows, UIElement anchor, Windows.Foundation.Point point)
    {
        if (rows.Count == 0) return;
        var menu = new MenuFlyout();
        var reveal = new MenuFlyoutItem { Text = "Open in File Explorer", IsEnabled = rows.Count == 1 };
        reveal.Click += (_, _) => _ = Reveal(rows[0]);
        menu.Items.Add(reveal);
        menu.ShowAt(anchor, point);
    }

    async Task Reveal(FileRow row)
    {
        if (await Read(conn => Queries.PathOf(conn, row.Id)) is { } path) Shell.Reveal(path, select: !row.IsDir);
    }

    /// <summary>A result's double click (SPEC §37): the discography view at its level, a song in its album, an album, an
    /// artist, or the library with a folder's artists lit; the results stay as they are underneath.</summary>
    async Task OpenInView(FileRow row)
    {
        var found = await Read(conn => row.IsDir ? ArtistAlbums.ForFolder(conn, row.Id)
            : (ArtistAlbums.For(conn, row.Id), false, (IReadOnlyList<string>)[]));
        if (!await ShowInView(found))
            Status(row.IsDir ? $"{row.Name} has no songs with an artist in their tags." : $"{row.Name} has no artist in its tags, so it has no discography to show.");
    }

    /// <summary>Opens the view on what was found; false when there was nothing to open.</summary>
    async Task<bool> ShowInView((ArtistAlbums? View, bool AllAlbums, IReadOnlyList<string> Artists) found)
    {
        if (found.View is { } view)
        {
            discography.Visibility = Visibility.Visible;
            discography.Show(view, allAlbums: found.AllAlbums);
            return true;
        }
        if (found.Artists.Count > 1)
        {
            // Several artists: the library level, theirs lit (SPEC §37).
            discography.Visibility = Visibility.Visible;
            await discography.ShowLibrary(found.Artists);
            return true;
        }
        return false;
    }

    /// <summary>An agent's off-screen check (SPEC §36 "7."): logs the colours the theme gave this window's controls, as
    /// they resolved (<see cref="Theme"/>; an agent can't see them). The app starts in that theme (<see cref="App"/>).</summary>
    async Task CheckTheme(Grid root)
    {
        await Task.Delay(300);
        static string Of(Brush? brush) => brush switch
        {
            SolidColorBrush solid => $"#{solid.Color.A:X2}{solid.Color.R:X2}{solid.Color.G:X2}{solid.Color.B:X2}",
            LinearGradientBrush gradient => "gradient " + string.Join(" ", gradient.GradientStops.Select(s => Of(new SolidColorBrush(s.Color)))),
            null => "none",
            _ => brush.GetType().Name,
        };
        var accent = Ui.Primary("Check");
        accent.Visibility = Visibility.Collapsed;
        root.Children.Add(accent);
        accent.ApplyTemplate();
        Log.Info($"Theme check ({root.ActualTheme}): window {Of(root.Background)}; search field {Of(searchBox.Background)} " +
                 $"edge {Of(searchBox.BorderBrush)} text {Of(searchBox.Foreground)}; Files {Of(filesMode.Background)}; " +
                 $"Add a folder… {Of(welcomeAdd.Background)} edge {Of(welcomeAdd.BorderBrush)}; filled {Of(accent.Background)} " +
                 $"text {Of(accent.Foreground)}; status {Of(status.Foreground)}; results card {Of(resultsCard.Background)} " +
                 $"edge {Of(resultsCard.BorderBrush)}; matches {Of(results.HighlightBrush)}; WinUI brushes recoloured {Theme.Recoloured}");
        root.Children.Remove(accent);
    }

    /// <summary>An agent's off-screen check (SPEC §36 "7."): the discography of <paramref name="track"/>, once the index
    /// has it (a minute at most), as a double click on its lyrics result would open it.</summary>
    async Task CheckDiscography(string track)
    {
        for (var tries = 0; tries < 60; tries++)
        {
            if (await Read(conn => Queries.Find(conn, track)) is { Exact: true } hit &&
                await Read(conn => ArtistAlbums.For(conn, hit.Id)) is { } albums)
            {
                discography.Visibility = Visibility.Visible;
                discography.Show(albums, allAlbums: Environment.GetEnvironmentVariable("REFRAIN_CHECK_ALBUMS") == "1");
                if (Environment.GetEnvironmentVariable("REFRAIN_CHECK_LEVELS") == "1") await discography.CheckLevels();
                if (Environment.GetEnvironmentVariable("REFRAIN_CHECK_PLAYER") == "1") await discography.CheckPlayer(App.Instance.Locks);
                return;
            }
            await Task.Delay(1000);
        }
        Log.Error($"Check: no discography for {track}");
    }

    /// <summary>An agent's off-screen check (SPEC §36 "7."), muted: the preview of each path (separated by |), as a
    /// click on its result would show it, logged once the index has it.</summary>
    async Task CheckPreview(string paths)
    {
        preview.Muted = true;
        foreach (var path in paths.Split('|'))
        {
            for (var tries = 0; tries < 60; tries++)
            {
                if (await Read(conn => Queries.Find(conn, path)) is { Exact: true } hit && !library.Indexer.Busy)
                {
                    var isDir = await Read(conn => Queries.IsDir(conn, hit.Id));
                    await preview.ShowRow(new FileRow { Id = hit.Id, Name = Path.GetFileName(path), IsDir = isDir });
                    await Task.Delay(4000);
                    Log.Info($"Check preview of {Path.GetFileName(path)}: {preview.Describe()}");
                    break;
                }
                await Task.Delay(1000);
            }
        }
    }

    void CloseDiscography()
    {
        discography.Visibility = Visibility.Collapsed;
        if (!Offscreen) cards.List.Focus(FocusState.Keyboard);
    }

    /// <summary>The preview follows the selection (SPEC §37): one result previewed, else nothing.</summary>
    async Task PreviewOf(IReadOnlyList<FileRow> rows)
    {
        if (rows.Count == 1) await preview.ShowRow(rows[0]);
        else preview.ShowNone(rows.Count == 0 ? "Click a result to preview it." : "Select one result to preview it.");
    }

    // -- reading the index off the UI thread --

    Task<T> Read<T>(Func<Microsoft.Data.Sqlite.SqliteConnection, T> query) => Task.Run(() =>
    {
        using var conn = library.Db.Read();
        return query(conn);
    });

    // -- the library --

    async Task LoadRoots()
    {
        libraryEmpty = await Read(conn => Roots.All(conn).Count == 0);
        ShowPage();
    }

    async Task PickFolder()
    {
        var picker = new Windows.Storage.Pickers.FolderPicker();
        picker.FileTypeFilter.Add("*");
        WinRT.Interop.InitializeWithWindow.Initialize(picker, WinRT.Interop.WindowNative.GetWindowHandle(this));
        var folder = await picker.PickSingleFolderAsync();
        if (folder is not null) await AddFolder(folder.Path);
    }

    /// <summary>The no-duplicates rule (SPEC §3): a folder already in the library, or inside one, is never added again.</summary>
    public async Task AddFolder(string path)
    {
        var result = await library.Add(path);
        Status(result.Outcome switch
        {
            AddOutcome.Merged => $"Added {result.Path}; the library folders inside it are now part of it.",
            AddOutcome.Added => $"Added {result.Path}. Its folders are being indexed.",
            AddOutcome.AlreadyRoot or AddOutcome.InsideRoot => $"{result.Path} is already in the library.",
            AddOutcome.IsAFile => $"{result.Path} is a file; add the folder that holds it.",
            _ => $"{result.Path} can't be opened as a folder.",
        });
    }

    /// <summary>Explorer's Open in Refrain (SPEC §37): the window comes forward, the folder (a file's folder) joins the
    /// library when it isn't in it yet, and the discography view opens on the path once its tracks are indexed. A start
    /// on its own asks for nothing more.</summary>
    public async Task OpenFromOutside(StartRequest request)
    {
        BringToFront();
        if (request.Paths.FirstOrDefault() is not { } first) return;
        var path = RealPath.Normalize(first);
        var (isFile, isFolder) = await Task.Run(() => (File.Exists(path), Directory.Exists(path)));
        if (!isFile && !isFolder)
        {
            Status($"{path} is gone, so there is nothing to open.");
            return;
        }
        var folder = isFile ? Path.GetDirectoryName(path)! : path;
        var added = await Read(conn => Queries.Find(conn, folder)) is null;
        if (added) await AddFolder(folder);
        var opening = ++openings;
        // A folder just added is still being read: wait for its tracks (the indexer goes idle), two minutes at most.
        for (var tries = 0; tries < 240 && opening == openings; tries++)
        {
            var found = await Read(conn => Queries.Find(conn, path) is { Exact: true } hit
                ? (isFile ? (ArtistAlbums.For(conn, hit.Id), false, (IReadOnlyList<string>)[]) : ArtistAlbums.ForFolder(conn, hit.Id))
                : ((ArtistAlbums?)null, false, []));
            var ready = !added || !library.Indexer.Busy;
            if (ready && await ShowInView(found)) return;
            if (ready && tries > 0)
            {
                Status(isFile ? $"{Path.GetFileName(path)} has no artist in its tags, so it has no discography to show."
                              : $"{path} has no songs with an artist in their tags.");
                return;
            }
            await Task.Delay(500);
        }
    }

    int openings;  // a later Open in Refrain replaces one still waiting for its tracks

    // ReplayGain at a scan's end (SPEC §37): the albums already asked about this session, and "Don't remind me".
    readonly HashSet<string> askedGain = new(StringComparer.OrdinalIgnoreCase);
    bool gainQuiet, askingGain;

    /// <summary>One popup when a scan ends (the last of a burst, once the indexer is idle) and the library has albums
    /// without ReplayGain tags that weren't asked about yet this session: run ReplayGain on all of them, one window
    /// measuring them one after another, each folder as an album (the right-click entry for several folders). "Don't
    /// remind me" lasts until Refrain closes. Albums a task holds are left out.</summary>
    async Task AskReplayGain()
    {
        if (gainQuiet || askingGain) return;
        await Task.Delay(1500);
        if (library.Indexer.Busy || gainQuiet || askingGain) return;
        var options = settings.GetReplayGain();
        var albums = (await Read(conn => Core.ReplayGain.Missing.AlbumsWithoutTags(conn, options)))
            .Where(a => App.Instance.Locks.Conflict(a) is null).ToList();
        if (albums.All(askedGain.Contains)) return;
        askedGain.UnionWith(albums);
        var text = albums.Count == 1 ? "1 album has no ReplayGain tags. Run ReplayGain on it?"
                                     : $"{albums.Count} albums have no ReplayGain tags. Run ReplayGain on them?";
        if (Offscreen)
        {
            Log.Info($"Check ReplayGain popup: {text} ({string.Join("; ", albums)})");  // an agent's check reads, never clicks
            return;
        }
        var quiet = new CheckBox { Content = "Don't remind me until Refrain closes" };
        var body = new StackPanel { Spacing = 10 };
        body.Children.Add(new TextBlock { Text = text, TextWrapping = TextWrapping.Wrap });
        body.Children.Add(Ui.Secondary($"One ReplayGain window measures them one after another, each folder as an album, at " +
                                       $"{RefrainMenu.MenuLogic.TargetText(options.Target)}, and writes their tags. Nothing changes until you run it."));
        body.Children.Add(quiet);
        var dialog = new ContentDialog
        {
            Title = "ReplayGain", Content = body, PrimaryButtonText = "Run ReplayGain", CloseButtonText = "Not now",
            DefaultButton = ContentDialogButton.Close, XamlRoot = Content.XamlRoot,
        };
        askingGain = true;
        try
        {
            var answer = await dialog.ShowAsync();
            gainQuiet = quiet.IsChecked == true;
            if (answer == ContentDialogResult.Primary) App.Instance.RunEntry(RefrainMenu.MenuLogic.ReplayGainAlbums, albums);
        }
        catch (Exception e) when (e is InvalidOperationException or System.Runtime.InteropServices.COMException)
        {
            Log.Info($"The ReplayGain question couldn't show (another dialog is open): {e.Message}");
            askedGain.ExceptWith(albums);
        }
        finally
        {
            askingGain = false;
        }
    }

    /// <summary>A task changed files in these folders: the index re-reads them, with their subfolders.</summary>
    public void TaskChanged(IReadOnlyList<string> folders)
    {
        foreach (var folder in folders) library.Indexer.ScanPath(folder, recursive: true);
        preview.TaskEnded(folders);
    }

    void PauseForTasks()
    {
        var owners = App.Instance.Locks.Owners();
        var paused = owners.Contains(ReplayGainTask.Owner) || owners.Contains(Core.Tidy.TidyTask.Owner);
        if (paused == library.Indexer.Paused) return;
        library.Indexer.Paused = paused;
        if (paused) Status("Indexing is paused while a task reads albums.", sticky: true);
        else if (status.Text.StartsWith("Indexing is paused", StringComparison.Ordinal)) status.Text = "";
    }

    [LibraryImport("user32.dll")]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static partial bool SetForegroundWindow(nint window);

    /// <summary>The window comes to the front, restored if minimized.</summary>
    void BringToFront()
    {
        if (Offscreen) return;
        if (AppWindow.Presenter is OverlappedPresenter { State: OverlappedPresenterState.Minimized } presenter) presenter.Restore();
        AppWindow.Show();
        SetForegroundWindow(WinRT.Interop.WindowNative.GetWindowHandle(this));
    }

    /// <summary>A pending row whose key's last lookup failed this session shows "error" (SPEC §5).</summary>
    List<FileRow> Decorate(List<FileRow> rows)
    {
        var failed = library.Lyrics.FailedKeys();
        if (failed.Count == 0) return rows;
        return rows.Select(r => r.Lrclib == "pending" && r.LyricsKey is { } key && failed.Contains(key) ? r with { Lrclib = "error" } : r)
            .ToList();
    }

    // -- search --

    async Task RunSearch(bool inPlace)
    {
        var text = searchBox.Text;
        if (text.Trim().Length == 0)
        {
            ClearResults();
            return;
        }
        var asked = mode;
        var outcome = await search.Run(text, asked == SearchMode.Files ? results.WorkerOrder : null, asked,
            library.Lyrics.FailedKeys());
        if (outcome is null || outcome.Text != searchBox.Text || asked != mode) return;  // a newer query took over
        var same = inPlace && resultsShown && shownQuery == outcome.Text + "\u0000" + asked;
        if (asked == SearchMode.Lyrics) cards.SetRows(Decorate(outcome.Rows), same);
        else results.ShowResults(Decorate(outcome.Rows), Decorate(outcome.Sorted), same);
        shownQuery = outcome.Text + "\u0000" + asked;
        var count = outcome.Total == outcome.Rows.Count
            ? $"{outcome.Total:N0} results"
            : $"{outcome.Rows.Count:N0} of {outcome.Total:N0} results";
        resultCount.Text = outcome.Errors.Count > 0 ? $"{count} · {string.Join(" ", outcome.Errors)}" : count;
        resultsShown = true;
        ShowPage();
    }

    IRowList ShownResults => mode == SearchMode.Lyrics ? cards : results;

    void ClearResults()
    {
        if (!resultsShown) return;
        resultsShown = false;
        liveSearchTimer.Stop();
        preview.ShowNone("Click a result to preview it.");
        ShowPage();
    }

    /// <summary>What the body shows: the results (with the lyrics panel in lyrics mode), else a welcome line, which
    /// offers Add a folder… while the library is empty.</summary>
    void ShowPage()
    {
        results.Visibility = mode == SearchMode.Files ? Visibility.Visible : Visibility.Collapsed;
        cards.Visibility = mode == SearchMode.Lyrics ? Visibility.Visible : Visibility.Collapsed;
        resultsCard.Visibility = resultsShown ? Visibility.Visible : Visibility.Collapsed;
        welcome.Visibility = resultsShown ? Visibility.Collapsed : Visibility.Visible;
        welcomeText.Text = libraryEmpty
            ? "Your library is empty. Add a music folder, and Refrain indexes its files, their tags and their lyrics."
            : mode == SearchMode.Files
                ? "Type a name, a title, an artist, an album or a year."
                : "Type a few words from a song's lyrics.";
        welcomeAdd.Visibility = libraryEmpty ? Visibility.Visible : Visibility.Collapsed;
        var showLyrics = resultsShown;  // the preview, in both modes (SPEC §37)
        lyricsCard.Visibility = lyricsSplitter.Visibility = showLyrics ? Visibility.Visible : Visibility.Collapsed;
        lyricsColumn.Width = showLyrics ? new GridLength(Math.Clamp(settings.PanelWidth, 240, 900)) : new GridLength(0);
        lyricsGap.Width = new GridLength(showLyrics ? 8 : 0);
    }

    /// <summary>The Files | Lyrics toggle (SPEC §6 "Direction task 5"): the query on screen runs again in the new mode,
    /// and the focus goes back to the field.</summary>
    void SetMode(SearchMode next)
    {
        mode = next;
        discography.Visibility = Visibility.Collapsed;
        settings.SearchMode = next == SearchMode.Lyrics ? "lyrics" : "files";
        ApplyMode();
        ShowPage();
        if (searchBox.Text.Trim().Length > 0) _ = RunSearch(inPlace: false);
        _ = PreviewOf(ShownResults.SelectedRows);
        searchBox.Focus(FocusState.Programmatic);
    }

    void ApplyMode()
    {
        filesMode.IsChecked = mode == SearchMode.Files;
        lyricsMode.IsChecked = mode == SearchMode.Lyrics;
        searchBox.PlaceholderText = mode == SearchMode.Files
            ? "Search folders, files and tags (Ctrl+K)"
            : "Search the lyrics (Ctrl+K)";
        ToolTipService.SetToolTip(searchBox, mode == SearchMode.Files
            ? "Any part of a folder or file name, title, artist, album artist, album or date. Filters: artist:, " +
              "album:, genre:, year:, ext:, lrclib:, rg:none|partial|full"
            : "Any part of the lyrics LRCLIB found. Filters still narrow it: artist:, album:, year:…");
    }

    /// <summary>The lyrics side of the status line (SPEC §9 "Controls"), with Retry now while lookups wait.</summary>
    void ShowLyricsProgress(Core.Lyrics.LyricsProgress progress)
    {
        var (text, retry) = progress switch
        {
            { PausedUntil: { } until } => ($"LRCLIB unreachable, retrying at {until:HH:mm}", true),
            { Offline: true } => ("", false),
            { Pending: > 0, Busy: true } => ($"Looking up lyrics: {progress.Pending:N0} to look up", false),
            { Failed: > 0 } => ($"Lyrics: {progress.Failed:N0} to retry later", true),
            _ => ("", false),
        };
        lyricsStatus.Text = text;
        lyricsRetry.Visibility = retry ? Visibility.Visible : Visibility.Collapsed;
    }

    // -- status, placement --

    void Status(string text, bool sticky = false)
    {
        status.Text = text;
        statusTimer.Stop();
        if (!sticky) statusTimer.Start();
    }

    /// <summary>An agent's window check (SPEC §36 "7."): the window stays beyond every monitor, out of the taskbar and
    /// Alt+Tab, and never takes the focus.</summary>
    public static bool Offscreen => Environment.GetEnvironmentVariable("REFRAIN_OFFSCREEN") == "1";

    void RestorePlacement()
    {
        if (Offscreen)
        {
            // The monitor list can only be indexed: enumerating it throws (a WinRT projection limit).
            var areas = DisplayArea.FindAll();
            var right = 0;
            for (var i = 0; i < areas.Count; i++) right = Math.Max(right, areas[i].OuterBounds.X + areas[i].OuterBounds.Width);
            AppWindow.MoveAndResize(new RectInt32(right + 200, 0, 1100, 760));
            AppWindow.IsShownInSwitchers = false;
            return;
        }
        if (settings is { WindowX: { } x, WindowY: { } y, WindowWidth: { } w, WindowHeight: { } h } && w > 200 && h > 200)
        {
            var area = DisplayArea.GetFromRect(new RectInt32(x, y, w, h), DisplayAreaFallback.None);
            if (area is not null) AppWindow.MoveAndResize(new RectInt32(x, y, w, h));
        }
        else
        {
            AppWindow.Resize(new SizeInt32(1100, 760));
        }
        if (settings.Maximized && AppWindow.Presenter is OverlappedPresenter presenter) presenter.Maximize();
    }

    void Shutdown()
    {
        if (AppWindow.Presenter is OverlappedPresenter presenter && !Offscreen)
        {
            settings.Maximized = presenter.State == OverlappedPresenterState.Maximized;
            if (presenter.State == OverlappedPresenterState.Restored)
            {
                settings.WindowX = AppWindow.Position.X;
                settings.WindowY = AppWindow.Position.Y;
                settings.WindowWidth = AppWindow.Size.Width;
                settings.WindowHeight = AppWindow.Size.Height;
            }
        }
        settings.SearchHeader = results.State;
        // Refrain reopens on the last song, paused where it was (SPEC §37); closing it stops the music.
        settings.PlayerPath = settings.PlayerKeep ? player.Current?.Path : null;
        settings.PlayerSeconds = settings.PlayerKeep && player.Current is not null ? player.Position.TotalSeconds : 0;
        player.Dispose();
        preview.Dispose();
        settings.Save(AppPaths.Settings);
    }

    /// <summary>The bar on the last song, paused where it was, unless the Player page turned that off (SPEC §37).</summary>
    async Task Reopen()
    {
        if (Offscreen && Environment.GetEnvironmentVariable("REFRAIN_CHECK_PLAYER") == "1") player.Muted = true;
        if (!settings.PlayerKeep || settings.PlayerPath is not { } path) return;
        if (await Read(conn => PlayerSong.Reopen(conn, path)) is { } found)
            player.Play(found.Queue, found.Start, TimeSpan.FromSeconds(settings.PlayerSeconds), paused: true);
    }
}
