using Microsoft.Data.Sqlite;
using Microsoft.UI.Text;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Refrain.Core;
using Refrain.Core.Index;
using Refrain.Core.Search;
using Refrain.Core.Tidy;
using RefrainMenu;

namespace Refrain.Views;

/// <summary>The preview (SPEC §37 "The preview"), right of the results in both search modes, full height: a single
/// click on a result shows it here while an excerpt plays (<see cref="ExcerptPlayer"/>). A song: its title, artist and
/// album, its rating and note, its tags, ReplayGain state and location, and its lyrics (the lyrics mode's panel, now part
/// of the preview). An album folder: the album, its ReplayGain state and songs, an excerpt of a random song. An artist
/// folder: its albums with their years and songs, an excerpt of a random song. Any other folder: its songs and artists.
/// The speaker button turns every preview's sound on or off (kept; also on Options' Player page).</summary>
public sealed partial class PreviewPanel : UserControl
{
    readonly Library library;
    readonly Settings settings;
    readonly Player player;
    readonly LyricsPage lyrics;
    readonly ExcerptPlayer excerpt;
    readonly Stars stars;
    readonly TextBlock title = new() { FontSize = 20, FontWeight = FontWeights.SemiBold, TextWrapping = TextWrapping.Wrap, MaxLines = 3, IsTextSelectionEnabled = true };
    readonly TextBlock subtitle = Ui.Secondary();
    readonly Button sound = new() { Padding = new Thickness(8, 4, 8, 4), VerticalAlignment = VerticalAlignment.Center };
    readonly TextBlock nowPlaying = new() { FontSize = 12, VerticalAlignment = VerticalAlignment.Center, TextTrimming = TextTrimming.CharacterEllipsis };
    readonly Grid details = new() { ColumnSpacing = 12, RowSpacing = 3, Margin = new Thickness(16, 0, 16, 8) };
    readonly StackPanel folderBody = new() { Spacing = 3, Padding = new Thickness(16, 0, 16, 16) };
    readonly ScrollViewer folderScroll = new() { HorizontalScrollMode = ScrollMode.Disabled };
    readonly Grid songBody = new();
    readonly TextBlock empty = Ui.Secondary();
    // Tidy in the preview (SPEC §37): a collapsible section at the very bottom, for folders.
    readonly Expander tidy = new() { HorizontalAlignment = HorizontalAlignment.Stretch, HorizontalContentAlignment = HorizontalAlignment.Stretch, Margin = new Thickness(8, 4, 8, 8) };
    readonly TextBlock tidyTitle = new() { Text = "Tidy", VerticalAlignment = VerticalAlignment.Center };
    readonly Microsoft.UI.Xaml.Shapes.Ellipse tidyDot = Dot();
    readonly StackPanel tidyBody = new() { Spacing = 6 };
    readonly StackPanel tidyFiles = new() { Spacing = 2 };
    readonly Button runTidy = Ui.Primary("Run Tidy");
    CancellationTokenSource? tidyCancel;
    string? tidyFolder;
    int generation;

