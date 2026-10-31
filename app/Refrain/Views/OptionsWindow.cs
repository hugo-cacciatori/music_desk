using System.Globalization;
using Microsoft.UI.Windowing;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Media;
using Refrain.Core;
using Refrain.Core.Index;
using Refrain.Core.Options;
using Refrain.Core.Tags;
using Windows.Graphics;

namespace Refrain.Views;

/// <summary>The Options window (SPEC §12, §36 "8.", "11."): every setting, and only there. Pages Library (the library
/// folders: add, rescan, remove), File names, ReplayGain, Tidy, Split and Trim; each setting page saves as it changes,
/// shows its problems inline and has Restore defaults; Restore all defaults sits beside Close. The same window from the app's … menu and from Explorer's Settings entry.</summary>
public sealed partial class OptionsWindow : Window
{
    readonly Settings settings;
    readonly Action save;
    readonly Func<Library> library;
    readonly ListView pages = new() { SelectionMode = ListViewSelectionMode.Single, Width = 200 };
    readonly ScrollViewer content = new() { HorizontalScrollMode = ScrollMode.Disabled, Padding = new Thickness(24, 16, 24, 16) };
    readonly string? examplePath;
    readonly Dictionary<string, Func<FrameworkElement>> builders = [];
    bool filling;

