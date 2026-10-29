using System.Runtime.InteropServices;
using Microsoft.Data.Sqlite;
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
using Refrain.Core.Lyrics;
using Refrain.Core.Search;
using Refrain.Views;
using Windows.ApplicationModel.DataTransfer;
using Windows.Graphics;
using Windows.Storage;
using Windows.System;
using DispatcherQueue = Microsoft.UI.Dispatching.DispatcherQueue;
using DispatcherQueueTimer = Microsoft.UI.Dispatching.DispatcherQueueTimer;

namespace Refrain;

/// <summary>The window (SPEC §33 "The app's explorer"): the search on top, the folder row with the address bar, the
/// file list (or the search results in its place) and the folder tree, hidden by default.</summary>
public sealed partial class MainWindow : Window
{
    readonly Library library;
    readonly Settings settings;
    readonly SearchService search;
    readonly History history = new();
    readonly DispatcherQueue ui;

    readonly TextBox searchBox = new()
    {
        PlaceholderText = "Search folders, files and tags (Ctrl+K)",
        FontSize = 19,  // 1.35 times the text (SPEC §6 "Direction task 5")
        Padding = new Thickness(40, 8, 12, 8),
        MaxWidth = 900,
        HorizontalAlignment = HorizontalAlignment.Stretch,
    };
    readonly TextBlock resultCount = new() { Margin = new Thickness(12, 8, 12, 4), VerticalAlignment = VerticalAlignment.Center };
    readonly StartRequest startRequest;
    (string Path, DateTime Until)? waitingFolder;
    readonly TextBlock status = new() { Margin = new Thickness(16, 4, 16, 6), TextTrimming = TextTrimming.CharacterEllipsis };
    readonly TextBlock lyricsStatus = new() { Margin = new Thickness(16, 4, 8, 6), VerticalAlignment = VerticalAlignment.Center };
    readonly Button lyricsRetry = new() { Content = "Retry now", Visibility = Visibility.Collapsed, Margin = new Thickness(0, 2, 16, 4) };
    readonly ToggleButton filesMode = new() { Content = "Files", CornerRadius = new CornerRadius(4, 0, 0, 4) };
    readonly ToggleButton lyricsMode = new() { Content = "Lyrics", CornerRadius = new CornerRadius(0), Margin = new Thickness(-1, 0, 0, 0) };
    readonly CardList cards = new();
    SearchMode mode;
    readonly AddressBar address = new();
    readonly FolderTree tree = new();
    readonly SidePanel panel;
    readonly ColumnDefinition panelColumn = new();
    readonly FileTable files;
    readonly FileTable results;
    readonly Border filesPage;
    readonly Grid resultsPage = new();
    readonly ColumnDefinition treeColumn = new();
    readonly ColumnDefinition splitterColumn = new();
    readonly FrameworkElement treeCard;
    readonly FrameworkElement splitter;
    readonly ToggleButton foldersButton = new();
    readonly Button backButton = Icon("", "Back (Alt+Left)");
    readonly Button forwardButton = Icon("", "Forward (Alt+Right)");
    readonly Button upButton = Icon("", "Up (Alt+Up, Backspace)");
    readonly Button rescanButton = Icon("", "Rescan this folder (F5)");
    readonly Button explorerButton = new();
    readonly DispatcherQueueTimer searchTimer;
    readonly DispatcherQueueTimer liveSearchTimer;
    readonly DispatcherQueueTimer refreshTimer;
    readonly DispatcherQueueTimer statusTimer;
    readonly HashSet<long> changedFolders = [];

    long? current;
    List<(long Id, string Name)> currentChain = [];
    string shownQuery = "";
    bool resultsShown;

