using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Refrain.Core;
using Refrain.Core.Browser;
using Refrain.Core.Index;
using Refrain.Core.Tags;

namespace Refrain.Views;

/// <summary>The folder summary (SPEC §7 "As built (M3 task 5)", §36 "5."): its subtree's tracks, duration, formats
/// and ReplayGain coverage, what it holds directly, and its cover. Play and the tools come with their tasks.</summary>
public sealed partial class FolderPage : UserControl
{
    const int CoverSize = 220;

    readonly Library library;
    readonly Image cover = new() { Width = CoverSize, Height = CoverSize, Stretch = Microsoft.UI.Xaml.Media.Stretch.Uniform };
    readonly TextBlock coverSource = Ui.Secondary();
    readonly TextBlock name = Ui.Title();
    readonly Grid details = Ui.DetailsGrid();
    int generation;

    public long? FolderId { get; private set; }

    /// <summary>Play folder (its own tracks) or Play with subfolders (SPEC §7 "As built (M3 task 5)").</summary>
    public event Action<long, bool>? PlayRequested;
    readonly Button play = Ui.Primary("Play folder");
    readonly Button playAll = Ui.Button("Play with subfolders", "Play this folder's tracks, then its subfolders'");

    public FolderPage(Library library)
    {
        this.library = library;
        var stack = new StackPanel { Spacing = 12, Padding = new Thickness(16) };
        stack.Children.Add(new Border
        {
            Child = cover,
            Width = CoverSize,
            Height = CoverSize,
            CornerRadius = new CornerRadius(4),
            Background = Ui.Brush("SubtleFillColorSecondaryBrush"),
            HorizontalAlignment = HorizontalAlignment.Left,
        });
        stack.Children.Add(coverSource);
        stack.Children.Add(name);
        var buttons = new StackPanel { Orientation = Orientation.Horizontal, Spacing = 8 };
        buttons.Children.Add(play);
        buttons.Children.Add(playAll);
        ToolTipService.SetToolTip(play, "Play this folder's tracks in your default player");
        play.Click += (_, _) =>
        {
            if (FolderId is { } id) PlayRequested?.Invoke(id, false);
        };
        playAll.Click += (_, _) =>
        {
            if (FolderId is { } id) PlayRequested?.Invoke(id, true);
        };
        stack.Children.Add(buttons);
        stack.Children.Add(details);
        Content = new ScrollViewer { Content = stack, HorizontalScrollMode = ScrollMode.Disabled };
    }

    /// <summary>Reads the summary and the cover off the UI thread.</summary>
    public async Task Load(long folderId)
    {
        var mine = ++generation;
        FolderId = folderId;
        var (summary, source) = await Task.Run(() =>
        {
            using var conn = library.Db.Read();
            return (FolderSummary.Read(conn, folderId), FolderSummary.CoverSource(conn, folderId));
        });
        if (mine != generation || summary is null) return;
        name.Text = summary.Name;
        play.IsEnabled = summary.OwnTracks > 0;  // enabled when the folder has tracks of its own
        playAll.IsEnabled = summary.Tracks > 0;
        details.Children.Clear();
        details.RowDefinitions.Clear();
        Ui.Detail(details, "Tracks", summary.Tracks == 0 ? "None" : $"{summary.Tracks:N0} · {Columns.Duration(summary.Duration)}");
        Ui.Detail(details, "Formats", string.Join(", ", summary.Formats));
        if (summary.Tracks > 0)
            Ui.Detail(details, "ReplayGain", summary.WithReplayGain == summary.Tracks
                ? "Every track"
                : $"{summary.WithReplayGain:N0} of {summary.Tracks:N0} tracks");
        Ui.Detail(details, "In this folder", $"{Plural(summary.Subfolders, "subfolder")}, {Plural(summary.Files, "file")}");
        Ui.Detail(details, "Path", summary.Path);

        var data = await Task.Run(() =>
        {
            try
            {
                return source switch
                {
                    null => null,
                    { Embedded: true } => TagFile.Read(source.Value.Path).Cover?.Data,
                    _ => File.ReadAllBytes(source.Value.Path),
                };
            }
            catch (Exception)
            {
                return null;
            }
        });
        var bitmap = data is null ? null : await Ui.Image(data, CoverSize * 2);
        if (mine != generation) return;
        cover.Source = bitmap;
        coverSource.Text = source is null ? "No cover"
            : source.Value.Embedded ? $"Embedded in {Path.GetFileName(source.Value.Path)}"
            : Path.GetFileName(source.Value.Path);
    }

    static string Plural(long n, string word) => n == 1 ? $"1 {word}" : $"{n:N0} {word}s";
}
