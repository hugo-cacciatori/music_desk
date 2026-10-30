using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Media;
using Refrain.Core;
using Refrain.Core.Index;
using Refrain.Core.Lyrics;

namespace Refrain.Views;

/// <summary>The side panel on the right (SPEC §7, §8, §33): for audio files, the Metadata and Lyrics tabs (plain text,
/// the selected one underlined in the accent colour); the folder summary for a folder (or the open folder when
/// nothing is selected); a preview for one text or image file.</summary>
public sealed partial class SidePanel : Grid
{
    readonly Library library;
    readonly Func<XamlRoot> xamlRoot;
    readonly Button metadataTab = Tab("Metadata");
    readonly Button lyricsTab = Tab("Lyrics");
    readonly StackPanel tabs = new() { Orientation = Orientation.Horizontal, Spacing = 4, Margin = new Thickness(12, 8, 12, 0) };
    readonly TextBlock heading = new() { FontWeight = Microsoft.UI.Text.FontWeights.SemiBold, Margin = new Thickness(16, 12, 16, 0) };
    readonly MetadataPage metadata;
    readonly LyricsPage lyrics;
    readonly FolderPage folder;
    readonly PreviewPage preview = new();
    readonly TextBlock message = Ui.Secondary();
    IReadOnlyList<long> shownIds = [];

    /// <summary>The folder summary's Play buttons: a folder, with or without its subfolders.</summary>
    public event Action<long, bool>? PlayRequested;
    IReadOnlyList<FileRow> audioRows = [];
    (List<string> Audio, List<string> Skipped)? audioPaths;
    bool metadataLoaded;
    bool onLyrics;
    int generation;