    public MainWindow(StartRequest request)
    {
        startRequest = request;
        ui = DispatcherQueue.GetForCurrentThread();
        settings = Settings.Load(AppPaths.Settings);
        library = new Library(AppPaths.Database);
        search = new SearchService(library.Db);
        files = new FileTable(Columns.FileList, settings.FileHeader);
        panel = new SidePanel(library, library.Lyrics, settings, () => Content.XamlRoot,
            () => WinRT.Interop.WindowNative.GetWindowHandle(this));
        // The last mode used is kept; an unknown value reads as Files (SPEC §6 "Direction task 5").
        mode = settings.SearchMode == "lyrics" ? SearchMode.Lyrics : SearchMode.Files;
        results = new FileTable(Columns.Search, settings.SearchHeader ?? new HeaderState { SortKey = "folder" }, searchResults: true);
        searchTimer = Timer(TimeSpan.FromMilliseconds(120), () => _ = RunSearch(inPlace: false));
        liveSearchTimer = Timer(TimeSpan.FromSeconds(1), () => _ = RunSearch(inPlace: true));
        refreshTimer = Timer(TimeSpan.FromMilliseconds(200), () => _ = ApplyChanges());
        statusTimer = Timer(TimeSpan.FromSeconds(6), () => status.Text = "");

        Title = "Refrain";
        SystemBackdrop = new MicaBackdrop();
        AppWindow.SetIcon(Path.Combine(AppContext.BaseDirectory, "Assets", "refrain.ico"));

        filesPage = Card(files);
        resultsPage.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });
        resultsPage.RowDefinitions.Add(new RowDefinition { Height = new GridLength(1, GridUnitType.Star) });
        resultsPage.Children.Add(resultCount);
        Grid.SetRow(results, 1);
        resultsPage.Children.Add(results);
        Grid.SetRow(cards, 1);
        resultsPage.Children.Add(cards);
        treeCard = Card(tree);
        splitter = Splitter(treeColumn, 1, width => settings.TreeWidth = width);

        var root = new Grid { Padding = new Thickness(8, 0, 8, 0), AllowDrop = true };
        root.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });
        root.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });
        root.RowDefinitions.Add(new RowDefinition { Height = new GridLength(1, GridUnitType.Star) });
        root.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });
        root.Children.Add(SearchRow());
        var nav = NavigationRow();
        Grid.SetRow(nav, 1);
        root.Children.Add(nav);
        var body = Body();
        Grid.SetRow(body, 2);
        root.Children.Add(body);
        var statusRow = new Grid();
        statusRow.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
        statusRow.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });
        statusRow.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });
        statusRow.Children.Add(status);
        Grid.SetColumn(lyricsStatus, 1);
        statusRow.Children.Add(lyricsStatus);
        Grid.SetColumn(lyricsRetry, 2);
        statusRow.Children.Add(lyricsRetry);
        ToolTipService.SetToolTip(lyricsRetry, "Try LRCLIB again now");
        Grid.SetRow(statusRow, 3);
        root.Children.Add(statusRow);
        Content = root;

        Wire(root);
        RestorePlacement();
        AppWindow.Closing += async (_, e) =>
        {
            if (closing || !panel.Dirty) return;
            e.Cancel = true;  // unsaved edits: ask first (SPEC §7)
            if (!await panel.ConfirmLeave()) return;
            closing = true;
            Close();
        };
        Closed += (_, _) => Shutdown();
        root.Loaded += async (_, _) =>
        {
            await LoadRoots();
            await OpenStartFolder();
            if (!Offscreen) searchBox.Focus(FocusState.Programmatic);  // the search has the focus at start (SPEC §33)
            if (!startRequest.IsEmpty) await OpenFromOutside(startRequest);
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
        var field = new Grid { HorizontalAlignment = HorizontalAlignment.Stretch, MaxWidth = 1040 };
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
        ApplyMode();
        row.Children.Add(field);

        var more = Icon("", "More");
        var menu = new MenuFlyout();
        var about = new MenuFlyoutItem { Text = "About Refrain" };
        about.Click += async (_, _) => await ShowAbout();
        var exit = new MenuFlyoutItem { Text = "Exit" };
        exit.Click += (_, _) => Close();
        menu.Items.Add(about);
        menu.Items.Add(new MenuFlyoutSeparator());
        menu.Items.Add(exit);
        more.Flyout = menu;
        more.Margin = new Thickness(8, 0, 0, 0);
        Grid.SetColumn(more, 1);
        row.Children.Add(more);
        return row;
    }

    FrameworkElement NavigationRow()
    {
        var row = new Grid { ColumnSpacing = 4, Margin = new Thickness(0, 0, 0, 8) };
        foldersButton.Content = Labelled("", "Folders");
        ToolTipService.SetToolTip(foldersButton, "Show or hide the folder tree (Ctrl+Shift+F)");
        explorerButton.Content = Labelled("", "Open in File Explorer");
        ToolTipService.SetToolTip(explorerButton, "Open this folder in File Explorer");
        UIElement[] items = [foldersButton, backButton, forwardButton, upButton, rescanButton, address, explorerButton];
        for (var i = 0; i < items.Length; i++)
        {
            row.ColumnDefinitions.Add(new ColumnDefinition
            {
                Width = items[i] == address ? new GridLength(1, GridUnitType.Star) : GridLength.Auto,
            });
            Grid.SetColumn((FrameworkElement)items[i], i);
            row.Children.Add(items[i]);
        }
        return row;
    }

    FrameworkElement Body()
    {
        var body = new Grid();
        treeColumn.Width = new GridLength(Math.Clamp(settings.TreeWidth, 120, 800));
        splitterColumn.Width = new GridLength(8);
        body.ColumnDefinitions.Add(treeColumn);
        body.ColumnDefinitions.Add(splitterColumn);
        body.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
        body.Children.Add(treeCard);
        Grid.SetColumn(splitter, 1);
        body.Children.Add(splitter);
        var page = new Grid();
        page.Children.Add(filesPage);
        var resultsCard = Card(resultsPage);
        resultsCard.Visibility = Visibility.Collapsed;
        page.Children.Add(resultsCard);
        resultsCardElement = resultsCard;
        Grid.SetColumn(page, 2);
        body.Children.Add(page);
        body.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(8) });
        panelColumn.Width = new GridLength(Math.Clamp(settings.PanelWidth, 240, 900));
        body.ColumnDefinitions.Add(panelColumn);
        var panelSplitter = Splitter(panelColumn, -1, width => settings.PanelWidth = width);
        Grid.SetColumn(panelSplitter, 3);
        body.Children.Add(panelSplitter);
        var panelCard = Card(panel);
        Grid.SetColumn(panelCard, 4);
        body.Children.Add(panelCard);
        return body;
    }

    UIElement? resultsCardElement;

    /// <summary>A panel as a rounded card on the window's layer (SPEC §36 "1." task 3).</summary>
    static Border Card(UIElement content) => new()
    {
        Child = content,
        CornerRadius = new CornerRadius(8),
        BorderThickness = new Thickness(1),
        Padding = new Thickness(3),
        Background = (Brush)Application.Current.Resources["LayerFillColorDefaultBrush"],
        BorderBrush = (Brush)Application.Current.Resources["CardStrokeColorDefaultBrush"],
    };

    static Button Icon(string glyph, string tip)
    {
        var button = new Button { Content = new FontIcon { Glyph = glyph, FontSize = 16 }, Padding = new Thickness(8) };
        ToolTipService.SetToolTip(button, tip);
        return button;
    }

    static StackPanel Labelled(string glyph, string label)
    {
        var panel = new StackPanel { Orientation = Orientation.Horizontal, Spacing = 8 };
        panel.Children.Add(new FontIcon { Glyph = glyph, FontSize = 16 });
        panel.Children.Add(new TextBlock { Text = label });
        return panel;
    }

    /// <summary>A column's width, dragged (the tree's to the right, the side panel's to the left); kept between
    /// sessions.</summary>
    FrameworkElement Splitter(ColumnDefinition column, int direction, Action<double> keep)
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
            var width = Math.Clamp(startWidth + direction * (e.GetCurrentPoint(null).Position.X - startX), 120, 900);
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
        library.Indexer.FolderChanged += id => ui.TryEnqueue(() =>
        {
            changedFolders.Add(id);
            refreshTimer.Start();
        });
        library.Indexer.ScanFinished += () => ui.TryEnqueue(() =>
        {
            if (current is { } id) changedFolders.Add(id);
            refreshTimer.Start();
        });
        library.Indexer.Progress += (folders, read, busy) => ui.TryEnqueue(() =>
        {
            if (busy) Status($"Indexing: {folders:N0} folders listed, {read:N0} files read", sticky: true);
            else if (status.Text.StartsWith("Indexing", StringComparison.Ordinal)) status.Text = "";
        });
        library.RootsChanged += () => ui.TryEnqueue(() => _ = LoadRoots());
        var lyrics = library.Lyrics;
        lyrics.FoldersChanged += folders => ui.TryEnqueue(() =>
        {
            changedFolders.UnionWith(folders);
            refreshTimer.Start();
        });
        lyrics.EntryReady += key => ui.TryEnqueue(() => _ = panel.LyricsReady(key));
        lyrics.ProgressChanged += progress => ui.TryEnqueue(() => ShowLyricsProgress(progress));
        lyrics.Unreachable += until => ui.TryEnqueue(() =>
        {
            Status($"LRCLIB unreachable, retrying at {until:HH:mm}.");
            _ = panel.LyricsStateChanged();
        });
        panel.PlayRequested += (id, subfolders) => Play(conn => Playlists.ForFolder(conn, id, subfolders), id);
        lyricsRetry.Click += (_, _) =>
        {
            lyrics.RetryNow();
            _ = panel.LyricsStateChanged();
        };
        filesMode.Click += (_, _) => SetMode(SearchMode.Files);
        lyricsMode.Click += (_, _) => SetMode(SearchMode.Lyrics);
        cards.Activated += row => _ = Activate(row, fromResults: true);
        cards.MenuRequested += (rows, anchor, point) => ShowMenu(rows, anchor, point, fromResults: true);
        cards.SelectionChanged += () => _ = SelectionChanged(cards);
        cards.Clicked += () => _ = panel.ShowLyrics();
        cards.List.KeyDown += (_, e) =>
        {
            if (e.Key != VirtualKey.Escape) return;
            e.Handled = true;
            ShowFiles();
            files.List.Focus(FocusState.Keyboard);
        };

        searchBox.TextChanged += (_, _) =>
        {
            if (searchBox.Text.Trim().Length == 0)
            {
                searchTimer.Stop();
                ShowFiles();
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
                ShowFiles();
            }
            else if (e.Key == VirtualKey.Down && resultsShown)
            {
                e.Handled = true;
                ShownResults.FocusFirst();
            }
        };
        results.List.KeyDown += (_, e) =>
        {
            if (e.Key != VirtualKey.Escape) return;
            e.Handled = true;
            ShowFiles();
            files.List.Focus(FocusState.Keyboard);
        };

        files.Activated += row => _ = Activate(row, fromResults: false);
        results.Activated += row => _ = Activate(row, fromResults: true);
        files.MenuRequested += (rows, anchor, point) => ShowMenu(rows, anchor, point, fromResults: false);
        results.MenuRequested += (rows, anchor, point) => ShowMenu(rows, anchor, point, fromResults: true);
        files.UpRequested += () => _ = GoUp();
        files.SelectionChanged += () => _ = SelectionChanged(files);
        results.SelectionChanged += () => _ = SelectionChanged(results);
        files.LayoutChanged += () => settings.FileHeader = files.State;
        results.LayoutChanged += () =>
        {
            settings.SearchHeader = results.State;
            if (resultsShown) _ = RunSearch(inPlace: true);
        };

        address.Subfolders = Subfolders;
        address.FolderChosen += id => _ = OpenFolder(id);
        address.PathTyped += path => _ = OpenPath(path);
        tree.Subfolders = Subfolders;
        tree.FolderChosen += id => _ = OpenFolder(id);
        tree.AddRequested += () => _ = PickFolder();
        tree.RemoveRequested += r => _ = ConfirmRemove(r);
        tree.RescanRequested += id => library.Indexer.Urgent(id);

        foldersButton.Click += (_, _) =>
        {
            settings.TreeVisible = foldersButton.IsChecked == true;
            ApplyTree();
        };
        backButton.Click += (_, _) => _ = GoBack();
        forwardButton.Click += (_, _) => _ = GoForward();
        upButton.Click += (_, _) => _ = GoUp();
        rescanButton.Click += (_, _) => Rescan();
        explorerButton.Click += (_, _) => RevealCurrent();

        Accelerator(root, VirtualKey.K, VirtualKeyModifiers.Control, () =>
        {
            searchBox.Focus(FocusState.Keyboard);
            searchBox.SelectAll();
        });
        Accelerator(root, VirtualKey.L, VirtualKeyModifiers.Control, address.EditText);
        Accelerator(root, VirtualKey.O, VirtualKeyModifiers.Control, () => _ = PickFolder());
        Accelerator(root, VirtualKey.F, VirtualKeyModifiers.Control | VirtualKeyModifiers.Shift, () =>
        {
            if (!foldersButton.IsEnabled) return;
            foldersButton.IsChecked = !foldersButton.IsChecked;
            settings.TreeVisible = foldersButton.IsChecked == true;
            ApplyTree();
        });
        Accelerator(root, VirtualKey.F5, VirtualKeyModifiers.None, Rescan);
        Accelerator(root, VirtualKey.Left, VirtualKeyModifiers.Menu, () => _ = GoBack());
        Accelerator(root, VirtualKey.Right, VirtualKeyModifiers.Menu, () => _ = GoForward());
        Accelerator(root, VirtualKey.Up, VirtualKeyModifiers.Menu, () => _ = GoUp());

        // The mouse's back and forward buttons move through history (SPEC §5).
        root.AddHandler(UIElement.PointerPressedEvent, new PointerEventHandler((_, e) =>
        {
            var properties = e.GetCurrentPoint(root).Properties;
            if (properties.IsXButton1Pressed) _ = GoBack();
            else if (properties.IsXButton2Pressed) _ = GoForward();
        }), handledEventsToo: true);

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

    static void Accelerator(UIElement target, VirtualKey key, VirtualKeyModifiers modifiers, Action action)
    {
        var accelerator = new KeyboardAccelerator { Key = key, Modifiers = modifiers };
        accelerator.Invoked += (_, e) =>
        {
            e.Handled = true;
            action();
        };
        target.KeyboardAccelerators.Add(accelerator);
        // WinUI would float the shortcut as a tooltip over the whole window each time it is used (Hugo, 2026-10-29:
        // unwanted); the buttons' hover texts already name their shortcuts.
        target.KeyboardAcceleratorPlacementMode = KeyboardAcceleratorPlacementMode.Hidden;
    }

    // -- reading the index off the UI thread --

    Task<T> Read<T>(Func<SqliteConnection, T> query) => Task.Run(() =>
    {
        using var conn = library.Db.Read();
        return query(conn);
    });

    Task<List<FolderNode>> Subfolders(long folderId) => Read(conn => Queries.ChildFolders(conn, folderId));

    // -- the folder tree and roots --

    async Task LoadRoots()
    {
        var roots = await Read(conn => Roots.All(conn)
            .Select(r => (Root: r, Top: Queries.TopEntry(conn, r.Id)))
            .Where(r => r.Top is not null)
            .Select(r => (r.Root, r.Top!.Value))
            .ToList());
        await tree.Load(roots, settings.ExpandedFolders);
        ApplyTree();
    }

    /// <summary>The tree shows when asked, and always while the library is empty: Add folder… lives there
    /// (SPEC §33 "Built (Direction task 8)").</summary>
    void ApplyTree()
    {
        var empty = tree.IsEmpty;
        var show = empty || settings.TreeVisible;
        foldersButton.IsEnabled = !empty;
        foldersButton.IsChecked = show;
        ToolTipService.SetToolTip(foldersButton, empty
            ? "The folder tree shows until a folder is added"
            : "Show or hide the folder tree (Ctrl+Shift+F)");
        treeCard.Visibility = splitter.Visibility = show ? Visibility.Visible : Visibility.Collapsed;
        treeColumn.Width = show ? new GridLength(Math.Clamp(settings.TreeWidth, 120, 800)) : new GridLength(0);
        splitterColumn.Width = new GridLength(show ? 8 : 0);
        tree.Visibility = show ? Visibility.Visible : Visibility.Collapsed;
        if (show && currentChain.Count > 0) _ = tree.Reveal(currentChain.Select(c => c.Id).ToList());
    }

    async Task PickFolder()
    {
        var picker = new Windows.Storage.Pickers.FolderPicker();
        picker.FileTypeFilter.Add("*");
        WinRT.Interop.InitializeWithWindow.Initialize(picker, WinRT.Interop.WindowNative.GetWindowHandle(this));
        var folder = await picker.PickSingleFolderAsync();
        if (folder is not null) await AddFolder(folder.Path);
    }

    /// <summary>The no-duplicates rule (SPEC §3): a folder already in the library, or inside one, is never added
    /// again; the app goes to it instead.</summary>
    async Task AddFolder(string path)
    {
        var result = await library.Add(path);
        switch (result.Outcome)
        {
            case AddOutcome.Added:
            case AddOutcome.Merged:
                await LoadRoots();
                Status(result.Outcome == AddOutcome.Merged
                    ? $"Added {result.Path}; the library folders inside it are now part of it."
                    : $"Added {result.Path}. Its folders are being indexed.");
                if (result.RootId is { } id && await Read(conn => Queries.TopEntry(conn, id)) is { } top)
                    await OpenFolder(top);
                break;
            case AddOutcome.AlreadyRoot:
            case AddOutcome.InsideRoot:
                Status($"{result.Path} is already in the library.");
                await OpenPath(result.Path);
                break;
            case AddOutcome.IsAFile:
                Status($"{result.Path} is a file; add the folder that holds it.");
                break;
            case AddOutcome.NotAFolder:
                Status($"{result.Path} can't be opened as a folder.");
                break;
        }
    }

    async Task ConfirmRemove(Root root)
    {
        var dialog = new ContentDialog
        {
            Title = "Remove from library?",
            Content = $"{root.Path} leaves the library. Nothing on disk changes, but adding it back means indexing " +
                      "it again from scratch.",
            PrimaryButtonText = "Remove",
            CloseButtonText = "Cancel",
            DefaultButton = ContentDialogButton.Close,
            XamlRoot = Content.XamlRoot,
        };
        if (await dialog.ShowAsync() != ContentDialogResult.Primary) return;
        var wasOpen = currentChain.Count > 0 &&
                      currentChain[0].Name.Equals(root.Path, StringComparison.OrdinalIgnoreCase);
        await library.Remove(root.Id);
        Status($"{root.Path} was removed from the library.");
        if (wasOpen)
        {
            current = null;
            currentChain = [];
            files.SetRows([]);
            address.Show([]);
            await OpenStartFolder();
        }
    }

    // -- navigation --

    async Task OpenStartFolder()
    {
        if (settings.LastFolder is { } last && await Read(conn => Queries.Find(conn, last)) is { Exact: true } found &&
            await Read(conn => Queries.IsDir(conn, found.Id)))
        {
            await OpenFolder(found.Id);
            return;
        }
        var first = await Read(conn => Roots.All(conn).Select(r => Queries.TopEntry(conn, r.Id)).FirstOrDefault(t => t is not null));
        if (first is { } top) await OpenFolder(top);
        UpdateButtons();
    }

    /// <summary>Opens a folder from the index at once (SPEC §5), then checks it against the disk. Going up by any way
    /// selects the folder you came from; <paramref name="select"/> picks a row instead.</summary>
    async Task OpenFolder(long folderId, long? select = null, bool record = true)
    {
        if (!await panel.ConfirmLeave()) return;
        var (rows, chain) = await Read(conn => (Queries.Listing(conn, folderId), Queries.Chain(conn, folderId)));
        if (chain.Count == 0) return;  // it left the index meanwhile
        if (select is null)
        {
            var at = currentChain.FindIndex(c => c.Id == folderId);
            if (at >= 0 && at + 1 < currentChain.Count) select = currentChain[at + 1].Id;
        }
        current = folderId;
        currentChain = chain;
        files.SetRows(Decorate(rows), select is { } id ? [id] : null);
        address.Show(chain);
        if (record) history.Open(folderId);
        settings.LastFolder = CurrentPath;
        library.FolderOpened(folderId);
        library.Lyrics.PrioritizeFolder(folderId);
        ShowFiles();
        UpdateButtons();
        if (tree.Visibility == Visibility.Visible) await tree.Reveal(chain.Select(c => c.Id).ToList());
    }

    /// <summary>A typed or pasted path: a folder opens, a file's folder opens with the file selected.</summary>
    async Task OpenPath(string path)
    {
        var found = await Read(conn =>
        {
            var hit = Queries.Find(conn, path);
            return hit is null ? ((long, bool, bool)?)null : (hit.Value.Id, hit.Value.Exact, Queries.IsDir(conn, hit.Value.Id));
        });
        if (found is not { } f)
        {
            Status($"{path} isn't in the library.");
            return;
        }
        var (id, exact, isDir) = f;
        if (!exact)
        {
            Status($"{path} isn't indexed (yet); its nearest indexed folder is open.");
            await OpenFolder(id);
            return;
        }
        if (isDir) await OpenFolder(id);
        else if (await Read(conn => Queries.ParentOf(conn, id)) is { } parent) await OpenFolder(parent, select: id);
    }

    async Task GoBack()
    {
        if (history.Back(Exists) is { } id) await OpenFolder(id, record: false);
    }

    async Task GoForward()
    {
        if (history.Forward(Exists) is { } id) await OpenFolder(id, record: false);
    }

    /// <summary>A one-row primary-key lookup: quick enough for the UI thread.</summary>
    bool Exists(long id)
    {
        using var conn = library.Db.Read();
        return Queries.Exists(conn, id);
    }

    async Task GoUp()
    {
        if (currentChain.Count < 2) return;
        await OpenFolder(currentChain[^2].Id);
    }

    void Rescan()
    {
        if (current is not { } id) return;
        library.Indexer.Urgent(id);
        Status("Rescanning this folder…");
    }

    void UpdateButtons()
    {
        backButton.IsEnabled = history.CanGoBack;
        forwardButton.IsEnabled = history.CanGoForward;
        upButton.IsEnabled = currentChain.Count > 1;
        rescanButton.IsEnabled = explorerButton.IsEnabled = current is not null;
    }

    async Task Activate(FileRow row, bool fromResults)
    {
        if (row.IsDir) await OpenFolder(row.Id);
        else if (fromResults) await OpenFolder(row.FolderId, select: row.Id);
        else if (await Read(conn => Queries.PathOf(conn, row.Id)) is { } path) Shell.Open(path);
    }

    void ShowMenu(IReadOnlyList<FileRow> rows, UIElement anchor, Windows.Foundation.Point point, bool fromResults)
    {
        var menu = new MenuFlyout();
        void Add(string text, Action action, bool enabled = true)
        {
            var item = new MenuFlyoutItem { Text = text, IsEnabled = enabled };
            item.Click += (_, _) => action();
            menu.Items.Add(item);
        }

        if (rows.Count == 0)
        {
            // The list's background: the open folder's menu, without Open (SPEC §5 "As built (M2 task 7)").
            if (fromResults || current is null) return;
            var open = current.Value;
            Add("Play folder", () => Play(conn => Playlists.ForFolder(conn, open, false), open));
            Add("Play folder and subfolders", () => Play(conn => Playlists.ForFolder(conn, open, true), open));
            Add("Open in File Explorer", RevealCurrent);
            Add("Copy path", () => Copy([CurrentPath]));
            Add("Rescan", Rescan);
        }
        else
        {
            var row = rows[0];
            if (fromResults)
            {
                Add("Go to folder", () => _ = OpenFolder(row.IsDir ? row.Id : row.FolderId, select: row.IsDir ? null : row.Id));
                Add("Open in File Explorer", () => _ = RevealRow(row), rows.Count == 1);
                Add("Open", () => _ = Activate(row, fromResults: false), rows.Count == 1);
            }
            else
            {
                Add("Open", () => _ = Activate(row, fromResults: false), rows.Count == 1);
                Add("Open in File Explorer", () => _ = RevealRow(row), rows.Count == 1);
            }
            if (row.IsDir && rows.Count == 1)
            {
                Add("Play folder", () => Play(conn => Playlists.ForFolder(conn, row.Id, false), row.Id));
                Add("Play folder and subfolders", () => Play(conn => Playlists.ForFolder(conn, row.Id, true), row.Id));
            }
            else if (rows.Any(r => r.IsAudio))
            {
                var ids = rows.Select(r => r.Id).ToList();
                Add("Play selection", () => Play(conn => Playlists.ForIds(conn, ids), null));
            }
            Add(rows.Count == 1 ? "Copy path" : "Copy paths", () => _ = CopyRows(rows));
            if (row.IsDir && rows.Count == 1) Add("Rescan", () => library.Indexer.Urgent(row.Id));
            if (fromResults && !string.IsNullOrEmpty(row.Artist))
                Add($"Filter by {row.Artist}", () => FilterByArtist(row.Artist));
        }
        menu.ShowAt(anchor, point);
    }

    /// <summary>Plays through the default app (SPEC §14): the entries come from the index and the playlist is written
    /// off the UI thread; with nothing to play, or no player for .m3u8, the status line or a message says so.</summary>
    void Play(Func<SqliteConnection, List<PlayEntry>> entries, long? folder)
    {
        _ = Task.Run(() =>
        {
            List<PlayEntry> list;
            using (var conn = library.Db.Read()) list = entries(conn);
            return list.Count == 0 ? null : Playlists.Write(list);
        }).ContinueWith(task => ui.TryEnqueue(async () =>
        {
            if (task.Exception is { } error)
            {
                Log.Error("Writing a playlist failed", error);
                Status("The playlist couldn't be written; see the log.");
                return;
            }
            if (task.Result is not { } playlist)
            {
                Status("There are no audio files to play here.");
                return;
            }
            if (Shell.TryOpen(playlist)) return;
            var dialog = new ContentDialog
            {
                Title = "No player for playlists",
                Content = "Windows has no app set to open .m3u8 playlists. Choose one in Settings > Apps > Default " +
                          "apps (search for .m3u8), or play the files from File Explorer.",
                PrimaryButtonText = "Open in File Explorer",
                CloseButtonText = "Close",
                XamlRoot = Content.XamlRoot,
            };
            if (await dialog.ShowAsync() != ContentDialogResult.Primary) return;
            if (folder is { } id && await Read(conn => Queries.PathOf(conn, id)) is { } path) Shell.Reveal(path, select: false);
            else if (Shown.SelectedRows.FirstOrDefault() is { } first) await RevealRow(first);
        }), TaskScheduler.Default);
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

    /// <summary>A start from outside (SPEC §29, §31): the <c>refrain</c> keyword or a folder argument, or a right-click
    /// entry. An indexed folder opens; one inside the library but not indexed is rescanned first; one outside is
    /// added. The wait for the index lasts at most 2 minutes, and a newer request replaces it.</summary>
    public async Task OpenFromOutside(StartRequest request)
    {
        BringToFront();
        var folder = request.Entry switch
        {
            null => request.Folder,
            "open" => request.Paths.FirstOrDefault(),
            _ => null,
        };
        if (request.Entry is not null and not "open")
        {
            Status($"This version of Refrain has no \"{request.Entry}\" entry yet.");
            return;
        }
        if (folder is null) return;
        var path = RealPath.Normalize(folder);
        if (!await Task.Run(() => Directory.Exists(path)))
        {
            Status($"{path} isn't a folder that can be opened.");
            return;
        }
        waitingFolder = null;
        var found = await Read(conn => Queries.Find(conn, path));
        if (found is { Exact: true } hit)
        {
            await OpenFolder(hit.Id);
            return;
        }
        waitingFolder = (path, DateTime.Now.AddMinutes(2));
        if (found is null) await AddFolder(path);
        else library.Indexer.ScanPath(path, recursive: true);
        await OpenWaitingFolder();
    }

    /// <summary>Opens the folder a start from outside waits for, once the index has it.</summary>
    async Task OpenWaitingFolder()
    {
        if (waitingFolder is not { } waiting) return;
        if (DateTime.Now > waiting.Until)
        {
            waitingFolder = null;
            Status($"{waiting.Path} isn't indexed yet; it opens from the folder tree once it is.");
            return;
        }
        if (await Read(conn => Queries.Find(conn, waiting.Path)) is not { Exact: true } hit) return;
        waitingFolder = null;
        await OpenFolder(hit.Id);
    }

    /// <summary>An <c>artist:</c> filter added to the query (the seek_lyrics page's artist checkboxes, SPEC §6).</summary>
    void FilterByArtist(string artist)
    {
        searchBox.Text = $"{searchBox.Text.TrimEnd()} artist:\"{artist.Replace("\"", "")}\"".TrimStart();
        searchBox.SelectionStart = searchBox.Text.Length;
    }

    /// <summary>A pending row whose key's last lookup failed this session shows "error" (SPEC §5).</summary>
    List<FileRow> Decorate(List<FileRow> rows)
    {
        var failed = library.Lyrics.FailedKeys();
        if (failed.Count == 0) return rows;
        return rows.Select(r => r.Lrclib == "pending" && r.LyricsKey is { } key && failed.Contains(key) ? r with { Lrclib = "error" } : r)
            .ToList();
    }

    string CurrentPath => Path.Combine(currentChain.Select(c => c.Name).ToArray());

    void RevealCurrent()
    {
        if (currentChain.Count > 0) Shell.Reveal(CurrentPath, select: false);
    }

    async Task RevealRow(FileRow row)
    {
        if (await Read(conn => Queries.PathOf(conn, row.Id)) is { } path) Shell.Reveal(path, select: !row.IsDir);
    }

    async Task CopyRows(IReadOnlyList<FileRow> rows)
    {
        var ids = rows.Select(r => r.Id).ToList();
        Copy(await Read(conn => ids.Select(id => Queries.PathOf(conn, id)).OfType<string>().ToList()));
    }

    static void Copy(IReadOnlyList<string> paths)
    {
        var package = new DataPackage();
        package.SetText(string.Join(Environment.NewLine, paths));
        Clipboard.SetContent(package);
    }

    // -- live updates --

    /// <summary>The indexer changed folders: the open one updates in place (or its nearest surviving ancestor opens
    /// when it left the index), the tree reloads them, and the search re-runs after 1 s.</summary>
    async Task ApplyChanges()
    {
        var changed = changedFolders.ToList();
        changedFolders.Clear();
        if (current is { } id && (changed.Contains(id) || currentChain.Any(c => changed.Contains(c.Id))))
        {
            var chainIds = currentChain.Select(c => c.Id).ToList();
            var (rows, chain, survivor) = await Read(conn =>
            {
                var exists = Queries.Exists(conn, id);
                return exists
                    ? (Queries.Listing(conn, id), Queries.Chain(conn, id), (long?)null)
                    : ([], [], Queries.NearestSurviving(conn, chainIds));
            });
            if (current == id)
            {
                if (chain.Count > 0)
                {
                    files.UpdateRows(Decorate(rows));
                    currentChain = chain;
                    address.Show(chain);
                }
                else if (survivor is { } ancestor)
                {
                    history.Replace(ancestor);
                    await OpenFolder(ancestor, record: false);
                }
            }
        }
        foreach (var folder in changed) await tree.Refresh(folder);
        await OpenWaitingFolder();
        await panel.Refresh(changed, Shown.SelectedRows, current);
        if (resultsShown) liveSearchTimer.Start();
    }

    // -- search --

    async Task RunSearch(bool inPlace)
    {
        var text = searchBox.Text;
        if (text.Trim().Length == 0)
        {
            ShowFiles();
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
        ShowResults();
    }

    IRowList ShownResults => mode == SearchMode.Lyrics ? cards : results;

    IRowList Shown => resultsShown ? ShownResults : files;

    /// <summary>The side panel follows the shown list's selection; with unsaved edits it asks first, and Cancel keeps
    /// the selection as it was.</summary>
    async Task SelectionChanged(IRowList table)
    {
        if (table != Shown || restoringSelection) return;
        var rows = table.SelectedRows;
        var ids = rows.Select(r => r.Id).ToList();
        if (table == panelTable && ids.SequenceEqual(panelSelection) && panelFolder == current) return;
        if (!await panel.ConfirmLeave())
        {
            restoringSelection = true;
            panelTable?.Select(panelSelection, scroll: false);
            restoringSelection = false;
            return;
        }
        panelTable = table;
        panelSelection = ids;
        panelFolder = current;
        await panel.Show(rows, current);
    }

    IRowList? panelTable;
    List<long> panelSelection = [];
    long? panelFolder;
    bool restoringSelection;
    bool closing;

    void ShowResults()
    {
        results.Visibility = mode == SearchMode.Files ? Visibility.Visible : Visibility.Collapsed;
        cards.Visibility = mode == SearchMode.Lyrics ? Visibility.Visible : Visibility.Collapsed;
        if (resultsShown) return;
        resultsShown = true;
        filesPage.Visibility = Visibility.Collapsed;
        resultsCardElement!.Visibility = Visibility.Visible;
        _ = panel.ShowLyrics();  // opening the search brings the Lyrics tab forward once (SPEC §6)
        _ = SelectionChanged(ShownResults);
    }

    /// <summary>The Files | Lyrics toggle (SPEC §6 "Direction task 5"): the query on screen runs again in the new mode,
    /// the page opens when the field has text, and the focus goes back to the field.</summary>
    void SetMode(SearchMode next)
    {
        mode = next;
        settings.SearchMode = next == SearchMode.Lyrics ? "lyrics" : "files";
        ApplyMode();
        if (searchBox.Text.Trim().Length > 0)
        {
            if (resultsShown) ShowResultsFor(next);
            _ = RunSearch(inPlace: false);
        }
        searchBox.Focus(FocusState.Programmatic);
    }

    void ShowResultsFor(SearchMode shown)
    {
        results.Visibility = shown == SearchMode.Files ? Visibility.Visible : Visibility.Collapsed;
        cards.Visibility = shown == SearchMode.Lyrics ? Visibility.Visible : Visibility.Collapsed;
        _ = SelectionChanged(ShownResults);
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
    void ShowLyricsProgress(LyricsProgress progress)
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

    /// <summary>Back to the file list as it was (Esc, or an emptied field).</summary>
    void ShowFiles()
    {
        if (!resultsShown) return;
        resultsShown = false;
        liveSearchTimer.Stop();
        resultsCardElement!.Visibility = Visibility.Collapsed;
        filesPage.Visibility = Visibility.Visible;
        _ = SelectionChanged(files);
    }

    // -- status, about, placement --

    void Status(string text, bool sticky = false)
    {
        status.Text = text;
        statusTimer.Stop();
        if (!sticky) statusTimer.Start();
    }

    async Task ShowAbout()
    {
        var version = typeof(MainWindow).Assembly.GetName().Version;
        var dialog = new ContentDialog
        {
            Title = "Refrain",
            Content = $"Version {version?.ToString(3)} (preview of the rebuild)\n\n" +
                      $"Its index and settings: {AppPaths.DataFolder}\n\nLicence: GPL-3.0-or-later.",
            CloseButtonText = "Close",
            XamlRoot = Content.XamlRoot,
        };
        await dialog.ShowAsync();
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
            AppWindow.MoveAndResize(new RectInt32(right + 200, 0, 1280, 800));
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
            AppWindow.Resize(new SizeInt32(1280, 800));
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
        settings.ExpandedFolders = tree.ExpandedPaths();
        settings.FileHeader = files.State;
        settings.SearchHeader = results.State;
        settings.Save(AppPaths.Settings);
        library.Dispose();
    }
}