    public PreviewPanel(Library library, Settings settings, Player player, LyricsPage lyrics, Ratings ratings)
    {
        this.library = library;
        this.settings = settings;
        this.player = player;
        this.lyrics = lyrics;
        excerpt = new ExcerptPlayer(settings, player);
        stars = new Stars(ratings);
        lyrics.ShowsTitle = false;

        var soundRow = new StackPanel { Orientation = Orientation.Horizontal, Spacing = 8 };
        soundRow.Children.Add(sound);
        soundRow.Children.Add(nowPlaying);
        var head = new StackPanel { Spacing = 6, Padding = new Thickness(16, 12, 16, 8) };
        head.Children.Add(title);
        head.Children.Add(subtitle);
        head.Children.Add(stars);
        head.Children.Add(soundRow);
        empty.Margin = new Thickness(16);

        songBody.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });
        songBody.RowDefinitions.Add(new RowDefinition { Height = new GridLength(1, GridUnitType.Star) });
        details.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });
        details.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
        songBody.Children.Add(details);
        Grid.SetRow(lyrics, 1);
        songBody.Children.Add(lyrics);
        folderScroll.Content = folderBody;

        var root = new Grid();
        root.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });
        root.RowDefinitions.Add(new RowDefinition { Height = new GridLength(1, GridUnitType.Star) });
        root.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });
        root.Children.Add(head);
        BuildTidy();
        Grid.SetRow(tidy, 2);
        root.Children.Add(tidy);
        foreach (var body in new UIElement[] { songBody, folderScroll, empty })
        {
            Grid.SetRow((FrameworkElement)body, 1);
            root.Children.Add(body);
        }
        Content = root;

        sound.Click += (_, _) =>
        {
            settings.PreviewSound = !settings.PreviewSound;
            ShowSound();
        };
        player.Changed += ShowSound;
        ShowNone("Click a result to preview it.");
    }

    /// <summary>For an agent's off-screen check: the excerpt makes no sound at all.</summary>
    public bool Muted
    {
        set => excerpt.Muted = value;
    }

    /// <summary>What the preview shows now, for an agent's check.</summary>
    public string Describe() =>
        $"{title.Text} | {subtitle.Text} | stars {(stars.Visibility == Visibility.Visible ? "shown" : "hidden")} | {nowPlaying.Text} | " +
        string.Join(" | ", (folderScroll.Visibility == Visibility.Visible ? folderBody.Children : details.Children).OfType<TextBlock>().Select(t => t.Text)) +
        (tidy.Visibility == Visibility.Visible
            ? $" || {tidyTitle.Text}{(tidyDot.Visibility == Visibility.Visible ? " (alert)" : "")}: " +
              string.Join(" | ", tidyBody.Children.OfType<TextBlock>().Select(t => t.Text)) + " | " +
              string.Join(" | ", tidyFiles.Children.OfType<Grid>().Take(6).Select(g => string.Join(": ", g.Children.OfType<TextBlock>().Select(t => t.Text))))
            : "");

    void ShowSound()
    {
        sound.Content = new FontIcon { Glyph = settings.PreviewSound ? "" : "", FontSize = 14 };
        ToolTipService.SetToolTip(sound, settings.PreviewSound
            ? player.Playing ? "Previews have sound, but stay silent while the player plays. Click to mute every preview"
                             : "Previews play with sound. Click to mute every preview"
            : "Previews are silent. Click to give every preview its sound");
        nowPlaying.Text = excerpt.Playing is not { } song ? ""
            : player.Playing && settings.PreviewSound ? $"{song} (silent while the player plays)"
            : song;
    }

    /// <summary>Nothing to preview (several results, none, a cleared search): the excerpt stops.</summary>
    public void ShowNone(string text)
    {
        generation++;
        excerpt.Stop();
        lyrics.ShowNone("");
        title.Text = "";
        subtitle.Text = "";
        stars.Load(null);
        empty.Text = text;
        Show(empty);
        ShowSound();
        sound.Visibility = Visibility.Collapsed;
        HideTidy();
    }

    /// <summary>The excerpt stops (the discography view opened over the preview).</summary>
    public void Stop()
    {
        excerpt.Stop();
        ShowSound();
    }

    void Show(UIElement body)
    {
        foreach (var each in new UIElement[] { songBody, folderScroll, empty }) each.Visibility = each == body ? Visibility.Visible : Visibility.Collapsed;
    }

    Task<T> Read<T>(Func<SqliteConnection, T> query) => Task.Run(() =>
    {
        using var conn = library.Db.Read();
        return query(conn);
    });

    public async Task ShowRow(FileRow row)
    {
        var mine = ++generation;
        if (!row.IsDir)
        {
            var info = await Read(conn => SongInfo.Read(conn, row.Id));
            if (mine != generation) return;
            if (info is null)
            {
                ShowNone($"{row.Name}'s tags haven't been read yet.");
                return;
            }
            string Field(string label) => info.Fields.FirstOrDefault(f => f.Label == label).Value ?? "";
            title.Text = Field("Title");
            subtitle.Text = string.Join(" · ", new[] { Field("Artist"), Field("Album"), Field("Date") is { Length: >= 4 } d ? d[..4] : "" }.Where(s => s.Length > 0));
            stars.Load(info.RatingKey);
            details.Children.Clear();
            details.RowDefinitions.Clear();
            foreach (var (label, value) in info.Fields.Where(f => f.Label is not ("Title" or "Artist" or "Album")))
                AddDetail(label, value);
            Show(songBody);
            HideTidy();
            _ = lyrics.Load(row.Id);
            if (info.Path is { } path) await PlayExcerpt(path, "Excerpt", mine);
            return;
        }
        var folder = await Read(conn => FolderPreview.Read(conn, row.Id));
        if (mine != generation) return;
        if (folder is null)
        {
            ShowNone($"{row.Name} has no songs indexed yet.");
            return;
        }
        stars.Load(null);
        lyrics.ShowNone("");
        title.Text = folder.Name;
        var count = folder.Songs.Count == 1 ? "1 song" : $"{folder.Songs.Count} songs";
        subtitle.Text = folder.Kind switch
        {
            PreviewKind.Album => string.Join(" · ", new[] { folder.Artist ?? "Various artists", folder.Year ?? "", count }.Where(s => s.Length > 0)),
            PreviewKind.Artist => $"{folder.Albums.Count} albums · {count}",
            _ => $"A folder · {count}",
        };
        folderBody.Children.Clear();
        void Line(string text, bool secondary = false, double top = 0) => folderBody.Children.Add(new TextBlock
        {
            Text = text, TextWrapping = TextWrapping.Wrap, FontSize = 13, Margin = new Thickness(0, top, 0, 0), IsTextSelectionEnabled = true,
            Foreground = secondary ? Ui.Brush("TextFillColorSecondaryBrush") : Ui.Brush("TextFillColorPrimaryBrush"),
        });
        if (folder.Kind != PreviewKind.Other)
            Line(folder.WithReplayGain == folder.Songs.Count ? "ReplayGain: every song has its tags"
                : folder.WithReplayGain == 0 ? "ReplayGain: no song has its tags"
                : $"ReplayGain: {folder.WithReplayGain} of {folder.Songs.Count} songs have their tags", secondary: true);
        if (folder.Path is { } location) Line(location, secondary: true);
        switch (folder.Kind)
        {
            case PreviewKind.Album:
                Line("Songs", secondary: true, top: 10);
                foreach (var song in folder.Songs)
                    Line($"{(song.Track is { } n ? $"{n}. " : "")}{song.Title}{(song.Seconds is { } s ? $"   {SongInfo.Length(s)}" : "")}");
                break;
            case PreviewKind.Artist:
                Line("Albums", secondary: true, top: 10);
                foreach (var album in folder.Albums)
                    Line($"{album.Name}{(album.Year is null ? "" : $" ({album.Year})")} · {(album.Songs == 1 ? "1 song" : $"{album.Songs} songs")}");
                break;
            default:
                Line(folder.Artists.Count == 1 ? "Artist" : $"{folder.Artists.Count} artists", secondary: true, top: 10);
                foreach (var artist in folder.Artists.Take(40)) Line(artist);
                if (folder.Artists.Count > 40) Line($"and {folder.Artists.Count - 40} more", secondary: true);
                break;
        }
        Show(folderScroll);
        folderScroll.ChangeView(null, 0, null, disableAnimation: true);
        if (folder.Path is { } tidyPath) PlanTidy(tidyPath);
        else HideTidy();
        // An album or an artist plays an excerpt of one of its songs, chosen at random; any other folder none (SPEC §37).
        if (folder.Kind == PreviewKind.Other)
        {
            excerpt.Stop();
            ShowSound();
            sound.Visibility = Visibility.Collapsed;
            return;
        }
        var pick = folder.Songs[Random.Shared.Next(folder.Songs.Count)];
        if (await Read(conn => Queries.PathOf(conn, pick.Id)) is { } pickPath) await PlayExcerpt(pickPath, $"Excerpt: {pick.Title}", mine);
    }

    void AddDetail(string label, string value)
    {
        var row = details.RowDefinitions.Count;
        details.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });
        var name = new TextBlock { Text = label, FontSize = 12, Foreground = Ui.Brush("TextFillColorSecondaryBrush") };
        var text = new TextBlock { Text = value, FontSize = 12, TextWrapping = TextWrapping.Wrap, IsTextSelectionEnabled = true };
        Grid.SetRow(name, row);
        Grid.SetRow(text, row);
        Grid.SetColumn(text, 1);
        details.Children.Add(name);
        details.Children.Add(text);
    }

    async Task PlayExcerpt(string path, string what, int mine)
    {
        if (mine != generation) return;
        sound.Visibility = Visibility.Visible;
        await excerpt.Play(path, what);
        ShowSound();
    }

    public void Dispose()
    {
        tidyCancel?.Cancel();
        excerpt.Dispose();
    }

    // -- Tidy --

    static Microsoft.UI.Xaml.Shapes.Ellipse Dot() => new()
    {
        Width = 8, Height = 8, Fill = Ui.Brush("SystemFillColorCriticalBrush"), VerticalAlignment = VerticalAlignment.Center,
        Visibility = Visibility.Collapsed,
    };

    void BuildTidy()
    {
        var header = new StackPanel { Orientation = Orientation.Horizontal, Spacing = 8 };
        header.Children.Add(tidyTitle);
        header.Children.Add(tidyDot);
        tidy.Header = header;
        var runRow = new StackPanel { Orientation = Orientation.Horizontal, Spacing = 8 };
        runRow.Children.Add(runTidy);
        var runDot = Dot();
        runDot.Visibility = Visibility.Visible;
        runRow.Children.Add(runDot);
        ToolTipService.SetToolTip(runTidy, "Opens Tidy's window on this folder, with its whole plan; nothing changes until you run it there");
        runTidy.Click += (_, _) =>
        {
            if (tidyFolder is { } folder) App.Instance.RunEntry(MenuLogic.Tidy, [folder]);
        };
        tidyBody.Tag = runRow;
        tidy.Content = new ScrollViewer { Content = tidyBody, MaxHeight = 320, HorizontalScrollMode = ScrollMode.Disabled };
        tidy.Visibility = Visibility.Collapsed;
    }

    void HideTidy()
    {
        tidyCancel?.Cancel();
        tidyFolder = null;
        tidy.Visibility = Visibility.Collapsed;
    }

    /// <summary>Tidy's own dry run on the folder, in the background (it reads the files: cue sheets, ReplayGain tags);
    /// a later preview cancels it. Nothing changes on disk.</summary>
    void PlanTidy(string folder)
    {
        tidyCancel?.Cancel();
        var cancel = tidyCancel = new CancellationTokenSource();
        tidyFolder = folder;
        tidy.Visibility = Visibility.Visible;
        tidy.IsExpanded = false;
        SetAlert(false);
        tidyTitle.Text = "Tidy · reading the folder…";
        tidyBody.Children.Clear();
        if (App.Instance.Locks.Conflict(folder) is { } owner)
        {
            tidyTitle.Text = "Tidy";
            Note(PathLocks.BusyText(folder, owner) + " Tidy looks again when it ends.");
            return;
        }
        var names = settings.GetFileNames();
        var replayGain = settings.GetReplayGain();
        var options = settings.GetTidy();
        var silent = File.Exists(Core.Cue.Flac.Exe) ? Core.Cue.Split.IsSilent(new Core.Cue.Flac(Core.Cue.Flac.Exe), cancel.Token) : null;
        var ui = DispatcherQueue;
        Task.Run(() => TidyPreview.From(Tidying.Plan(folder, names, replayGain, options, cancel.Token, isSilent: silent), folder), cancel.Token)
            .ContinueWith(task => ui.TryEnqueue(() =>
            {
                if (cancel.IsCancellationRequested || tidyFolder != folder) return;
                tidyTitle.Text = "Tidy";
                if (task.Exception is { } error)
                {
                    Log.Error($"Planning Tidy for the preview of {folder} failed", error);
                    Note($"Tidy can't read this folder: {error.InnerException?.Message ?? error.Message}");
                    return;
                }
                if (task.IsCompletedSuccessfully) ShowTidy(task.Result);
            }), TaskScheduler.Default);
    }

    void Note(string text) => tidyBody.Children.Add(new TextBlock { Text = text, TextWrapping = TextWrapping.Wrap, FontSize = 13, Foreground = Ui.Brush("TextFillColorSecondaryBrush") });

    /// <summary>Nothing to do: "Already tidy". Otherwise the alert (when on), what Tidy would do in words, the files with
    /// their planned action (those it changes highlighted), and Run Tidy.</summary>
    void ShowTidy(TidyPreview plan)
    {
        tidyBody.Children.Clear();
        SetAlert(plan.HasWork);
        if (!plan.HasWork)
        {
            Note("Already tidy");
            return;
        }
        foreach (var line in plan.Lines)
            tidyBody.Children.Add(new TextBlock { Text = line, TextWrapping = TextWrapping.Wrap, FontSize = 13 });
        tidyFiles.Children.Clear();
        foreach (var file in plan.Files)
        {
            var row = new Grid { ColumnSpacing = 10 };
            row.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
            row.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
            var brush = Ui.Brush(file.Changes ? "TextFillColorPrimaryBrush" : "TextFillColorTertiaryBrush");
            var name = new TextBlock { Text = file.Name, FontSize = 12, TextTrimming = TextTrimming.CharacterEllipsis, Foreground = brush };
            var action = new TextBlock { Text = file.Action, FontSize = 12, TextWrapping = TextWrapping.Wrap, Foreground = brush };
            if (file.Changes) name.FontWeight = action.FontWeight = FontWeights.SemiBold;
            ToolTipService.SetToolTip(name, file.Name);
            Grid.SetColumn(action, 1);
            row.Children.Add(name);
            row.Children.Add(action);
            tidyFiles.Children.Add(row);
        }
        if (plan.Files.Count >= TidyPreview.MaxFiles) tidyFiles.Children.Add(Ui.Secondary($"The first {TidyPreview.MaxFiles} files only; Tidy's window shows them all."));
        tidyBody.Children.Add(tidyFiles);
        if (tidyBody.Tag is UIElement runRow) tidyBody.Children.Add(runRow);
    }

    /// <summary>The collapsed section's alert (SPEC §37): its colour and a red dot, unless Options turned it off; the
    /// hover says so and names the setting.</summary>
    void SetAlert(bool work)
    {
        var alert = work && settings.PreviewTidyAlert;
        tidyDot.Visibility = alert ? Visibility.Visible : Visibility.Collapsed;
        tidyTitle.Foreground = Ui.Brush(alert ? "SystemFillColorCriticalBrush" : "TextFillColorPrimaryBrush");
        ToolTipService.SetToolTip(tidy, alert
            ? "Tidy has something to do in this folder. This alert can be turned off: … > Options… > Tidy, \"Alert in the search preview when Tidy has something to do\""
            : null);
    }

    /// <summary>A task ended on these folders: the section plans again if it shows one of them.</summary>
    public void TaskEnded(IReadOnlyList<string> folders)
    {
        if (tidyFolder is { } folder && folders.Any(f => RealPath.IsInside(folder, f) || RealPath.IsInside(f, folder) ||
                                                         string.Equals(f.TrimEnd('\\'), folder.TrimEnd('\\'), StringComparison.OrdinalIgnoreCase)))
            PlanTidy(folder);
    }
}
