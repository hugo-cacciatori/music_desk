using System.Diagnostics;
using Microsoft.UI.Dispatching;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Refrain.Core;
using Refrain.Core.Options;
using Refrain.Core.ReplayGain;

namespace Refrain.Views;

/// <summary>A ReplayGain entry's own window (SPEC §31, §35): it starts at once (no preview, as the tool), shows
/// per-album progress and rsgain's live lines, then the report, which stays until the window closes. Its folders are
/// locked for the run; files another task holds refuse it, in the window. Closing it while it runs asks first.</summary>
public sealed partial class ReplayGainWindow : Window
{
    /// <summary>Where every refusal points for the options (they may come from Explorer or from the app).</summary>
    public const string OptionsWhere = "… > Options… in Refrain, or Refrain > Settings in the right-click menu";

    readonly ReplayGainKind kind;
    readonly IReadOnlyList<string> paths;
    readonly ReplayGainOptions options;
    readonly PathLocks locks;
    readonly DispatcherQueue ui = DispatcherQueue.GetForCurrentThread();
    readonly CancellationTokenSource cancel = new();
    readonly ProgressBar busy = new() { IsIndeterminate = true, Width = 160, VerticalAlignment = VerticalAlignment.Center };
    readonly TextBlock message = new() { TextWrapping = TextWrapping.Wrap, VerticalAlignment = VerticalAlignment.Center };
    readonly TextBox report = Ui.ReportBox(minWidth: 720, minHeight: 280);  // the live lines fill in after the fit
    readonly Button openLog = Ui.Button("Open replaygain.log", "Open the replaygain.log this run wrote, in its default app");
    readonly Button reveal = Ui.Button("Open in File Explorer", "Show the folder in File Explorer");
    readonly Button stop = Ui.Button("Stop", "Stop ReplayGain: rsgain is stopped and the album being measured isn't leveled");
    readonly Button done = Ui.Primary("Done");
    readonly DispatcherQueueTimer clock;
    readonly Stopwatch albumClock = Stopwatch.StartNew();
    readonly List<string> pending = [];
    string album = "Starting";
    string unit;
    bool running;
    bool closing;
    ReplayGainOutcome? outcome;

    /// <summary>The folders the run may have changed, when it ends: the index re-reads them.</summary>
    public event Action<IReadOnlyList<string>>? Finished;

