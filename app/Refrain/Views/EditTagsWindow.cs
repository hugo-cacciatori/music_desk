using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Refrain.Core;
using RefrainMenu;

namespace Refrain.Views;

/// <summary>"Edit tags…" (SPEC §36 "11."): the old side panel's Metadata tab as a window of its own, on the music files
/// right-clicked: the tags of one or several files and the cover, saved only by Save. Closing with edits not saved asks
/// first (SPEC §7). Its Trim… and New cue sheet… open those entries' windows.</summary>
public sealed partial class EditTagsWindow : Window
{
    readonly MetadataPage metadata;
    readonly List<string> audio;
    readonly List<string> skipped;
    bool closing;

    /// <summary>Tags were saved in these folders: the index re-reads them.</summary>
    public event Action<IReadOnlyList<string>>? Finished;

    public EditTagsWindow(IReadOnlyList<string> paths)
    {
        audio = paths.Where(p => Formats.IsRead(Path.GetExtension(p))).ToList();
        skipped = paths.Where(p => !Formats.IsRead(Path.GetExtension(p))).Select(p => Path.GetFileName(p)!).ToList();
        Ui.Prepare(this, audio.Count == 1 ? $"Refrain · Edit tags · {Path.GetFileName(audio[0])}" : $"Refrain · Edit tags · {audio.Count} tracks");
        metadata = new MetadataPage(() => Content.XamlRoot, () => WinRT.Interop.WindowNative.GetWindowHandle(this));
        Content = new Grid { Padding = new Thickness(8), Children = { metadata } };

        metadata.Saved += saved => Finished?.Invoke(saved.Select(p => Path.GetDirectoryName(p)!).Distinct(StringComparer.OrdinalIgnoreCase).ToList());
        metadata.NewCueRequested += path => App.Instance.RunEntry(MenuLogic.NewCue, [path]);
        metadata.TrimRequested += path => App.Instance.RunEntry(MenuLogic.Trim, [path]);
        AppWindow.Closing += async (_, e) =>
        {
            if (closing || !metadata.Dirty) return;
            e.Cancel = true;  // edits not saved: ask first
            if (!await ConfirmLeave()) return;
            closing = true;
            Close();
        };
    }

    public async void Start()
    {
        Ui.Show(this);
        await metadata.Load(audio, skipped);
        Ui.Fit(this);
    }

    /// <summary>Save, Discard or Cancel (SPEC §7). False when the user cancels, or the save failed.</summary>
    async Task<bool> ConfirmLeave()
    {
        var dialog = new ContentDialog
        {
            Title = "Save your edits?",
            Content = "The tags have edits that aren't saved yet.",
            PrimaryButtonText = "Save",
            SecondaryButtonText = "Discard",
            CloseButtonText = "Cancel",
            DefaultButton = ContentDialogButton.Primary,
            XamlRoot = Content.XamlRoot,
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
}
