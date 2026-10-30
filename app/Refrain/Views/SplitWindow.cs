using Microsoft.UI.Dispatching;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Refrain.Core;
using Refrain.Core.Cue;

namespace Refrain.Views;

/// <summary>"Split this cue sheet…" (SPEC §31): a recap of what the split will do, with the app's defaults (every
/// track, the sheet's folder, the File names masks, the sheet's encoding as read), then Split or Cancel, nothing else.
/// The split runs in the same window (progress, Stop), then the report stays until the window closes. A sheet that
/// can't be split as it is says why, with Split greyed.</summary>
public sealed partial class SplitWindow : Window
{
    readonly IReadOnlyList<string> paths;
    readonly Settings settings;
    readonly PathLocks locks;
    readonly DispatcherQueue ui = DispatcherQueue.GetForCurrentThread();
    readonly CancellationTokenSource cancel = new();
    readonly Flac flac = new(Flac.Exe);
    readonly TextBlock heading = Ui.Title("Split");
    readonly TextBlock where = Ui.Secondary();
    readonly TextBlock message = new() { Text = "Reading the cue sheet…", TextWrapping = TextWrapping.Wrap };
    readonly TextBlock details = new() { TextWrapping = TextWrapping.Wrap };
    readonly StackPanel tracks = new() { Spacing = 2 };
    readonly ScrollViewer tracksScroll = new();
    readonly TextBox report = Ui.ReportBox();
    readonly ProgressBar busy = new() { IsIndeterminate = true, Width = 200, VerticalAlignment = VerticalAlignment.Center };
    readonly Button split = Ui.Primary("Split");
    readonly Button cancelButton = Ui.Button("Cancel", "Close without splitting anything");
    readonly Button stop = Ui.Button("Stop", "Stop the split; the tracks it was writing are deleted, the image isn't touched");
    readonly Button close = Ui.Button("Close", "Close this report");
    AlbumPlan? plan;
    List<string> reasons = [];
    bool running;
    bool closing;

    /// <summary>The folder written to, when the split ends: the index re-reads it.</summary>
    public event Action<IReadOnlyList<string>>? Finished;

