using System.Collections.Concurrent;
using Microsoft.UI.Dispatching;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Media;
using Refrain.Core;
using Refrain.Core.Cue;
using Refrain.Core.Tags;

namespace Refrain.Views;

/// <summary>The cue sheet editor (SPEC §22, §31 "Edit this cue sheet…"), in a window of its own: the source and encoding
/// pickers, the album fields and their other lines, the track table, the selected track's other lines, Add / Delete
/// track, Fill from track lengths…, live checks, Preview in player, Save and Save as…. It edits and saves only: the split
/// is the Split window's. Opened on a .cue, or on an audio file for New cue sheet… (a new, unsaved sheet).</summary>
public sealed partial class CueEditorWindow : Window
{
    const int PlanDelayMs = 300;
    static readonly (string Key, string Header, double Width)[] Columns =
    [
        ("number", "#", 36), ("start", "Start", 84), ("gap", "Gap", 84), ("length", "Length", 76), ("title", "Title", 200),
        ("performer", "Performer", 150), ("songwriter", "Songwriter", 130), ("isrc", "ISRC", 120), ("flags", "Flags", 64),
        ("name", "New name", 220),
    ];
    static readonly HashSet<string> Editable = ["start", "gap", "title", "performer", "songwriter", "isrc", "flags"];
    static readonly (string Key, string Label)[] AlbumLabels =
    [
        ("performer", "Performer"), ("title", "Title"), ("genre", "Genre"), ("date", "Date"), ("disc", "Disc"),
        ("disc_total", "Total discs"), ("catalog", "Catalog"), ("songwriter", "Songwriter"),
    ];
    static readonly Dictionary<string, string> HeaderTips = new()
    {
        ["start"] = "Where the track starts (INDEX 01), as mm:ss:ff, with 75 frames a second",
        ["gap"] = "Where the gap before the track starts (INDEX 00); never a cut point",
        ["length"] = "Computed from the starts",
        ["flags"] = "The track's cue FLAGS (DCP, 4CH, PRE, SCMS)",
        ["name"] = "The file name the track gets, from the File names masks",
    };

    readonly Settings settings;
    readonly PathLocks locks;
    readonly DispatcherQueue ui = DispatcherQueue.GetForCurrentThread();
    readonly DispatcherQueueTimer planTimer;
    readonly Flac flac = new(Flac.Exe);
    readonly ConcurrentDictionary<(string, long, long), bool> silence = new();
    readonly ConcurrentDictionary<(string, DateTime, long), SourceInfo> sources = new();
    readonly TextBlock heading = Ui.Title("Edit cue sheet");
    readonly TextBlock where = Ui.Secondary();
    readonly ComboBox source = new() { MinWidth = 260 };
    readonly ComboBox encoding = new() { MinWidth = 220 };
    readonly TextBlock sourceNote = new() { TextWrapping = TextWrapping.Wrap };
    readonly Dictionary<string, TextBox> albumBoxes = [];
    readonly TextBox albumOther = Multiline("The album lines with no field of their own (REM DISCID, REM COMMENT, …).");
    readonly StackPanel table = new();
    readonly TextBox trackOther = Multiline("The selected track's other lines (INDEX 02 and later, PREGAP, POSTGAP, REM, …).");
    readonly TextBlock problemsText = new() { TextWrapping = TextWrapping.Wrap, Foreground = Ui.Brush("SystemFillColorCriticalBrush") };
    readonly TextBlock notesText = new() { TextWrapping = TextWrapping.Wrap };
    readonly TextBlock status = Ui.Secondary();
    readonly TextBlock saveNote = Ui.Secondary();
    readonly Button add = Ui.Button("Add track", "A new track after the selected one, starting halfway through it.");
    readonly Button delete = Ui.Button("Delete track", "Its audio joins the track before it (or the next one, for track 1).");
    readonly Button fill = Ui.Button("Fill from track lengths…", "Set the starts from each track's length, as a tracklist gives them.");
    readonly Button preview = Ui.Button("Preview in player", "Open the sheet as it is now, unsaved edits included, in the default player.");
    readonly Button save = Ui.Primary("Save");
    readonly Button saveAs = Ui.Button("Save as…", "Save the sheet as another .cue file beside its audio file; a file it replaces goes to the Recycle Bin first");
    readonly Button close = Ui.Button("Close", "Close this window");
    readonly List<(int? SheetRow, Grid Row, Dictionary<string, FrameworkElement> Cells)> rows = [];
    readonly string? audioPath;  // New cue sheet…: the audio file
    CueDocument? doc;
    string path;
    bool isNew;
    string? code;  // the picked encoding; null: Auto
    string? autoLabel;
    List<string> audio = [];
    AlbumPlan? plan;
    SourceInfo? info;
    List<CueProblem> problems = [];
    bool hidden;
    bool planning;
    int planToken;
    int? selected;  // the sheet row of the selected track
    bool refreshing;
    bool closing;

