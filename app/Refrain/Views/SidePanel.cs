using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Refrain.Core;
using Refrain.Core.Index;

namespace Refrain.Views;

/// <summary>The side panel on the right (SPEC §7, §33): the Metadata tab for audio files (one or several), the folder
/// summary for a folder (or the open folder when nothing is selected), and a preview for one text or image file.
/// The Lyrics tab comes with rebuild task 6.</summary>
public sealed partial class SidePanel : Grid
{
    readonly Library library;
    readonly Func<XamlRoot> xamlRoot;
    readonly TextBlock tab = new() { Text = "Metadata", FontWeight = Microsoft.UI.Text.FontWeights.SemiBold, Margin = new Thickness(16, 12, 16, 0) };
    readonly MetadataPage metadata;
    readonly FolderPage folder;
    readonly PreviewPage preview = new();
    readonly TextBlock message = Ui.Secondary();
    IReadOnlyList<long> shownIds = [];
    int generation;

    public SidePanel(Library library, Func<XamlRoot> xamlRoot, Func<nint> window)
    {
        this.library = library;
        this.xamlRoot = xamlRoot;
        metadata = new MetadataPage(xamlRoot, window);
        folder = new FolderPage(library);
        message.Margin = new Thickness(16);
        RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });
        RowDefinitions.Add(new RowDefinition { Height = new GridLength(1, GridUnitType.Star) });
        Children.Add(tab);
        foreach (var page in new FrameworkElement[] { metadata, folder, preview, message })
        {
            SetRow(page, 1);
            page.Visibility = Visibility.Collapsed;
            Children.Add(page);
        }
        metadata.DirtyChanged += dirty => tab.Text = dirty ? "Metadata •" : "Metadata";
        metadata.Saved += paths =>
        {
            // Only the saved files' folders are re-read into the index (SPEC §7 "As built (M3 task 2)").
            foreach (var path in paths.Select(Path.GetDirectoryName).Distinct(StringComparer.OrdinalIgnoreCase))
                if (path is not null) library.Indexer.ScanPath(path, recursive: false);
        };
    }

    public bool Dirty => metadata.Visibility == Visibility.Visible && metadata.Dirty;

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

    /// <summary>Shows what the selection calls for; with nothing selected, the open folder's summary.</summary>
    public async Task Show(IReadOnlyList<FileRow> rows, long? openFolder)
    {
        var mine = ++generation;
        shownIds = rows.Select(r => r.Id).ToList();
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
        for (var i = 0; i < rows.Count; i++)
        {
            if (paths[i] is not { } path) continue;
            if (rows[i].IsAudio && Formats.IsRead(rows[i].Ext)) audio.Add(path);
            else skipped.Add(rows[i].Name);
        }
        if (audio.Count > 0)
        {
            Show(metadata);
            await metadata.Load(audio, skipped);
            return;
        }
        if (rows.Count == 1 && paths[0] is { } file)
        {
            var row = rows[0];
            if (Preview.Of(file) != Preview.Kind.None)
            {
                Show(preview);
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

    async Task ShowFolder(long id)
    {
        Show(folder);
        await folder.Load(id);
    }

    void ShowMessage(string text)
    {
        message.Text = text;
        Show(message);
    }

    void Show(FrameworkElement page)
    {
        foreach (var child in new FrameworkElement[] { metadata, folder, preview, message })
            child.Visibility = child == page ? Visibility.Visible : Visibility.Collapsed;
        tab.Text = page == metadata && metadata.Dirty ? "Metadata •" : page == metadata ? "Metadata"
            : page == folder ? "Folder" : page == preview ? "Preview" : "";
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
        if (metadata.Visibility == Visibility.Visible && !metadata.Dirty && shownIds.SequenceEqual(selected.Select(r => r.Id)))
            await Show(selected, openFolder);
    }
}