    public SplitWindow(IReadOnlyList<string> paths, Settings settings, PathLocks locks)
    {
        this.paths = paths;
        this.settings = settings;
        this.locks = locks;
        Ui.Prepare(this, "Refrain · Split", 760, 520);
        ToolTipService.SetToolTip(split, "Write these tracks as FLAC files in the cue sheet's folder, each proven against the original; the image and its cue sheet aren't touched");
        where.IsTextSelectionEnabled = true;
        tracksScroll.Content = tracks;

        var head = new StackPanel { Spacing = 2 };
        head.Children.Add(heading);
        head.Children.Add(where);
        var buttons = new Grid { ColumnSpacing = 8 };
        buttons.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });
        buttons.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
        var right = new StackPanel { Orientation = Orientation.Horizontal, Spacing = 8 };
        foreach (var button in new[] { split, cancelButton, stop, close }) right.Children.Add(button);
        Grid.SetColumn(right, 2);
        buttons.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });
        buttons.Children.Add(busy);
        buttons.Children.Add(right);

        var root = new Grid { Padding = new Thickness(24, 16, 24, 16), RowSpacing = 10 };
        FrameworkElement[] parts = [head, message, details, tracksScroll, report, buttons];
        for (var i = 0; i < parts.Length; i++)
        {
            var stretch = parts[i] == tracksScroll || parts[i] == report;
            root.RowDefinitions.Add(new RowDefinition { Height = stretch ? new GridLength(1, GridUnitType.Star) : GridLength.Auto });
            Grid.SetRow(parts[i], i);
            root.Children.Add(parts[i]);
        }
        Content = root;

        split.Click += (_, _) => Run();
        cancelButton.Click += (_, _) => Close();
        stop.Click += (_, _) => Stop();
        close.Click += (_, _) => Close();
        AppWindow.Closing += async (_, e) =>
        {
            if (!running || closing) return;
            e.Cancel = true;
            var dialog = new ContentDialog
            {
                Title = "Splitting",
                Content = "Stop the split and close? The tracks it was writing are deleted; the album stays as it was.",
                PrimaryButtonText = "Stop and close",
                CloseButtonText = "Keep splitting",
                DefaultButton = ContentDialogButton.Close,
                XamlRoot = Content.XamlRoot,
            };
            if (await dialog.ShowAsync() != ContentDialogResult.Primary) return;
            closing = true;
            Stop();
        };
        Closed += (_, _) => cancel.Cancel();
        Show("loading");
    }

    /// <summary>Shows the window, then reads and plans the sheet; the recap follows.</summary>
    public void Start()
    {
        Ui.Show(this);
        if (paths.Count != 1 || !Path.GetExtension(paths[0]).Equals(".cue", StringComparison.OrdinalIgnoreCase))
        {
            Fail("Split didn't start: choose one cue sheet (.cue).");
            return;
        }
        var cue = paths[0];
        where.Text = cue;
        if (!File.Exists(Flac.Exe))
        {
            Fail($"flac isn't where Refrain expects it ({Flac.Exe}). Reinstall Refrain.");
            return;
        }
        var names = settings.GetFileNames();
        var masksValid = names.Validate().Count == 0;
        var silent = Split.IsSilent(flac, cancel.Token);
        Task.Run(() =>
        {
            var planned = SplitPlanner.Plan(cue, names.Masks, names.Replacement, silent);
            // The editor's own checks too: a value it marks keeps Split greyed (SPEC §31).
            var toFix = planned.Sheet is not null && CueDocument.Read(cue).Validate(planned.Info).Any(p => p.BlocksSave);
            return (planned, toFix);
        }).ContinueWith(task => ui.TryEnqueue(() =>
        {
            if (task.Exception is { } error)
            {
                Log.Error($"Planning the split of {cue} failed", error);
                Fail($"The cue sheet can't be planned: {error.InnerException?.Message ?? error.Message}. {SplitRecap.FixIt}");
                return;
            }
            var (planned, toFix) = task.Result;
            if (planned.Sheet is null)
            {
                Fail($"{planned.Refusal} {SplitRecap.FixIt}");
                return;
            }
            ShowRecap(planned, masksValid, toFix);
        }), TaskScheduler.Default);
    }

    static Grid Row(string track, string file, string length, bool header = false)
    {
        var grid = new Grid { ColumnSpacing = 16, Padding = new Thickness(4, 2, 4, 2) };
        grid.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(64) });
        grid.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
        grid.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(64) });
        var cells = new[] { track, file, length };
        for (var i = 0; i < cells.Length; i++)
        {
            var text = new TextBlock { Text = cells[i], TextTrimming = TextTrimming.CharacterEllipsis };
            if (header) text.FontWeight = Microsoft.UI.Text.FontWeights.SemiBold;
            Grid.SetColumn(text, i);
            grid.Children.Add(text);
        }
        return grid;
    }

    void ShowRecap(AlbumPlan planned, bool masksValid, bool valuesToFix)
    {
        plan = planned;
        reasons = SplitRecap.Reasons(planned, masksValid, ReplayGainWindow.OptionsWhere, valuesToFix);
        details.Text = string.Join("\n", SplitRecap.Details(planned));
        tracks.Children.Clear();
        tracks.Children.Add(Row("Track", "File", "Length", header: true));
        foreach (var (track, file, length) in SplitRecap.Rows(planned)) tracks.Children.Add(Row(track, file, length));
        message.Text = SplitRecap.Message(planned, reasons);
        Show("recap");
    }

    void Run()
    {
        if (plan is null || reasons.Count > 0) return;
        int token;
        try
        {
            token = locks.Acquire(SplitTask.LockPaths(plan), SplitTask.Owner);
        }
        catch (LockedException e)
        {
            message.Text = e.Message;
            return;
        }
        running = true;
        message.Text = "Splitting…";
        busy.IsIndeterminate = false;
        busy.Maximum = Math.Max(plan.Written.Count, 1);
        busy.Value = 0;
        Show("running");
        var current = plan;
        Task.Run(() => SplitTask.Run(current, flac, cancel.Token, (done, total, name) => ui.TryEnqueue(() =>
            {
                busy.Maximum = Math.Max(total, 1);
                busy.Value = done;
                if (name != "done") message.Text = $"Writing track {done + 1} of {total}: {name}";
            })))
            .ContinueWith(task =>
            {
                locks.Release(token);
                ui.TryEnqueue(() => Ended(task.Result));
            }, TaskScheduler.Default);
    }

    void Stop()
    {
        if (!running) return;
        stop.IsEnabled = false;
        cancel.Cancel();
    }

    void Ended(SplitOutcome outcome)
    {
        running = false;
        Finished?.Invoke([outcome.Plan.Folder]);
        if (closing)
        {
            Close();
            return;
        }
        var text = outcome.Text();
        message.Text = text.Split('\n')[0];
        report.Text = text.Replace("\n", "\r");
        Show("result");
    }

    void Fail(string text)
    {
        message.Text = text;
        Show("error");
    }

    void Show(string state)
    {
        details.Visibility = tracksScroll.Visibility = state is "recap" or "running" ? Visibility.Visible : Visibility.Collapsed;
        report.Visibility = state == "result" ? Visibility.Visible : Visibility.Collapsed;
        busy.Visibility = state is "loading" or "running" ? Visibility.Visible : Visibility.Collapsed;
        split.Visibility = state == "recap" ? Visibility.Visible : Visibility.Collapsed;
        split.IsEnabled = state == "recap" && reasons.Count == 0;
        cancelButton.Visibility = state is "loading" or "recap" ? Visibility.Visible : Visibility.Collapsed;
        stop.Visibility = state == "running" ? Visibility.Visible : Visibility.Collapsed;
        close.Visibility = state is "result" or "error" ? Visibility.Visible : Visibility.Collapsed;
    }
}
