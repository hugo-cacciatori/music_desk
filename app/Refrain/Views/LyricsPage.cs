using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Controls.Primitives;
using Microsoft.UI.Xaml.Input;
using Refrain.Core;
using Refrain.Core.Index;
using Refrain.Core.Lyrics;
using Windows.ApplicationModel.DataTransfer;
using Windows.System;

namespace Refrain.Views;

/// <summary>The Lyrics tab (SPEC §8, §33 "The lyrics pane", §36 "6."): LRCLIB's result for the selected track, big
/// and readable, with A− / A+ (kept between sessions) and Show timestamps; or why there are none, with Genius,
/// Google and Retry now. Everything comes from the index: no file is read.</summary>
public sealed partial class LyricsPage : UserControl
{
    /// <summary>The text sizes, in points (SPEC §8 "Direction task 7").</summary>
    static readonly double[] Sizes = [9, 10, 11, 12, 13, 14, 16, 18, 20, 22, 24, 28, 32, 36];
    const double DefaultSize = 14;

    readonly Library library;
    readonly LyricsService service;
    readonly Settings settings;
    readonly TextBlock title = Ui.Title();
    readonly TextBlock message = Ui.Secondary();
    readonly ProgressRing ring = new() { IsActive = false, Width = 20, Height = 20, HorizontalAlignment = HorizontalAlignment.Left };
    readonly TextBlock lyrics = new() { TextWrapping = TextWrapping.Wrap, IsTextSelectionEnabled = true, Margin = new Thickness(0, 4, 0, 12) };
    readonly ScrollViewer lyricsScroll;
    readonly TextBlock source = Ui.Secondary();
    readonly ToggleButton timestamps = new() { Content = "Show timestamps" };
    readonly Button smaller = Ui.Button("A−", "Smaller text (Ctrl+−)");
    readonly Button larger = Ui.Button("A+", "Larger text (Ctrl+=)");
    readonly Button copy = Ui.Button("Copy", "Copy the lyrics");
    readonly Button genius = Ui.Button("Search on Genius", "Open a Genius search in your browser");
    readonly Button google = Ui.Button("Search on Google", "Open a Google search in your browser");
    readonly Button retry = Ui.Button("Retry now", "Look this track up on LRCLIB again now");
    readonly StackPanel textTools;
    readonly StackPanel links;
    LyricsInfo? info;
    long? entryId;
    int generation;

