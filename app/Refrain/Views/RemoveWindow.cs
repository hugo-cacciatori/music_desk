using Microsoft.UI.Dispatching;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Refrain.Core;
using Refrain.Core.Options;
using Refrain.Core.ReplayGain;

namespace Refrain.Views;

/// <summary>"Remove ReplayGain tags…" (SPEC §10 B, §31): it asks first, with the number of tracks and where; then the
/// removal runs, one file at a time under a short lock, and the window becomes its report (tracks cleared, tracks that
/// had none, each file that couldn't be changed and why), staying until closed. The same window whether the entry
/// came from Explorer or from the app.</summary>
public sealed partial class RemoveWindow : Window
{
    readonly List<string> tracks;
    readonly List<string> leftOut;
    readonly ReplayGainOptions options;
    readonly PathLocks locks;
    readonly DispatcherQueue ui = DispatcherQueue.GetForCurrentThread();
    readonly TextBlock heading = Ui.Title("Remove ReplayGain tags");
    readonly TextBlock message = new() { TextWrapping = TextWrapping.Wrap };
    readonly TextBlock note = Ui.Secondary();
    readonly TextBox details = Ui.ReportBox();
    readonly Button remove = Ui.Primary("Remove");
    readonly Button reveal = Ui.Button("Open in File Explorer", "Show the tracks' folder in File Explorer");
    readonly Button close = Ui.Button("Cancel", "Close without changing anything");
    bool closed;

    /// <summary>A file's tags changed: the index re-reads its folder.</summary>
    public event Action<IReadOnlyList<string>>? Finished;

    public RemoveWindow(IReadOnlyList<string> paths, ReplayGainOptions options, PathLocks locks)
    {
        (tracks, leftOut) = RemoveTask.Split(paths);
        this.options = options;
        this.locks = locks;
        Ui.Prepare(this, "Refrain · Remove ReplayGain tags");

        var folders = tracks.Select(p => Path.GetDirectoryName(p)!).Distinct(StringComparer.OrdinalIgnoreCase).ToList();
        var where = folders.Count == 1 ? folders[0] : "several folders";
        message.Text = $"Remove the ReplayGain tags from {Report.Plural(tracks.Count, "track")} in {where}?";
        note.Text = $"This deletes their track and album gain and peak values, and the comment line " +
                    $"\"{options.Comment.Trim()}\" that ReplayGain writes. Other tags, other comment lines and the audio " +
                    "itself are not changed, and replaygain.log files stay.";

        var buttons = new StackPanel { Orientation = Orientation.Horizontal, Spacing = 8, HorizontalAlignment = HorizontalAlignment.Right };
        buttons.Children.Add(reveal);
        buttons.Children.Add(remove);
        buttons.Children.Add(close);
        ToolTipService.SetToolTip(remove, "Remove the ReplayGain tags from these tracks");

        var root = new Grid { Padding = new Thickness(24, 16, 24, 16), RowSpacing = 12 };
        for (var i = 0; i < 4; i++) root.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });
        root.RowDefinitions.Add(new RowDefinition { Height = new GridLength(1, GridUnitType.Star) });
        root.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });
        var parts = new FrameworkElement[] { heading, message, note, new Border(), details, buttons };
        for (var i = 0; i < parts.Length; i++)
        {
            Grid.SetRow(parts[i], i);
            root.Children.Add(parts[i]);
        }
        Content = root;

        details.Visibility = Visibility.Collapsed;
        reveal.Visibility = Visibility.Collapsed;
        remove.Click += (_, _) => Run();
        close.Click += (_, _) => Close();
        reveal.Click += (_, _) => Shell.Reveal(folders.FirstOrDefault() ?? Path.GetDirectoryName(paths[0])!, select: false);
        Closed += (_, _) => closed = true;  // a report closed before the end is dropped; the removal still finishes
        if (tracks.Count == 0) ShowOutcome(new RemoveOutcome(), [.. leftOut]);
    }

    public void Start()
    {
        Ui.Show(this);
        Ui.Fit(this);
    }

    void Run()
    {
        remove.IsEnabled = close.IsEnabled = false;
        message.Text = "Removing…";
        note.Text = "";
        Task.Run(() => RemoveTask.Run([.. tracks, .. leftOut], options, locks)).ContinueWith(task => ui.TryEnqueue(() =>
        {
            Finished?.Invoke(tracks.Select(p => Path.GetDirectoryName(p)!).Distinct(StringComparer.OrdinalIgnoreCase).ToList());
            if (closed) return;
            if (task.Exception is { } error)
            {
                Log.Error("Removing ReplayGain tags failed", error);
                ShowOutcome(new RemoveOutcome(), [], $"The removal failed: {error.InnerException?.Message ?? error.Message}");
                return;
            }
            ShowOutcome(task.Result, []);
        }), TaskScheduler.Default);
    }

    void ShowOutcome(RemoveOutcome outcome, List<string> extraLeftOut, string? failure = null)
    {
        outcome.LeftOut.AddRange(extraLeftOut);
        message.Text = failure ?? outcome.Message();
        note.Text = "";
        var text = outcome.Details();
        details.Text = text.Replace("\n", "\r");
        details.Visibility = text.Length > 0 ? Visibility.Visible : Visibility.Collapsed;
        remove.Visibility = Visibility.Collapsed;
        reveal.Visibility = tracks.Count > 0 ? Visibility.Visible : Visibility.Collapsed;
        close.Content = "Close";
        close.IsEnabled = true;
        ToolTipService.SetToolTip(close, "Close this window");
        if (text.Length > 0) AppWindow.Resize(new Windows.Graphics.SizeInt32(760, 520));
    }
}