    public SidePanel(Library library, LyricsService service, Settings settings, Func<XamlRoot> xamlRoot, Func<nint> window)
    {
        this.library = library;
        this.xamlRoot = xamlRoot;
        metadata = new MetadataPage(xamlRoot, window);
        lyrics = new LyricsPage(library, service, settings);
        folder = new FolderPage(library);
        folder.PlayRequested += (id, subfolders) => PlayRequested?.Invoke(id, subfolders);
        preview.SplitRequested += path => _ = App.Instance.RunEntry(RefrainMenu.MenuLogic.Split, [path]);
        preview.EditRequested += path => _ = App.Instance.RunEntry(RefrainMenu.MenuLogic.EditCue, [path]);
        metadata.NewCueRequested += path => _ = App.Instance.RunEntry(RefrainMenu.MenuLogic.NewCue, [path]);
        message.Margin = new Thickness(16);
        RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });
        RowDefinitions.Add(new RowDefinition { Height = new GridLength(1, GridUnitType.Star) });
        tabs.Children.Add(metadataTab);
        tabs.Children.Add(lyricsTab);
        Children.Add(tabs);
        Children.Add(heading);
        foreach (var page in Pages)
        {
            SetRow(page, 1);
            page.Visibility = Visibility.Collapsed;
            Children.Add(page);
        }
        metadataTab.Click += async (_, _) => await ChooseTab(lyricsTab: false);
        lyricsTab.Click += async (_, _) => await ChooseTab(lyricsTab: true);
        metadata.DirtyChanged += _ => PaintTabs();
        metadata.Saved += paths =>
        {
            // Only the saved files' folders are re-read into the index (SPEC §7 "As built (M3 task 2)").
            foreach (var path in paths.Select(Path.GetDirectoryName).Distinct(StringComparer.OrdinalIgnoreCase))
                if (path is not null) library.Indexer.ScanPath(path, recursive: false);
        };
    }

    FrameworkElement[] Pages => [metadata, lyrics, folder, preview, message];

    static Button Tab(string text) => new()
    {
        Content = text,
        Background = new SolidColorBrush(Microsoft.UI.Colors.Transparent),
        BorderThickness = new Thickness(0, 0, 0, 2),
        Padding = new Thickness(8, 4, 8, 4),
        CornerRadius = new CornerRadius(0),
    };

    void PaintTabs()
    {
        metadataTab.Content = metadata.Dirty ? "Metadata •" : "Metadata";
        foreach (var (tab, selected) in new[] { (metadataTab, !onLyrics), (lyricsTab, onLyrics) })
        {
            tab.BorderBrush = selected ? Ui.Brush("AccentFillColorDefaultBrush") : new SolidColorBrush(Microsoft.UI.Colors.Transparent);
            tab.Foreground = Ui.Brush(selected ? "TextFillColorPrimaryBrush" : "TextFillColorSecondaryBrush");
        }
    }

    public bool Dirty => metadataLoaded && metadata.Dirty;

    /// <summary>The files whose edits aren't saved yet (none when nothing is edited).</summary>
    public IReadOnlyList<string> EditedPaths => Dirty && audioPaths is { } shown ? shown.Audio : [];

    /// <summary>Unsaved edits: Save, Discard or Cancel (SPEC §7). False when the user cancels, or the save failed.</summary>
    public async Task<bool> ConfirmLeave()
    {
        if (!Dirty) return true;
        var dialog = new ContentDialog
        {
            Title = "Save your edits?",
            Content = "The Metadata tab has edits that aren't saved yet.",
            PrimaryButtonText = "Save",
            SecondaryButtonText = "Discard",
            CloseButtonText = "Cancel",
            DefaultButton = ContentDialogButton.Primary,
            XamlRoot = xamlRoot(),
        };
        switch (await dialog.ShowAsync())
        {
            case ContentDialogResult.Primary:
                return await metadata.Save();
            case ContentDialogResult.Secondary:
                metadata.Revert();
                return true;
            default:
                return false;
        }
    }

    /// <summary>The Lyrics tab comes forward (a lyrics card was clicked, or the search opened).</summary>
    public Task ShowLyrics() => ChooseTab(lyricsTab: true);

    async Task ChooseTab(bool lyricsTab)
    {
        onLyrics = lyricsTab;
        PaintTabs();
        if (audioPaths is null) return;
        await ShowAudio();
    }

    /// <summary>Shows what the selection calls for; with nothing selected, the open folder's summary.</summary>
    public async Task Show(IReadOnlyList<FileRow> rows, long? openFolder)
    {
        var mine = ++generation;
        shownIds = rows.Select(r => r.Id).ToList();
        audioPaths = null;
        metadataLoaded = false;
        if (rows.Count == 0)
        {
            if (openFolder is { } id) await ShowFolder(id);
            else ShowMessage("Add a music folder to start.");
            return;
        }
        if (rows.Count == 1 && rows[0].IsDir)
        {
            await ShowFolder(rows[0].Id);
            return;
        }
        var ids = rows.Select(r => r.Id).ToList();
        var paths = await Task.Run(() =>
        {
            using var conn = library.Db.Read();
            return ids.Select(id => Queries.PathOf(conn, id)).ToList();
        });
        if (mine != generation) return;
        var audio = new List<string>();
        var skipped = new List<string>();
        var audioRowsFound = new List<FileRow>();
        for (var i = 0; i < rows.Count; i++)
        {
            if (paths[i] is not { } path) continue;
            if (rows[i].IsAudio && Formats.IsRead(rows[i].Ext))
            {
                audio.Add(path);
                audioRowsFound.Add(rows[i]);
            }
            else skipped.Add(rows[i].Name);
        }
        if (audio.Count > 0)
        {
            audioPaths = (audio, skipped);
            audioRows = audioRowsFound;
            await ShowAudio();
            return;
        }
        if (rows.Count == 1 && paths[0] is { } file)
        {
            var row = rows[0];
            if (Preview.Of(file) != Preview.Kind.None)
            {
                Show(preview, "Preview");
                await preview.Load(file, row.Size is { } size && row.Mtime is { } mtime ? (size, mtime) : null);
                return;
            }
            ShowMessage(row.IsAudio
                ? $"{row.Name}'s tags can't be read here: the tag library doesn't support this format."
                : $"{row.Name} isn't an audio file.");
            return;
        }
        ShowMessage($"{rows.Count} items selected, none of them an audio file with tags.");
    }

    /// <summary>Audio files: the chosen tab, its content loaded when first shown.</summary>
    async Task ShowAudio()
    {
        if (audioPaths is not { } chosen) return;
        if (onLyrics)
        {
            Show(lyrics, null);
            if (audioRows.Count == 1) await lyrics.Load(audioRows[0].Id);
            else lyrics.ShowNone("Select a single track to see its lyrics.");
            return;
        }
        Show(metadata, null);
        if (metadataLoaded) return;
        metadataLoaded = true;
        await metadata.Load(chosen.Audio, chosen.Skipped);
    }

    async Task ShowFolder(long id)
    {
        Show(folder, "Folder");
        await folder.Load(id);
    }

    void ShowMessage(string text)
    {
        message.Text = text;
        Show(message, "");
    }

    /// <summary>A page; the tabs show for audio files, a heading for the rest.</summary>
    void Show(FrameworkElement page, string? title)
    {
        foreach (var child in Pages) child.Visibility = child == page ? Visibility.Visible : Visibility.Collapsed;
        tabs.Visibility = title is null ? Visibility.Visible : Visibility.Collapsed;
        heading.Visibility = title is { Length: > 0 } ? Visibility.Visible : Visibility.Collapsed;
        heading.Text = title ?? "";
        PaintTabs();
    }

    /// <summary>LRCLIB answered for a key: the Lyrics tab showing it reloads.</summary>
    public async Task LyricsReady(string key)
    {
        if (lyrics.Visibility == Visibility.Visible && lyrics.ShownKey == key) await lyrics.Reload();
    }

    /// <summary>The service's state changed (a pause, Retry now): a shown Lyrics tab reloads.</summary>
    public async Task LyricsStateChanged()
    {
        if (lyrics.Visibility == Visibility.Visible) await lyrics.Reload();
    }

    /// <summary>The indexer changed folders: a shown summary or preview reloads, and so do clean Metadata fields.</summary>
    public async Task Refresh(IReadOnlyCollection<long> folders, IReadOnlyList<FileRow> selected, long? openFolder)
    {
        if (folder.Visibility == Visibility.Visible && folder.FolderId is { } id)
        {
            var chain = await Task.Run(() =>
            {
                using var conn = library.Db.Read();
                return Queries.Chain(conn, id).Select(c => c.Id).ToList();
            });
            if (chain.Any(folders.Contains)) await folder.Load(id);
            return;
        }
        if (preview.Visibility == Visibility.Visible && selected.Count == 1 && shownIds.SequenceEqual([selected[0].Id]))
        {
            var row = selected[0];
            if (row.Size is { } size && row.Mtime is { } mtime && preview.Stamp != (size, mtime))
                await Show(selected, openFolder);
            return;
        }
        if (lyrics.Visibility == Visibility.Visible)
        {
            await lyrics.Reload();
            return;
        }
        if (metadata.Visibility == Visibility.Visible && !metadata.Dirty && shownIds.SequenceEqual(selected.Select(r => r.Id)))
            await Show(selected, openFolder);
    }
}