    public LyricsPage(Library library, LyricsService service, Settings settings)
    {
        this.library = library;
        this.service = service;
        this.settings = settings;
        textTools = new StackPanel { Orientation = Orientation.Horizontal, Spacing = 8 };
        textTools.Children.Add(timestamps);
        textTools.Children.Add(smaller);
        textTools.Children.Add(larger);
        textTools.Children.Add(copy);
        links = new StackPanel { Orientation = Orientation.Horizontal, Spacing = 8 };
        links.Children.Add(genius);
        links.Children.Add(google);
        links.Children.Add(retry);
        var head = new StackPanel { Spacing = 8, Padding = new Thickness(16, 12, 16, 8) };
        head.Children.Add(title);
        head.Children.Add(ring);
        head.Children.Add(message);
        head.Children.Add(source);
        head.Children.Add(textTools);
        head.Children.Add(links);
        lyricsScroll = new ScrollViewer { Content = lyrics, Padding = new Thickness(16, 0, 16, 0), HorizontalScrollMode = ScrollMode.Disabled };
        var grid = new Grid();
        grid.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });
        grid.RowDefinitions.Add(new RowDefinition { Height = new GridLength(1, GridUnitType.Star) });
        grid.Children.Add(head);
        Grid.SetRow(lyricsScroll, 1);
        grid.Children.Add(lyricsScroll);
        Content = grid;

        ApplySize(Nearest(settings.LyricsTextSize));
        timestamps.Click += (_, _) => ShowText();
        smaller.Click += (_, _) => Step(-1);
        larger.Click += (_, _) => Step(1);
        copy.Click += (_, _) =>
        {
            var package = new DataPackage();
            package.SetText(lyrics.Text);
            Clipboard.SetContent(package);
        };
        genius.Click += (_, _) => Shell.Open(Urls().Genius);
        google.Click += (_, _) => Shell.Open(Urls().Google);
        retry.Click += (_, _) =>
        {
            if (entryId is { } id) service.Retry(id);
            _ = Reload();
        };
        Key(VirtualKey.Add, () => Step(1));
        Key((VirtualKey)187, () => Step(1));  // = and +
        Key(VirtualKey.Subtract, () => Step(-1));
        Key((VirtualKey)189, () => Step(-1));  // −
        Key(VirtualKey.Number0, () => ApplySize(DefaultSize));
        lyricsScroll.PointerWheelChanged += (_, e) =>
        {
            if (!e.KeyModifiers.HasFlag(VirtualKeyModifiers.Control)) return;
            Step(e.GetCurrentPoint(lyricsScroll).Properties.MouseWheelDelta > 0 ? 1 : -1);
            e.Handled = true;
        };
    }

    void Key(VirtualKey key, Action action)
    {
        var accelerator = new KeyboardAccelerator { Key = key, Modifiers = VirtualKeyModifiers.Control, ScopeOwner = this };
        accelerator.Invoked += (_, e) =>
        {
            e.Handled = true;
            action();
        };
        KeyboardAccelerators.Add(accelerator);
        KeyboardAcceleratorPlacementMode = KeyboardAcceleratorPlacementMode.Hidden;
    }

    static double Nearest(double size) => Sizes.MinBy(s => Math.Abs(s - size));

    void Step(int direction)
    {
        var at = Array.IndexOf(Sizes, Nearest(lyrics.FontSize)) + direction;
        if (at >= 0 && at < Sizes.Length) ApplySize(Sizes[at]);
    }

    void ApplySize(double points)
    {
        lyrics.FontSize = points * 96 / 72;  // points to the view's pixels
        lyrics.LineHeight = lyrics.FontSize * 1.4;
        settings.LyricsTextSize = points;
        smaller.IsEnabled = points > Sizes[0];
        larger.IsEnabled = points < Sizes[^1];
    }

    (string Genius, string Google) Urls() =>
        info?.Track is { } t ? Lrclib.SearchUrls(t.Artist, t.Title)
        : info is not null ? Lrclib.SearchUrls(null, Path.GetFileNameWithoutExtension(info.Name))
        : ("", "");

    public long? EntryId => entryId;

    /// <summary>The track's title line; off inside the preview, whose own title says it (SPEC §37).</summary>
    public bool ShowsTitle
    {
        set => title.Visibility = value ? Visibility.Visible : Visibility.Collapsed;
    }
    public string? ShownKey => info?.Key;

    /// <summary>Shows one track's lyrics state, and puts it first in the lookup queue when it still needs one.</summary>
    public async Task Load(long id)
    {
        if (entryId != id) timestamps.IsChecked = false;  // hidden again for each track
        entryId = id;
        service.Prioritize(id);
        await Reload();
    }

    /// <summary>Several tracks, or none: nothing to show here.</summary>
    public void ShowNone(string text)
    {
        generation++;
        entryId = null;
        info = null;
        title.Text = "";
        Render(text, busy: false, found: false, links: false, canRetry: false);
    }

    public async Task Reload()
    {
        if (entryId is not { } id) return;
        var mine = ++generation;
        var read = await Task.Run(() =>
        {
            using var conn = library.Db.Read();
            return LyricsIndex.Info(conn, id);
        });
        if (mine != generation) return;
        info = read;
        if (read is null)
        {
            ShowNone("This file isn't an indexed track yet.");
            return;
        }
        title.Text = read.Track is { } t ? $"{t.Title} — {t.Artist}" : read.Name;
        var entry = read.Entry;
        var today = DateOnly.FromDateTime(DateTime.Now);
        if (read.ReadError is not null)
            Render($"The tags can't be read, so LRCLIB can't be asked: {read.ReadError}", false, false, false, false);
        else if (read.Track is null)
            Render("No artist or title in the tags or the file name, so LRCLIB can't be asked.", false, false, true, false);
        else if (entry?.Status == LyricsEntry.Found)
        {
            source.Text = $"LRCLIB #{entry.LrclibId} · checked {entry.Checked}";
            Render("", false, true, false, false);
        }
        else if (entry?.Status == LyricsEntry.Instrumental)
        {
            source.Text = $"LRCLIB #{entry.LrclibId} · checked {entry.Checked}";
            Render("LRCLIB lists this song as an instrumental.", false, false, true, false);
        }
        else if (service.LastError(read.Key) is { } error)
            Render($"The lookup failed: {error}", false, false, true, true);
        else if (service.PausedUntil is { } until)
            Render($"LRCLIB is unreachable; lookups resume at {until:HH:mm}.", false, false, true, true);
        else if (entry?.Status == LyricsEntry.NotFound && Lrclib.IsFresh(entry, today))
            Render($"LRCLIB has no lyrics for this song (checked {entry.Checked}).", false, false, true, true);
        else if (service.Offline)
            Render("Lyrics lookups are off.", false, false, true, false);
        else
            Render("Looking up on LRCLIB…", true, false, false, false);
    }

    void Render(string text, bool busy, bool found, bool links, bool canRetry)
    {
        message.Text = text;
        message.Visibility = text.Length > 0 ? Visibility.Visible : Visibility.Collapsed;
        ring.IsActive = busy;
        ring.Visibility = busy ? Visibility.Visible : Visibility.Collapsed;
        source.Visibility = found || info?.Entry?.Status == LyricsEntry.Instrumental && !busy ? Visibility.Visible : Visibility.Collapsed;
        textTools.Visibility = found ? Visibility.Visible : Visibility.Collapsed;
        lyricsScroll.Visibility = found ? Visibility.Visible : Visibility.Collapsed;
        this.links.Visibility = links ? Visibility.Visible : Visibility.Collapsed;
        retry.Visibility = canRetry ? Visibility.Visible : Visibility.Collapsed;
        if (found) ShowText();
    }

    void ShowText()
    {
        var entry = info?.Entry;
        timestamps.IsEnabled = !string.IsNullOrEmpty(entry?.Synced);
        lyrics.Text = timestamps.IsChecked == true && !string.IsNullOrEmpty(entry?.Synced)
            ? entry.Synced.Replace("\r\n", "\n")
            : entry?.Plain?.Replace("\r\n", "\n") ?? "";
    }
}