    /// <summary><paramref name="examplePath"/>: what the File names example reads its first track from (the
    /// right-clicked item, or the app's open folder).</summary>
    public OptionsWindow(Settings settings, Action save, string? examplePath, Func<Library> library)
    {
        this.settings = settings;
        this.library = library;
        this.save = save;
        this.examplePath = examplePath;
        Ui.Prepare(this, "Refrain · Options");

        builders["Library"] = LibraryPage;
        builders["File names"] = FileNamesPage;
        builders["ReplayGain"] = ReplayGainPage;
        builders["Tidy"] = TidyPage;
        builders["Split"] = SplitPage;
        builders["Trim"] = TrimPage;
        builders["Player"] = PlayerPage;
        foreach (var name in builders.Keys) pages.Items.Add(name);

        var body = new Grid();
        body.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });
        body.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
        pages.Margin = new Thickness(8, 16, 0, 8);
        body.Children.Add(pages);
        var card = new Border
        {
            Child = content,
            CornerRadius = new CornerRadius(8),
            BorderThickness = new Thickness(1),
            Margin = new Thickness(8, 16, 16, 8),
            Background = Ui.Brush("LayerFillColorDefaultBrush"),
            BorderBrush = Ui.Brush("CardStrokeColorDefaultBrush"),
        };
        Grid.SetColumn(card, 1);
        body.Children.Add(card);

        var bar = new Grid { Padding = new Thickness(16, 4, 16, 16), ColumnSpacing = 8 };
        bar.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });
        bar.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
        bar.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });
        var restoreAll = Ui.Button("Restore all defaults", "Every page's settings back to their defaults");
        restoreAll.Click += async (_, _) => await RestoreAll();
        bar.Children.Add(restoreAll);
        var close = Ui.Primary("Close");
        close.Click += (_, _) => Close();
        Grid.SetColumn(close, 2);
        bar.Children.Add(close);

        var root = new Grid();
        root.RowDefinitions.Add(new RowDefinition { Height = new GridLength(1, GridUnitType.Star) });
        root.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });
        root.Children.Add(body);
        Grid.SetRow(bar, 1);
        root.Children.Add(bar);
        Content = root;

        pages.SelectionChanged += (_, _) =>
        {
            if (pages.SelectedItem is not string name) return;
            settings.OptionsPage = name;
            content.Content = builders[name]();
            save();
        };
        pages.SelectedItem = builders.ContainsKey(settings.OptionsPage) ? settings.OptionsPage : "File names";
    }

    /// <summary>Shows the window: activated normally, never taking the focus in an agent's off-screen check.</summary>
    public void ShowWindow()
    {
        Ui.Show(this);
        Ui.Fit(this);
    }

    void ShowPage(string name)
    {
        content.Content = builders[name]();
    }

    // -- the pieces of a page --

    static StackPanel Page(string title, string description)
    {
        var page = new StackPanel { Spacing = 12 };
        page.Children.Add(new TextBlock { Text = title, FontSize = 24, FontWeight = Microsoft.UI.Text.FontWeights.SemiBold });
        page.Children.Add(Ui.Secondary(description));
        return page;
    }

    static TextBox Field(StackPanel page, string label, string text, string? help = null, bool multiline = false)
    {
        var box = new TextBox
        {
            Header = label,
            Text = text,
            AcceptsReturn = multiline,
            TextWrapping = multiline ? TextWrapping.Wrap : TextWrapping.NoWrap,
            MinHeight = multiline ? 120 : 0,
        };
        if (multiline) box.FontFamily = new FontFamily("Cascadia Mono, Consolas");
        if (help is not null) ToolTipService.SetToolTip(box, help);
        page.Children.Add(box);
        return box;
    }

    static TextBlock Problems(StackPanel page)
    {
        var text = new TextBlock { TextWrapping = TextWrapping.Wrap, Foreground = Ui.Brush("SystemFillColorCriticalBrush") };
        page.Children.Add(text);
        return text;
    }

    static void ShowProblems(TextBlock block, IReadOnlyList<string> problems) =>
        block.Text = string.Join("\n", problems);

    Button RestoreButton(StackPanel page, string name, Action restore)
    {
        var button = Ui.Button("Restore defaults", $"The {name} settings back to their defaults");
        button.Click += (_, _) =>
        {
            restore();
            save();
            ShowPage(name);
        };
        page.Children.Add(button);
        return button;
    }

    static List<string> Lines(string text) =>
        text.Replace("\r\n", "\n").Replace('\r', '\n').Split('\n').Where(l => l.Length > 0).ToList();

    static List<string> List(string text) =>
        text.Split([',', ';', ' '], StringSplitOptions.RemoveEmptyEntries | StringSplitOptions.TrimEntries).ToList();

    static List<string> Keywords(string text) =>
        text.Split(',').Select(k => k.Trim()).Where(k => k.Length > 0).ToList();

    // -- the pages --

    /// <summary>The library folders (SPEC §3, §36 "11."; the folder tree's menu before): each with Rescan and Remove…,
    /// and Add a folder…. Not a setting: no Restore defaults, and Restore all defaults leaves it alone.</summary>
    FrameworkElement LibraryPage()
    {
        var page = Page("Library", "The folders Refrain indexes and searches. Removing one only takes it out of the " +
                                   "library: nothing on disk changes.");
        var rows = new StackPanel { Spacing = 4 };
        var status = Ui.Secondary();
        var add = Ui.Button("Add a folder…", "Add a music folder to the library; its files are indexed in the background");
        page.Children.Add(rows);
        page.Children.Add(add);
        page.Children.Add(status);
        var lib = library();
        var queue = DispatcherQueue;

        async Task Fill()
        {
            var roots = await Task.Run(() =>
            {
                using var conn = lib.Db.Read();
                return Roots.All(conn).Select(r => (Root: r, Top: Queries.TopEntry(conn, r.Id))).ToList();
            });
            rows.Children.Clear();
            if (roots.Count == 0) rows.Children.Add(Ui.Secondary("No folder yet."));
            foreach (var (root, top) in roots)
            {
                var row = new Grid { ColumnSpacing = 8 };
                row.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
                row.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });
                row.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });
                row.Children.Add(new TextBlock
                {
                    Text = root.Online ? root.Path : $"{root.Path} (offline)",
                    VerticalAlignment = VerticalAlignment.Center,
                    TextTrimming = TextTrimming.CharacterEllipsis,
                });
                var rescan = Ui.Button("Rescan", "Read this folder again from the disk");
                rescan.IsEnabled = top is not null;
                rescan.Click += (_, _) =>
                {
                    if (top is { } id) lib.Indexer.Urgent(id);
                    status.Text = $"Rescanning {root.Path}…";
                };
                Grid.SetColumn(rescan, 1);
                row.Children.Add(rescan);
                var remove = Ui.Button("Remove…", "Take this folder out of the library; nothing on disk changes");
                remove.Click += async (_, _) =>
                {
                    var dialog = new ContentDialog
                    {
                        Title = "Remove from library?",
                        Content = $"{root.Path} leaves the library. Nothing on disk changes, but adding it back means " +
                                  "indexing it again from scratch.",
                        PrimaryButtonText = "Remove",
                        CloseButtonText = "Cancel",
                        DefaultButton = ContentDialogButton.Close,
                        XamlRoot = Content.XamlRoot,
                    };
                    if (await dialog.ShowAsync() != ContentDialogResult.Primary) return;
                    await lib.Remove(root.Id);
                    status.Text = $"{root.Path} was removed from the library.";
                };
                Grid.SetColumn(remove, 2);
                row.Children.Add(remove);
                rows.Children.Add(row);
            }
        }

        add.Click += async (_, _) =>
        {
            var picker = new Windows.Storage.Pickers.FolderPicker();
            picker.FileTypeFilter.Add("*");
            WinRT.Interop.InitializeWithWindow.Initialize(picker, WinRT.Interop.WindowNative.GetWindowHandle(this));
            if (await picker.PickSingleFolderAsync() is not { } folder) return;
            var result = await lib.Add(folder.Path);
            status.Text = result.Outcome switch
            {
                AddOutcome.Merged => $"Added {result.Path}; the library folders inside it are now part of it.",
                AddOutcome.Added => $"Added {result.Path}. Its folders are being indexed.",
                AddOutcome.AlreadyRoot or AddOutcome.InsideRoot => $"{result.Path} is already in the library.",
                AddOutcome.IsAFile => $"{result.Path} is a file; add the folder that holds it.",
                _ => $"{result.Path} can't be opened as a folder.",
            };
        };
        void Changed() => queue.TryEnqueue(() => _ = Fill());
        lib.RootsChanged += Changed;
        page.Unloaded += (_, _) => lib.RootsChanged -= Changed;
        _ = Fill();
        return page;
    }

    FrameworkElement FileNamesPage()
    {
        var options = settings.GetFileNames();
        var page = Page("File names",
            "How Tidy names a music file from its tags: the first mask whose fields are all present names the file. " +
            "Characters Windows refuses in file names are replaced. One mask per line, in order.");
        filling = true;
        var masks = Field(page, "Masks", string.Join("\r", options.Masks), "One mask per line; the first that fits names the file", multiline: true);
        var insert = new DropDownButton { Content = "Insert field" };
        var fields = new MenuFlyout();
        foreach (var field in Masks.Fields)
        {
            var token = field == "track" ? "{track:02}" : $"{{{field}}}";
            var item = new MenuFlyoutItem { Text = token };
            item.Click += (_, _) =>
            {
                var at = masks.SelectionStart;
                masks.Text = masks.Text.Insert(at, token);
                masks.SelectionStart = at + token.Length;
                masks.Focus(FocusState.Programmatic);
            };
            fields.Items.Add(item);
        }
        fields.Placement = Microsoft.UI.Xaml.Controls.Primitives.FlyoutPlacementMode.BottomEdgeAlignedLeft;
        insert.Flyout = fields;
        ToolTipService.SetToolTip(insert, "Insert a field at the cursor");
        page.Children.Add(insert);
        var replacement = Field(page, "Replacement for characters not allowed in file names", options.Replacement);
        var example = Ui.Secondary();
        page.Children.Add(example);
        var problems = Problems(page);
        RestoreButton(page, "File names", () => settings.Set(FileNamesOptions.Default));
        filling = false;

        FileTags? exampleTags = null;
        void Update()
        {
            var edited = new FileNamesOptions(Lines(masks.Text), replacement.Text);
            var found = edited.Validate();
            ShowProblems(problems, found);
            if (!filling)
            {
                settings.Set(edited);
                save();
            }
            if (exampleTags is null)
            {
                example.Text = examplePath is null ? "" : "No track here to show an example.";
                return;
            }
            var name = found.Count == 0
                ? Masks.NameFor(RenameFields.Of(exampleTags), edited.Masks, edited.Replacement, Path.GetExtension(exampleTags.Path))?.Name
                : null;
            example.Text = $"Example: {Path.GetFileName(exampleTags.Path)} → {name ?? "(no mask fits its tags; it keeps its name)"}";
        }
        masks.TextChanged += (_, _) => Update();
        replacement.TextChanged += (_, _) => Update();
        Update();
        if (examplePath is { } where)
            _ = Task.Run(() =>
            {
                try
                {
                    return RenameFields.FirstTrack(where) is { } track ? TagFile.Read(track) : null;
                }
                catch (Exception)
                {
                    return null;
                }
            }).ContinueWith(t => DispatcherQueue.TryEnqueue(() =>
            {
                exampleTags = t.Result;
                Update();
            }), TaskScheduler.Default);
        return page;
    }

    FrameworkElement ReplayGainPage()
    {
        var options = settings.GetReplayGain();
        var page = Page("ReplayGain",
            "Measures loudness with rsgain at the target level, lowers any track gain that would clip, sets the album gain " +
            "to the lowest track gain and the album peak to the highest track peak, and writes the comment line. It then " +
            "copies the album values into the lite, bonus and CD2 subfolders. Its report replaces replaygain.log in the album folder.");
        string N(double value) => value.ToString(CultureInfo.InvariantCulture);
        filling = true;
        var target = Field(page, $"Target loudness (LUFS, {Python.G(ReplayGainOptions.LowestTarget)} to {Python.G(ReplayGainOptions.HighestTarget)})", N(options.Target));
        var advanced = new StackPanel { Spacing = 12 };
        var expander = new Expander
        {
            Header = "Advanced",
            Content = advanced,
            HorizontalAlignment = HorizontalAlignment.Stretch,
            HorizontalContentAlignment = HorizontalAlignment.Stretch,
        };
        page.Children.Add(expander);
        var threshold = Field(advanced, "Safety threshold (a peak of 1.0 is full scale)", N(options.SafetyThreshold));
        var margin = Field(advanced, "Safety margin (dB)", N(options.SafetyMargin));
        var subfolders = Field(advanced, "Album-data subfolder keywords (comma-separated)", string.Join(", ", options.SubfolderKeywords));
        var trackData = Field(advanced, "Track-data keywords", string.Join(", ", options.TrackDataKeywords));
        var excluded = Field(advanced, "Excluded keywords", string.Join(", ", options.ExcludedKeywords));
        var log = Field(advanced, "Log file name", options.LogFile);
        var comment = Field(advanced, "Comment text ({target} is the target)", options.Comment);
        var problems = Problems(page);
        RestoreButton(page, "ReplayGain", () => settings.Set(ReplayGainOptions.Default));
        filling = false;

        void Update()
        {
            var numbers = new List<string>();
            double Number(TextBox box, string name, double fallback)
            {
                if (double.TryParse(box.Text.Trim(), NumberStyles.Float, CultureInfo.InvariantCulture, out var value)) return value;
                numbers.Add($"{name} must be a number (with a dot for decimals)");
                return fallback;
            }
            var current = settings.GetReplayGain();
            var edited = new ReplayGainOptions(
                Number(target, "The target loudness", current.Target), Number(threshold, "The safety threshold", current.SafetyThreshold),
                Number(margin, "The safety margin", current.SafetyMargin), Keywords(subfolders.Text), Keywords(trackData.Text),
                Keywords(excluded.Text), log.Text, comment.Text);
            ShowProblems(problems, [.. numbers, .. edited.Validate()]);
            if (filling) return;
            settings.Set(edited);
            save();
        }
        foreach (var box in new[] { target, threshold, margin, subfolders, trackData, excluded, log, comment })
            box.TextChanged += (_, _) => Update();
        Update();
        return page;
    }

    FrameworkElement TidyPage()
    {
        var options = settings.GetTidy();
        var page = Page("Tidy",
            "Tidy splits every album below a folder that is still one image and its cue sheet, renames the music files " +
            "from their tags (the File names masks), runs ReplayGain where an album needs it, and offers the split " +
            "images, the pictures, videos, cue sheets, playlists and empty folders it finds for the Recycle Bin, one by one.");
        filling = true;
        var music = Field(page, "Music file types it renames", string.Join(" ", options.MusicTypes));
        var pictures = Field(page, "Pictures it offers", string.Join(" ", options.Pictures));
        var videos = Field(page, "Videos it offers", string.Join(" ", options.Videos));
        var others = Field(page, "Other files it offers", string.Join(" ", options.Others));
        var problems = Problems(page);
        RestoreButton(page, "Tidy", () => settings.Set(TidyOptions.Default));
        filling = false;

        void Update()
        {
            var edited = new TidyOptions(List(music.Text), List(pictures.Text), List(videos.Text), List(others.Text));
            ShowProblems(problems, edited.Validate());
            if (filling) return;
            settings.Set(edited);
            save();
        }
        foreach (var box in new[] { music, pictures, videos, others }) box.TextChanged += (_, _) => Update();
        Update();
        return page;
    }

    FrameworkElement SplitPage()
    {
        var page = Page("Split", "The cue sheet editor: how it saves a new cue sheet (New cue sheet… on an audio file).");
        var beside = new CheckBox
        {
            Content = "Save new cue sheets beside their audio file without asking",
            IsChecked = settings.SaveCueBeside,
        };
        ToolTipService.SetToolTip(beside, "Save puts a new sheet next to its audio file at once, named after it (numbered if the name is taken), without asking for a name");
        void Changed()
        {
            settings.SaveCueBeside = beside.IsChecked == true;
            save();
        }
        beside.Checked += (_, _) => Changed();
        beside.Unchecked += (_, _) => Changed();
        page.Children.Add(beside);
        RestoreButton(page, "Split", () => settings.SaveCueBeside = false);
        return page;
    }

    FrameworkElement TrimPage()
    {
        var page = Page("Trim", "Trimming one track: the copy goes in this subfolder of the track's folder, created if missing. " +
                                "The original is never changed.");
        filling = true;
        var subfolder = Field(page, "Subfolder", settings.TrimSubfolder, "The subfolder's name, the same for every track (lite by default)");
        var problems = Problems(page);
        RestoreButton(page, "Trim", () => settings.TrimSubfolder = Core.Cue.Trim.DefaultSubfolder);
        filling = false;

        void Update()
        {
            var why = Core.Cue.Trim.CheckSubfolderName(subfolder.Text);
            ShowProblems(problems, why is null ? [] : [why]);
            if (filling || why is not null) return;  // a name Windows refuses isn't saved
            settings.TrimSubfolder = subfolder.Text;
            save();
        }
        subfolder.TextChanged += (_, _) => Update();
        Update();
        return page;
    }

    /// <summary>The player (SPEC §37): what happens when an album ends, whether Refrain reopens on the last song, and
    /// what a task starting on the playing song's folder does.</summary>
    FrameworkElement PlayerPage()
    {
        var page = Page("Player", "The bar at the bottom of the Search window, which plays a song chosen in the discography " +
                                  "view and goes on through its album.");
        StackPanel Choice(string header, string group, (string Value, string Label, string Help)[] options, string current, Action<string> set)
        {
            var panel = new StackPanel { Spacing = 2 };
            panel.Children.Add(new TextBlock { Text = header, Margin = new Thickness(0, 4, 0, 2) });
            foreach (var (value, label, help) in options)
            {
                var radio = new RadioButton { Content = label, GroupName = group, IsChecked = value == current };
                ToolTipService.SetToolTip(radio, help);
                radio.Checked += (_, _) =>
                {
                    set(value);
                    save();
                };
                panel.Children.Add(radio);
            }
            page.Children.Add(panel);
            return panel;
        }
        Choice("When an album ends", "player-end",
        [
            ("loop", "Loop the album", "After its last song, the album starts over from the first"),
            ("once", "Play the album once, then stop", "After its last song, the player stops"),
        ], settings.PlayerAtEnd == "once" ? "once" : "loop", value => settings.PlayerAtEnd = value);
        Choice("When a task (ReplayGain, Tidy, a split, a trim…) starts on the folder of the song playing or of a song after it in the album", "player-busy",
        [
            ("stop", "Stop playback and say why", "The player stops and the bar says which task is changing which folder; press play once it ends"),
            ("skip", "Skip to the next song of the album the task doesn't touch",
             "The player goes on with the album's other songs, leaving out the files the task is changing, and the bar says so"),
        ], settings.PlayerOnBusy == "skip" ? "skip" : "stop", value => settings.PlayerOnBusy = value);
        var keep = new CheckBox { Content = "Reopen Refrain on the last song, paused where it was", IsChecked = settings.PlayerKeep };
        ToolTipService.SetToolTip(keep, "Closing Refrain always stops the music; with this, the bar shows the same song next time, ready to play");
        void Kept()
        {
            settings.PlayerKeep = keep.IsChecked == true;
            save();
        }
        keep.Checked += (_, _) => Kept();
        keep.Unchecked += (_, _) => Kept();
        page.Children.Add(keep);
        var previews = new CheckBox { Content = "Play the search preview's excerpts with their sound", IsChecked = settings.PreviewSound };
        ToolTipService.SetToolTip(previews, "A click on a result plays about 30 seconds of it; never over the player, which keeps playing while previews stay silent. The speaker button on the preview sets this too");
        void Previews()
        {
            settings.PreviewSound = previews.IsChecked == true;
            save();
        }
        previews.Checked += (_, _) => Previews();
        previews.Unchecked += (_, _) => Previews();
        page.Children.Add(previews);
        RestoreButton(page, "Player", RestorePlayer);
        return page;
    }

    void RestorePlayer()
    {
        settings.PlayerAtEnd = "loop";
        settings.PlayerOnBusy = "stop";
        settings.PlayerKeep = true;
        settings.PreviewSound = true;
    }

    async Task RestoreAll()
    {
        var dialog = new ContentDialog
        {
            Title = "Restore every setting?",
            Content = "Every page goes back to its defaults. Your masks and other changes will be lost.",
            PrimaryButtonText = "Restore",
            CloseButtonText = "Cancel",
            DefaultButton = ContentDialogButton.Close,
            XamlRoot = Content.XamlRoot,
        };
        if (await dialog.ShowAsync() != ContentDialogResult.Primary) return;
        settings.Set(FileNamesOptions.Default);
        settings.Set(ReplayGainOptions.Default);
        settings.Set(TidyOptions.Default);
        settings.SaveCueBeside = false;
        settings.TrimSubfolder = Core.Cue.Trim.DefaultSubfolder;
        RestorePlayer();
        save();
        if (pages.SelectedItem is string name) ShowPage(name);
    }
}