    public ReplayGainWindow(string label, ReplayGainKind kind, IReadOnlyList<string> paths, ReplayGainOptions options,
        PathLocks locks)
    {
        this.kind = kind;
        this.paths = paths;
        this.options = options;
        this.locks = locks;
        unit = kind == ReplayGainKind.Loose ? "Track" : "Album";
        Ui.Prepare(this, "Refrain · ReplayGain");

        var where = kind is ReplayGainKind.Tracks or ReplayGainKind.Loose
            ? $"{Report.Plural(paths.Count, "file")} in {Path.GetDirectoryName(paths[0])}"
            : paths.Count == 1 ? paths[0] : $"{paths.Count} folders in {Path.GetDirectoryName(paths[0])}";
        var heading = new StackPanel { Spacing = 2 };
        heading.Children.Add(Ui.Title(label));
        var place = Ui.Secondary(where);
        place.IsTextSelectionEnabled = true;
        heading.Children.Add(place);

        var status = new StackPanel { Orientation = Orientation.Horizontal, Spacing = 12 };
        status.Children.Add(busy);
        status.Children.Add(message);

        var buttons = new Grid { ColumnSpacing = 8 };
        buttons.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });
        buttons.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });
        buttons.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
        buttons.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });
        buttons.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });
        foreach (var (button, column) in new[] { (openLog, 0), (reveal, 1), (stop, 3), (done, 4) })
        {
            Grid.SetColumn(button, column);
            buttons.Children.Add(button);
        }
        ToolTipService.SetToolTip(done, "Close this window");

        var root = new Grid { Padding = new Thickness(24, 16, 24, 16), RowSpacing = 12 };
        root.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });
        root.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });
        root.RowDefinitions.Add(new RowDefinition { Height = new GridLength(1, GridUnitType.Star) });
        root.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });
        foreach (var (element, row) in new FrameworkElement[] { heading, status, report, buttons }.Select((e, i) => (e, i)))
        {
            Grid.SetRow(element, row);
            root.Children.Add(element);
        }
        Content = root;

        openLog.Click += (_, _) =>
        {
            if (outcome?.LogPath is { } log) Shell.Open(log);
        };
        reveal.Click += (_, _) => Shell.Reveal(outcome?.Folder ?? Folder, select: false);
        stop.Click += (_, _) => Stop();
        done.Click += (_, _) => Close();
        clock = ui.CreateTimer();
        clock.Interval = TimeSpan.FromSeconds(1);
        clock.Tick += (_, _) => Tick();
        AppWindow.Closing += async (_, e) =>
        {
            if (!running || closing) return;
            e.Cancel = true;
            if (!await ConfirmStop()) return;
            closing = true;
            Stop();  // the window closes once rsgain has stopped and the locks are released
        };
        Closed += (_, _) => cancel.Cancel();
        Show(state: "idle", "");
    }

    string Folder => kind is ReplayGainKind.Tracks or ReplayGainKind.Loose ? Path.GetDirectoryName(paths[0])! : paths[0];

    /// <summary>Shows the window and starts the run, or says why it can't start.</summary>
    public void Start()
    {
        Ui.Show(this);
        if (options.Validate() is { Count: > 0 } problems)
        {
            Show("error", $"The ReplayGain options need fixing first ({OptionsWhere}): {string.Join("; ", problems)}.");
            return;
        }
        if (!File.Exists(Rsgain.Exe))
        {
            Show("error", $"rsgain isn't where Refrain expects it ({Rsgain.Exe}). Reinstall Refrain.");
            return;
        }
        int token;
        try
        {
            token = locks.Acquire(ReplayGainTask.LockPaths(kind, paths), ReplayGainTask.Owner);
        }
        catch (LockedException e)
        {
            Show("error", e.Message);
            return;
        }
        running = true;
        Show("running", "");
        clock.Start();
        Tick();
        var reporter = new Reporter(this);
        Task.Run(() => ReplayGainTask.Run(kind, paths, options, cancel.Token, reporter)).ContinueWith(task =>
        {
            locks.Release(token);
            ui.TryEnqueue(() => Ended(task.Result));
        }, TaskScheduler.Default);
    }

    void Ended(ReplayGainOutcome result)
    {
        running = false;
        clock.Stop();
        outcome = result;
        FlushLines();
        Finished?.Invoke(ReplayGainTask.LockPaths(kind, paths));
        if (closing)
        {
            Close();
            return;
        }
        if (result.Error is null && !result.Nothing) report.Text = result.Text().Replace("\n", "\r");
        Show(result.Error is not null ? "error" : "result", result.Message());
    }

    void Stop()
    {
        if (!running) return;
        stop.IsEnabled = false;
        message.Text = "Stopping…";
        cancel.Cancel();
    }

    async Task<bool> ConfirmStop()
    {
        var dialog = new ContentDialog
        {
            Title = "ReplayGain is running",
            Content = "Stop ReplayGain and close? The album being measured is reported as cancelled and isn't leveled; " +
                      "the ones after it aren't processed.",
            PrimaryButtonText = "Stop and close",
            CloseButtonText = "Keep running",
            DefaultButton = ContentDialogButton.Close,
            XamlRoot = Content.XamlRoot,
        };
        return await dialog.ShowAsync() == ContentDialogResult.Primary;
    }

    /// <summary>While an album is scanned, its elapsed time: rsgain prints nothing until the album is done, so without
    /// it a long album looks frozen.</summary>
    void Tick()
    {
        if (!running) return;
        var seconds = (int)albumClock.Elapsed.TotalSeconds;
        message.Text = $"{album} · {seconds / 60}:{seconds % 60:00}. rsgain reports each album when it is done; " +
                       "files in this folder can't be edited meanwhile.";
    }

    void Show(string state, string text)
    {
        Ui.Fit(this);  // the first state is the run (or why it can't start): the report has a minimum size meanwhile
        message.Text = text;
        busy.Visibility = state == "running" ? Visibility.Visible : Visibility.Collapsed;
        report.Visibility = state is "running" or "result" ? Visibility.Visible : Visibility.Collapsed;
        stop.Visibility = state == "running" ? Visibility.Visible : Visibility.Collapsed;
        openLog.Visibility = state == "result" && outcome?.LogPath is not null ? Visibility.Visible : Visibility.Collapsed;
        reveal.Visibility = state is "result" or "error" ? Visibility.Visible : Visibility.Collapsed;
        done.Visibility = state == "running" ? Visibility.Collapsed : Visibility.Visible;
        done.Content = state == "result" ? "Done" : "Close";
    }

    // -- live lines, from the run's threads --

    void AddLine(string line)
    {
        lock (pending)
        {
            pending.Add(line);
            if (pending.Count > 1) return;  // a flush is already on its way
        }
        ui.TryEnqueue(DispatcherQueuePriority.Low, FlushLines);
    }

    void FlushLines()
    {
        string[] lines;
        lock (pending)
        {
            lines = [.. pending];
            pending.Clear();
        }
        if (lines.Length == 0 || outcome is not null) return;
        report.Text += string.Concat(lines.Select(l => l + "\r"));
        report.SelectionStart = report.Text.Length;
    }

    void Progressed(int doneCount, int total, string label)
    {
        if (total <= 0) return;
        busy.IsIndeterminate = false;
        busy.Maximum = total;
        busy.Value = doneCount;
        if (label.Length == 0 || label == "done") return;
        album = $"{unit} {Math.Min(doneCount + 1, total)} of {total}: {label}";
        albumClock.Restart();
        Tick();
    }

    sealed class Reporter(ReplayGainWindow window) : IReporter
    {
        public void Info(string message) => window.AddLine(message);
        public void Warn(string message) => window.AddLine($"WARNING  {message}");
        public void Error(string message) => window.AddLine($"ERROR  {message}");
        public void Progress(int done, int total, string label) =>
            window.ui.TryEnqueue(() => window.Progressed(done, total, label));
    }
}