    /// <summary>A sheet was saved into this folder: the index re-reads it.</summary>
    public event Action<IReadOnlyList<string>>? Finished;

    /// <summary><paramref name="file"/>: a .cue to edit, or (with <paramref name="newSheet"/>) the audio file a new sheet
    /// is for.</summary>
    public CueEditorWindow(string file, bool newSheet, Settings settings, PathLocks locks)
    {
        this.settings = settings;
        this.locks = locks;
        isNew = newSheet;
        audioPath = newSheet ? file : null;
        path = newSheet ? Path.ChangeExtension(file, ".cue") : file;
        Ui.Prepare(this, newSheet ? "Refrain · New cue sheet" : "Refrain · Edit cue sheet");
        heading.Text = newSheet ? "New cue sheet" : "Edit cue sheet";
        where.IsTextSelectionEnabled = true;
        planTimer = ui.CreateTimer();
        planTimer.Interval = TimeSpan.FromMilliseconds(PlanDelayMs);
        planTimer.IsRepeating = false;
        planTimer.Tick += (_, _) => RequestPlan();

        var top = new StackPanel { Spacing = 12, Padding = new Thickness(24, 16, 24, 8) };
        var head = new StackPanel { Spacing = 2 };
        head.Children.Add(heading);
        head.Children.Add(where);
        top.Children.Add(head);
        top.Children.Add(SourceRow());
        top.Children.Add(sourceNote);
        top.Children.Add(AlbumGrid());
        var tableScroll = new ScrollViewer
        {
            Content = table, HorizontalScrollMode = ScrollMode.Auto, HorizontalScrollBarVisibility = ScrollBarVisibility.Auto,
            VerticalScrollMode = ScrollMode.Disabled, VerticalScrollBarVisibility = ScrollBarVisibility.Disabled,
        };
        top.Children.Add(new Border
        {
            Child = tableScroll, CornerRadius = new CornerRadius(6), BorderThickness = new Thickness(1), Padding = new Thickness(4),
            BorderBrush = Ui.Brush("CardStrokeColorDefaultBrush"), Background = Ui.Brush("LayerFillColorDefaultBrush"),
        });
        var trackButtons = new StackPanel { Orientation = Orientation.Horizontal, Spacing = 8 };
        trackButtons.Children.Add(add);
        trackButtons.Children.Add(delete);
        trackButtons.Children.Add(fill);
        top.Children.Add(trackButtons);
        top.Children.Add(Labelled("Other lines of the selected track:", trackOther));
        top.Children.Add(problemsText);
        top.Children.Add(notesText);
        var scroll = new ScrollViewer { Content = top };

        var bar = new Grid { ColumnSpacing = 8, Padding = new Thickness(24, 8, 24, 16) };
        bar.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });
        bar.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
        bar.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });
        bar.Children.Add(preview);
        var middle = new StackPanel { VerticalAlignment = VerticalAlignment.Center, Margin = new Thickness(8, 0, 8, 0) };
        middle.Children.Add(saveNote);
        middle.Children.Add(status);
        Grid.SetColumn(middle, 1);
        bar.Children.Add(middle);
        var right = new StackPanel { Orientation = Orientation.Horizontal, Spacing = 8 };
        right.Children.Add(save);
        right.Children.Add(saveAs);
        right.Children.Add(close);
        Grid.SetColumn(right, 2);
        bar.Children.Add(right);

        var root = new Grid();
        root.RowDefinitions.Add(new RowDefinition { Height = new GridLength(1, GridUnitType.Star) });
        root.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });
        root.Children.Add(scroll);
        Grid.SetRow(bar, 1);
        root.Children.Add(bar);
        Content = root;

        Wire();
        SetEnabled(false);
    }

    // -- layout --

    static TextBox Multiline(string tip)
    {
        var box = new TextBox { AcceptsReturn = true, TextWrapping = TextWrapping.NoWrap, MinHeight = 64, MaxHeight = 120,
            FontFamily = new FontFamily("Cascadia Mono, Consolas") };
        ScrollViewer.SetVerticalScrollBarVisibility(box, ScrollBarVisibility.Auto);
        ToolTipService.SetToolTip(box, tip);
        return box;
    }

    static StackPanel Labelled(string label, FrameworkElement control)
    {
        var panel = new StackPanel { Spacing = 4 };
        panel.Children.Add(new TextBlock { Text = label });
        panel.Children.Add(control);
        return panel;
    }

    FrameworkElement SourceRow()
    {
        var row = new StackPanel { Orientation = Orientation.Horizontal, Spacing = 8 };
        row.Children.Add(new TextBlock { Text = "Source:", VerticalAlignment = VerticalAlignment.Center });
        ToolTipService.SetToolTip(source, "The audio file this cue sheet points to (its FILE line): a FLAC, WAV or AIFF file in its folder.");
        row.Children.Add(source);
        row.Children.Add(new TextBlock { Text = "Encoding:", VerticalAlignment = VerticalAlignment.Center, Margin = new Thickness(16, 0, 0, 0) });
        ToolTipService.SetToolTip(encoding, "The cue sheet's text encoding: pick another if the titles look garbled.");
        row.Children.Add(encoding);
        return row;
    }

    FrameworkElement AlbumGrid()
    {
        var grid = new Grid { ColumnSpacing = 8, RowSpacing = 6 };
        for (var c = 0; c < 2; c++)
        {
            grid.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });
            grid.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
        }
        for (var i = 0; i < AlbumLabels.Length; i++)
        {
            if (i % 2 == 0) grid.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });
            var (key, label) = AlbumLabels[i];
            var text = new TextBlock { Text = label + ":", VerticalAlignment = VerticalAlignment.Center };
            Grid.SetRow(text, i / 2);
            Grid.SetColumn(text, i % 2 * 2);
            grid.Children.Add(text);
            var box = new TextBox();
            albumBoxes[key] = box;
            Grid.SetRow(box, i / 2);
            Grid.SetColumn(box, i % 2 * 2 + 1);
            grid.Children.Add(box);
            Commit(box, value => Edit(d => d.SetAlbum(key, value)), () => doc?.Album()[key] ?? "");
        }
        grid.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });
        var other = new TextBlock { Text = "Other lines:", VerticalAlignment = VerticalAlignment.Top };
        Grid.SetRow(other, 4);
        grid.Children.Add(other);
        Grid.SetRow(albumOther, 4);
        Grid.SetColumn(albumOther, 1);
        Grid.SetColumnSpan(albumOther, 3);
        grid.Children.Add(albumOther);
        Commit(albumOther, value => Edit(d => d.SetAlbumOther(value.Replace("\r", "\n"))), () => doc?.Album()["other"] ?? "", multiline: true);
        return grid;
    }

    /// <summary>A field edits the sheet when it loses the focus (or on Enter, for one line), if its text changed.</summary>
    void Commit(TextBox box, Action<string> apply, Func<string> current, bool multiline = false)
    {
        void Done()
        {
            if (refreshing || doc is null) return;
            var text = box.Text.Replace("\r\n", "\n").Replace('\r', '\n');
            if (text != current()) apply(text);
        }
        box.LostFocus += (_, _) => Done();
        if (!multiline)
            box.KeyDown += (_, e) =>
            {
                if (e.Key != Windows.System.VirtualKey.Enter) return;
                e.Handled = true;
                Done();
            };
    }

    void Wire()
    {
        source.SelectionChanged += (_, _) =>
        {
            if (refreshing || doc is null || source.SelectedItem is not ComboBoxItem { Tag: string name }) return;
            Edit(d => d.SetFile(name));
        };
        foreach (var (encodingCode, label) in CueDocument.Encodings)
            encoding.Items.Add(new ComboBoxItem { Content = label, Tag = encodingCode });
        encoding.SelectedIndex = 0;
        encoding.SelectionChanged += async (_, _) =>
        {
            if (refreshing || doc is null || encoding.SelectedItem is not ComboBoxItem item) return;
            await PickEncoding(item.Tag as string);
        };
        Commit(trackOther, value =>
        {
            if (selected is { } row) Edit(d => d.SetTrackOther(row, value.Replace("\r", "\n")));
        }, () => selected is { } row && doc is not null && row < doc.Tracks().Count ? doc.Tracks()[row].Other : "", multiline: true);
        add.Click += (_, _) => AddTrack();
        delete.Click += (_, _) =>
        {
            if (selected is { } row && doc is not null && row < doc.Tracks().Count) Edit(d => d.DeleteTracks([row]));
        };
        fill.Click += async (_, _) => await FillFromLengths();
        preview.Click += (_, _) => Preview();
        save.Click += async (_, _) => await Save();
        saveAs.Click += async (_, _) => await SaveAs();
        close.Click += (_, _) => Close();
        AppWindow.Closing += async (_, e) =>
        {
            if (closing || doc is null || !doc.Modified) return;
            e.Cancel = true;
            if (!await ConfirmLeave()) return;
            closing = true;
            Close();
        };
    }

    void SetEnabled(bool on)
    {
        foreach (var box in albumBoxes.Values) box.IsEnabled = on;
        albumOther.IsEnabled = trackOther.IsEnabled = source.IsEnabled = on;
        encoding.IsEnabled = on && !isNew;
        if (!on) add.IsEnabled = delete.IsEnabled = fill.IsEnabled = preview.IsEnabled = save.IsEnabled = saveAs.IsEnabled = false;
    }

    // -- opening --

    /// <summary>Shows the window, then reads the sheet (or makes the new one) off the UI thread.</summary>
    public void Start()
    {
        Ui.Show(this);
        where.Text = isNew ? $"{path} (new, not saved yet)" : path;
        status.Text = "Reading the cue sheet…";
        Load(null);
    }

    void Load(string? pickedCode)
    {
        var (file, newOne, audioFile) = (path, isNew, audioPath);
        Task.Run(() =>
        {
            if (!newOne) return (CueDocument.Read(file, pickedCode), CueEditing.AudioFiles(Path.GetDirectoryName(file)!));
            var (performer, title) = ("", "");
            try
            {
                var tags = TagFile.Read(audioFile!).Values;
                performer = tags[Field.AlbumArtist] is { Length: > 0 } artist ? artist : tags[Field.Artist];
                title = tags[Field.Album];
            }
            catch (Exception e)
            {
                Log.Info($"New cue sheet: no tags read from {audioFile}: {e.Message}");
            }
            return (CueDocument.New(Path.GetFileName(audioFile!), performer, title), CueEditing.AudioFiles(Path.GetDirectoryName(audioFile!)!));
        }).ContinueWith(task => ui.TryEnqueue(() =>
        {
            Ui.Fit(this);  // the sheet (or why it can't be read) sets the window's size, once laid out
            if (task.Exception is { } error)
            {
                status.Text = $"{Path.GetFileName(file)} can't be read: {error.InnerException?.Message ?? error.Message}";
                return;
            }
            (doc, audio) = task.Result;
            if (newOne) doc.Path = null;
            code = pickedCode;
            if (pickedCode is null) autoLabel = doc.EncodingName;
            status.Text = "";
            SetEnabled(true);
            Refresh();
        }), TaskScheduler.Default);
    }

    async Task PickEncoding(string? picked)
    {
        if (doc is null || picked == code) return;
        if (doc.Modified)
        {
            var dialog = new ContentDialog
            {
                Title = "Unsaved changes",
                Content = "Reading the cue sheet with another encoding loses the unsaved changes. Go on?",
                PrimaryButtonText = "Go on",
                CloseButtonText = "Cancel",
                DefaultButton = ContentDialogButton.Close,
                XamlRoot = Content.XamlRoot,
            };
            if (await dialog.ShowAsync() != ContentDialogResult.Primary)
            {
                ShowEncoding();
                return;
            }
        }
        SetEnabled(false);
        status.Text = "Reading the cue sheet…";
        Load(picked);
    }

    void ShowEncoding()
    {
        refreshing = true;
        for (var i = 0; i < encoding.Items.Count; i++)
        {
            var item = (ComboBoxItem)encoding.Items[i];
            if (item.Tag is null) item.Content = autoLabel is null ? "Auto" : $"Auto ({autoLabel})";
            if ((item.Tag as string) == code) encoding.SelectedIndex = i;
        }
        refreshing = false;
    }

    // -- edits and the plan --

    void Edit(Action<CueDocument> change)
    {
        if (doc is null) return;
        change(doc);
        Refresh();
    }

    void AddTrack()
    {
        if (doc is null) return;
        try
        {
            selected = doc.AddTrack(selected, info is null ? null : CueEditing.AudioFrames(info));
        }
        catch (CueException e)
        {
            status.Text = e.Message;
            return;
        }
        Refresh();
    }

    /// <summary>Validates now, shows it, and plans again after a pause.</summary>
    void Refresh()
    {
        if (doc is null) return;
        problems = doc.Validate(info);
        refreshing = true;
        try
        {
            var album = doc.Album();
            foreach (var (key, box) in albumBoxes) SetText(box, album[key]);
            SetText(albumOther, album["other"]);
            ShowSources();
            ShowEncoding();
            ShowTracks();
            ShowProblems();
        }
        finally
        {
            refreshing = false;
        }
        planning = true;
        planTimer.Stop();
        planTimer.Start();
        ShowButtons();
    }

    /// <summary>A field shows the sheet's value, unless the user is typing in it.</summary>
    static void SetText(TextBox box, string text)
    {
        if (box.FocusState != FocusState.Unfocused && box.Text.Replace("\r", "\n") == text.Replace("\r", "\n")) return;
        if (box.FocusState == FocusState.Unfocused || box.Text != text) box.Text = text.Replace("\n", "\r");
    }

    void ShowSources()
    {
        var current = doc!.File;
        source.Items.Clear();
        var names = audio.ToList();
        var mark = CueEditing.SourceMark(current, audio);
        if (current.Length > 0 && mark is not null) names.Insert(0, current);
        foreach (var name in names)
            source.Items.Add(new ComboBoxItem { Content = name == current && mark is not null ? $"{name} ({mark})" : name, Tag = name });
        source.SelectedIndex = names.FindIndex(n => LyricsKey.CaseFold(n) == LyricsKey.CaseFold(current));
    }

    void RequestPlan()
    {
        if (doc is null) return;
        CueSheet sheet;
        try
        {
            sheet = doc.Sheet();
        }
        catch (CueException)
        {
            planning = false;  // Validate marks the line; nothing to plan until it is fixed
            plan = null;
            ShowButtons();
            return;
        }
        var token = ++planToken;
        var names = settings.GetFileNames();
        var cue = path;
        Task.Run(() => SplitPlanner.Plan(sheet, cue, names.Masks, names.Replacement, isSilent: Silent, readInfo: Source))
            .ContinueWith(task => ui.TryEnqueue(() =>
            {
                if (token != planToken || doc is null) return;
                planning = planTimer.IsRunning;
                plan = task.Exception is null ? task.Result : new AlbumPlan(cue) { Sheet = sheet, Refusal = task.Exception.InnerException?.Message };
                audio = CueEditing.AudioFiles(Path.GetDirectoryName(cue)!);
                if (plan.Tracks.Count > 0) hidden = plan.Tracks[0].Row is null;
                if (plan.Info is not null && plan.Info != info)
                {
                    info = plan.Info;
                    problems = doc.Validate(info);  // a start past the end of the audio
                }
                refreshing = true;
                try
                {
                    ShowSources();
                    ShowTracks();
                    ShowProblems();
                }
                finally
                {
                    refreshing = false;
                }
                ShowButtons();
            }), TaskScheduler.Default);
    }

    bool Silent(string file, long start, long end) => silence.GetOrAdd((file, start, end), _ => Split.IsSilent(flac)(file, start, end));

    SourceInfo Source(string file)
    {
        var stat = new FileInfo(file);
        return sources.GetOrAdd((file, stat.LastWriteTimeUtc, stat.Length), _ => SplitPlanner.ReadSource(file));
    }

    // -- the table --

    /// <summary>The sheet's tracks, a hidden track's row 0 (when the last plan with tracks found one), the plan's
    /// lengths and names, and each bad cell marked with its reason. Rows are rebuilt only when their number changes,
    /// so a field being typed in keeps the focus.</summary>
    void ShowTracks()
    {
        var views = doc!.Tracks();
        var planned = plan?.Tracks.ToDictionary(t => t.Row ?? -1) ?? [];
        var wanted = new List<(int? SheetRow, int Number)>();
        if (hidden) wanted.Add((null, 0));
        wanted.AddRange(views.Select(v => ((int?)v.Row, v.Number ?? v.Row + 1)));
        if (rows.Count != wanted.Count || table.Children.Count == 0) RebuildTable(wanted);
        var rate = plan?.Info?.Rate ?? 0;
        string Frames(long samples) => rate > 0 ? CueDocument.FormatTime(samples * CueSheet.FramesPerSecond / rate) : "";
        for (var r = 0; r < wanted.Count; r++)
        {
            var (sheetRow, number) = wanted[r];
            var cells = rows[r].Cells;
            planned.TryGetValue(sheetRow ?? -1, out var track);
            var values = sheetRow is { } sr ? views[sr].Values : new Dictionary<string, string>();
            string Value(string key) => values.TryGetValue(key, out var v) ? v : "";
            var texts = new Dictionary<string, string>
            {
                ["number"] = number.ToString(),
                ["start"] = sheetRow is null ? track is null ? "" : Frames(track.Start) : Value("start"),
                ["gap"] = Value("gap"),
                ["length"] = track is not null ? Frames(track.Length) : "",
                ["title"] = sheetRow is null ? track?.Fields.GetValueOrDefault("title")?.ToString() ?? "" : Value("title"),
                ["performer"] = Value("performer"), ["songwriter"] = Value("songwriter"), ["isrc"] = Value("isrc"), ["flags"] = Value("flags"),
                ["name"] = track?.Name ?? "",
            };
            foreach (var (key, text) in texts)
            {
                var reasons = sheetRow is { } row ? problems.Where(p => p.Row == row && p.Column == key).Select(p => p.Message).ToList() : [];
                switch (cells[key])
                {
                    case TextBox box:
                        SetText(box, text);
                        Mark(box, reasons);
                        break;
                    case TextBlock block:
                        block.Text = text;
                        var note = key == "name" && track is not null ? plan!.Notes.FirstOrDefault(n => n.Track == track.Number).Why : null;
                        ToolTipService.SetToolTip(block, string.IsNullOrEmpty(note) ? (text.Length > 0 ? text : null) : note);
                        break;
                }
            }
            rows[r].Row.Background = sheetRow is not null && sheetRow == selected ? Ui.Brush("SubtleFillColorSecondaryBrush") : null;
        }
        SetText(trackOther, selected is { } chosen && chosen < views.Count ? views[chosen].Other : "");
        trackOther.IsEnabled = selected is { } s && s < views.Count;
    }

    void RebuildTable(List<(int? SheetRow, int Number)> wanted)
    {
        table.Children.Clear();
        rows.Clear();
        var header = NewRow();
        foreach (var (key, title, _) in Columns)
        {
            var text = new TextBlock { Text = title, FontWeight = Microsoft.UI.Text.FontWeights.SemiBold, Margin = new Thickness(4, 2, 4, 2) };
            if (HeaderTips.TryGetValue(key, out var tip)) ToolTipService.SetToolTip(text, tip);
            Place(header, text, key);
        }
        table.Children.Add(header);
        foreach (var (sheetRow, _) in wanted)
        {
            var grid = NewRow();
            var cells = new Dictionary<string, FrameworkElement>();
            foreach (var (key, _, _) in Columns)
            {
                FrameworkElement cell;
                if (Editable.Contains(key) && sheetRow is { } row)
                {
                    var box = new TextBox { MinWidth = 0, Padding = new Thickness(6, 3, 6, 3) };
                    var column = key;
                    Commit(box, value => Edit(d => d.SetTrack(row, column, value)),
                        () => doc is not null && row < doc.Tracks().Count ? doc.Tracks()[row].Values[column] : "");
                    box.GotFocus += (_, _) => Select(row);
                    cell = box;
                }
                else cell = new TextBlock { TextTrimming = TextTrimming.CharacterEllipsis, VerticalAlignment = VerticalAlignment.Center, Margin = new Thickness(6, 2, 6, 2) };
                Place(grid, cell, key);
                cells[key] = cell;
            }
            if (sheetRow is { } chosen) grid.Tapped += (_, _) => Select(chosen);
            table.Children.Add(grid);
            rows.Add((sheetRow, grid, cells));
        }
    }

    static Grid NewRow()
    {
        var grid = new Grid { ColumnSpacing = 2, Padding = new Thickness(0, 1, 0, 1) };
        foreach (var (_, _, width) in Columns) grid.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(width) });
        return grid;
    }

    static void Place(Grid grid, FrameworkElement cell, string key)
    {
        Grid.SetColumn(cell, Array.FindIndex(Columns, c => c.Key == key));
        grid.Children.Add(cell);
    }

    void Select(int row)
    {
        if (selected == row) return;
        selected = row;
        refreshing = true;
        try
        {
            ShowTracks();
        }
        finally
        {
            refreshing = false;
        }
        ShowButtons();
    }

    static void Mark(Control control, List<string> reasons)
    {
        control.BorderBrush = reasons.Count > 0 ? Ui.Brush("SystemFillColorCriticalBrush") : null;
        control.BorderThickness = reasons.Count > 0 ? new Thickness(2) : new Thickness(1);
        ToolTipService.SetToolTip(control, reasons.Count > 0 ? string.Join("\n", reasons) : null);
    }

    void ShowProblems()
    {
        foreach (var (key, box) in albumBoxes)
            Mark(box, problems.Where(p => p.Row is null && p.Column == key).Select(p => p.Message).ToList());
        var messages = problems.Select(p => p.Message).Distinct().ToList();
        if (plan is not null) messages.AddRange(plan.Problems.Select(p => p.Why));
        problemsText.Text = string.Join("\n", messages.Take(6)) + (messages.Count > 6 ? "\n…" : "");
        problemsText.Visibility = messages.Count > 0 ? Visibility.Visible : Visibility.Collapsed;
        var notes = plan is null ? [] : plan.Notes.Select(n => $"Track {n.Track}: {n.Why}").Concat(plan.Warnings).ToList();
        notesText.Text = string.Join("\n", notes);
        notesText.Visibility = notes.Count > 0 ? Visibility.Visible : Visibility.Collapsed;
        // A source the split can't use draws the eye: the control is marked like a bad cell, the reason in bold.
        var (text, bad) = CueEditing.SourceLine(doc!, audio, plan);
        sourceNote.Text = text;
        sourceNote.FontWeight = bad ? Microsoft.UI.Text.FontWeights.SemiBold : Microsoft.UI.Text.FontWeights.Normal;
        sourceNote.Foreground = bad ? Ui.Brush("SystemFillColorCriticalBrush") : Ui.Brush("TextFillColorSecondaryBrush");
        source.BorderBrush = bad ? Ui.Brush("SystemFillColorCriticalBrush") : null;
    }

    void ShowButtons()
    {
        if (doc is null) return;
        var sheetOk = !problems.Any(p => p.BlocksSave);
        var warning = CueEditing.SourceWarning(doc, audio, plan);
        // A new sheet can be saved once it could be split, as far as the sheet itself goes (Hugo, 2026-10-17).
        save.IsEnabled = sheetOk && (isNew ? warning is null : doc.Modified);
        saveAs.IsEnabled = sheetOk;
        preview.IsEnabled = sheetOk && plan is { Info: not null, Tracks.Count: > 0 } && !planning && warning is null;
        fill.IsEnabled = info is not null && doc.Tracks().Count > 0;
        add.IsEnabled = true;
        delete.IsEnabled = selected is { } row && row < doc.Tracks().Count;
        var beside = isNew && settings.SaveCueBeside ? $"Saves beside {Path.GetFileName(audioPath)} (the Split options)" : "";
        saveNote.Text = beside;
        saveNote.Visibility = beside.Length > 0 ? Visibility.Visible : Visibility.Collapsed;
        ToolTipService.SetToolTip(save, beside.Length > 0 ? beside
            : isNew ? "Save this new cue sheet beside its audio file, under the name you choose"
            : "Save the edits into this .cue; its old version goes to the Recycle Bin first");
    }

    // -- Fill from track lengths…, Preview in player --

    async Task FillFromLengths()
    {
        if (doc is null || info is null) return;
        if (CueEditing.FillStart(doc) is not var (first, lengths))
        {
            status.Text = "Track 1 needs a valid start first.";
            return;
        }
        var result = await TrackLengthsDialog.Ask(Content.XamlRoot, lengths, first, CueEditing.AudioFrames(info));
        if (result is null) return;
        Edit(d => d.FillFromLengths(result));
    }

    void Preview()
    {
        if (plan is not { Info: not null, Tracks.Count: > 0 } || planning) return;
        var text = CueEditing.PreviewText(plan);
        var name = Path.GetFileName(path);
        Task.Run(() => Playlists.WritePreview(text)).ContinueWith(task => ui.TryEnqueue(() =>
        {
            if (task.Exception is { } error)
            {
                Log.Error("Writing the preview sheet failed", error);
                status.Text = "The preview sheet couldn't be written; see the log.";
                return;
            }
            status.Text = Shell.TryOpen(task.Result)
                ? $"Previewing {name} in the player…"
                : "No app opens cue sheets: choose one in Settings > Apps > Default apps (search for .cue).";
        }), TaskScheduler.Default);
    }

    // -- saving --

    /// <summary>Save over the sheet's own file. A new sheet is saved as &lt;stem&gt;.cue beside its audio file: at once,
    /// numbered when the name is taken, with the Split options' box; else under the name asked for.</summary>
    async Task<bool> Save()
    {
        if (!isNew) return await SaveTo(path);
        if (settings.SaveCueBeside) return await SaveTo(CueDocument.NumberedPath(path));  // never replaces a file
        return await SaveAs();
    }

    /// <summary>Save as: a cue sheet must sit beside its audio file, so only its name is asked for; a taken name asks
    /// Replace (the old file goes to the Recycle Bin) or Keep both (the new one is numbered).</summary>
    async Task<bool> SaveAs()
    {
        if (doc is null || problems.Any(p => p.BlocksSave)) return false;
        var nameBox = new TextBox { Text = Path.GetFileNameWithoutExtension(path), Header = $"Save in {Path.GetDirectoryName(path)} as:" };
        var dialog = new ContentDialog
        {
            Title = "Save the cue sheet as",
            Content = nameBox,
            PrimaryButtonText = "Save",
            CloseButtonText = "Cancel",
            DefaultButton = ContentDialogButton.Primary,
            XamlRoot = Content.XamlRoot,
        };
        if (await dialog.ShowAsync() != ContentDialogResult.Primary) return false;
        var name = nameBox.Text.Trim();
        if (!name.EndsWith(".cue", StringComparison.OrdinalIgnoreCase)) name += ".cue";
        var stem = name[..^4];
        if (stem.Trim().Length == 0 || Core.Options.Masks.Forbidden().IsMatch(stem) || stem.EndsWith(' ') || stem.EndsWith('.'))
        {
            status.Text = $"{name} isn't a valid file name.";
            return false;
        }
        var target = Path.Combine(Path.GetDirectoryName(path)!, name);
        if (File.Exists(target))
        {
            var ask = new ContentDialog
            {
                Title = "The file exists",
                Content = $"{name} already exists. Replace it (it goes to the Recycle Bin), or keep both (the new one is numbered)?",
                PrimaryButtonText = "Replace",
                SecondaryButtonText = "Keep both",
                CloseButtonText = "Cancel",
                DefaultButton = ContentDialogButton.Close,
                XamlRoot = Content.XamlRoot,
            };
            switch (await ask.ShowAsync())
            {
                case ContentDialogResult.Secondary:
                    target = CueDocument.NumberedPath(target);
                    break;
                case ContentDialogResult.None:
                    return false;
            }
        }
        return await SaveTo(target);
    }

    async Task<bool> SaveTo(string target)
    {
        if (doc is null || problems.Any(p => p.BlocksSave)) return false;
        if (locks.Conflict(Path.GetDirectoryName(target)!) is { } owner)
        {
            status.Text = PathLocks.BusyText(Path.GetDirectoryName(target)!, owner);
            return false;
        }
        var overwrite = false;
        while (true)
        {
            try
            {
                doc.Save(target, overwriteForGood: overwrite);
                break;
            }
            catch (CueEncodingException e)
            {
                if (!await Confirm("Characters that don't fit",
                        $"{e.Encoding} can't hold {string.Join(" ", e.Characters)}. Save the cue sheet as UTF-8 (with a BOM) instead?", "Save as UTF-8"))
                    return false;
                doc.UseUtf8Bom();
                code = "utf-8";
            }
            catch (CannotRecycleException e)
            {
                if (overwrite || !await Confirm("No Recycle Bin",
                        $"Overwrite {Path.GetFileName(target)} for good? Its current version can't go to the Recycle Bin: {e.Message}.", "Overwrite"))
                    return false;
                overwrite = true;
            }
            catch (Exception e) when (e is IOException or UnauthorizedAccessException)
            {
                status.Text = $"{Path.GetFileName(target)} couldn't be saved: {e.Message}";
                return false;
            }
        }
        var moved = target != path || isNew;
        path = target;
        if (isNew)
        {
            isNew = false;
            autoLabel = doc.EncodingName;
            heading.Text = "Edit cue sheet";
            Title = "Refrain · Edit cue sheet";
        }
        where.Text = path;
        status.Text = $"Saved {Path.GetFileName(target)}.";
        Finished?.Invoke([Path.GetDirectoryName(target)!]);
        SetEnabled(true);
        Refresh();
        if (moved) RequestPlan();  // the window now works on the new file
        return true;
    }

    async Task<bool> Confirm(string title, string text, string yes)
    {
        var dialog = new ContentDialog
        {
            Title = title,
            Content = text,
            PrimaryButtonText = yes,
            CloseButtonText = "Cancel",
            DefaultButton = ContentDialogButton.Close,
            XamlRoot = Content.XamlRoot,
        };
        return await dialog.ShowAsync() == ContentDialogResult.Primary;
    }

    /// <summary>Unsaved edits: Save, Discard or Cancel. False when cancelled or the save didn't go through.</summary>
    async Task<bool> ConfirmLeave()
    {
        var dialog = new ContentDialog
        {
            Title = "Unsaved changes",
            Content = $"Save the changes to {Path.GetFileName(path)}?",
            PrimaryButtonText = "Save",
            SecondaryButtonText = "Discard",
            CloseButtonText = "Cancel",
            DefaultButton = ContentDialogButton.Primary,
            XamlRoot = Content.XamlRoot,
        };
        return await dialog.ShowAsync() switch
        {
            ContentDialogResult.Primary => await Save(),
            ContentDialogResult.Secondary => true,
            _ => false,
        };
    }
}
