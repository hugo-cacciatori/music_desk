using Microsoft.UI.Dispatching;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Refrain.Core;
using Refrain.Core.Options;
using Refrain.Core.Cue;
using Refrain.Core.ReplayGain;
using Refrain.Core.Tidy;

namespace Refrain.Views;

/// <summary>Tidy's own window (SPEC §28, §31, §35): it reads the tree, shows the whole plan (every album with what
/// will happen to it), and Run Tidy does it album by album under a lock on the trees, with progress and live lines,
/// then the report, which stays until closed. When something is offered, the Redundant files window opens at the end.
/// Closing it while Tidy runs asks first.</summary>
public sealed partial class TidyWindow : Window
{
    readonly IReadOnlyList<string> folders;
    readonly Settings settings;
    readonly PathLocks locks;
    readonly DispatcherQueue ui = DispatcherQueue.GetForCurrentThread();
    readonly CancellationTokenSource cancel = new();
    readonly TextBlock summary = new() { TextWrapping = TextWrapping.Wrap, FontWeight = Microsoft.UI.Text.FontWeights.SemiBold };
    readonly TextBlock message = new() { TextWrapping = TextWrapping.Wrap };
    readonly ProgressBar busy = new() { IsIndeterminate = true, Width = 160, VerticalAlignment = VerticalAlignment.Center };
    readonly StackPanel albums = new() { Spacing = 4 };
    readonly ScrollViewer planScroll = new();
    readonly TextBox log = Ui.ReportBox();
    readonly Button run = Ui.Primary("Run Tidy");
    readonly Button stop = Ui.Button("Stop", "Stop after the current step; no further album starts");
    readonly Button close = Ui.Button("Close", "Close this window; nothing has been changed");
    readonly Button done = Ui.Button("Done", "Close this window");
    readonly List<string> pending = [];
    TidyPlan? plan;
    bool running;
    bool closing;

    /// <summary>The trees Tidy worked in, when the run ends: the index re-reads them.</summary>
    public event Action<IReadOnlyList<string>>? Finished;

    public TidyWindow(IReadOnlyList<string> folders, Settings settings, PathLocks locks)
    {
        this.folders = folders;
        this.settings = settings;
        this.locks = locks;
        Ui.Prepare(this, "Refrain · Tidy", 1000, 700);

        var heading = new StackPanel { Spacing = 2 };
        heading.Children.Add(Ui.Title("Tidy"));
        var where = Ui.Secondary(folders.Count == 1 ? folders[0] : $"{folders.Count} folders in {Path.GetDirectoryName(folders[0])}");
        where.IsTextSelectionEnabled = true;
        heading.Children.Add(where);
        planScroll.Content = albums;
        ToolTipService.SetToolTip(run, "Do everything listed: split, rename, then ReplayGain, album by album");

        var buttons = new StackPanel { Orientation = Orientation.Horizontal, Spacing = 8, HorizontalAlignment = HorizontalAlignment.Right };
        foreach (var element in new FrameworkElement[] { busy, run, stop, close, done }) buttons.Children.Add(element);

        var root = new Grid { Padding = new Thickness(24, 16, 24, 16), RowSpacing = 10 };
        FrameworkElement[] parts = [heading, summary, message, planScroll, log, buttons];
        for (var i = 0; i < parts.Length; i++)
        {
            var stretch = parts[i] == planScroll || parts[i] == log;
            root.RowDefinitions.Add(new RowDefinition { Height = stretch ? new GridLength(1, GridUnitType.Star) : GridLength.Auto });
            Grid.SetRow(parts[i], i);
            root.Children.Add(parts[i]);
        }
        Content = root;

        run.Click += (_, _) => Run();
        stop.Click += (_, _) => Stop();
        close.Click += (_, _) => Close();
        done.Click += (_, _) => Close();
        AppWindow.Closing += async (_, e) =>
        {
            if (!running || closing) return;
            e.Cancel = true;
            var dialog = new ContentDialog
            {
                Title = "Tidy is running",
                Content = "Stop Tidy and close? The album being worked on stops after its current step; the ones after it " +
                          "aren't touched.",
                PrimaryButtonText = "Stop and close",
                CloseButtonText = "Keep running",
                DefaultButton = ContentDialogButton.Close,
                XamlRoot = Content.XamlRoot,
            };
            if (await dialog.ShowAsync() != ContentDialogResult.Primary) return;
            closing = true;
            Stop();
        };
        Closed += (_, _) => cancel.Cancel();
        Show("planning");
    }

    /// <summary>Shows the window and reads the tree, or says why Tidy can't run.</summary>
    public void Start()
    {
        Ui.Show(this);
        var (names, replayGain, tidy) = (settings.GetFileNames(), settings.GetReplayGain(), settings.GetTidy());
        var problems = names.Validate().Concat(replayGain.Validate()).Concat(tidy.Validate()).ToList();
        if (problems.Count > 0)
        {
            Error($"The options need fixing first ({ReplayGainWindow.OptionsWhere}): {string.Join("; ", problems)}.");
            return;
        }
        foreach (var (tool, exe) in new[] { ("rsgain", Rsgain.Exe), ("flac", Flac.Exe) })
            if (!File.Exists(exe))
            {
                Error($"{tool} isn't where Refrain expects it ({exe}). Reinstall Refrain.");
                return;
            }
        summary.Text = "Reading the albums…";
        // A pregap before track 1 is a hidden track only when flac hears something in it (SPEC §22).
        var silent = Split.IsSilent(new Flac(Flac.Exe), cancel.Token);
        Task.Run(() => Tidying.Plan(folders, names, replayGain, tidy, cancel.Token,
                (n, total, folder) => ui.TryEnqueue(() =>
                {
                    if (total <= 0 || plan is not null) return;
                    busy.IsIndeterminate = false;
                    busy.Maximum = total;
                    busy.Value = n;
                    message.Text = folder.Length > 0 ? $"Reading {folder}" : "";
                }), silent))
            .ContinueWith(task => ui.TryEnqueue(() =>
            {
                if (task.IsCanceled || task.Exception?.InnerException is OperationCanceledException) return;
                if (task.Exception is { } error)
                {
                    Log.Error("Planning Tidy failed", error);
                    Error($"Reading the albums failed: {error.InnerException?.Message ?? error.Message}");
                    return;
                }
                ShowPlan(task.Result);
            }), TaskScheduler.Default);
    }

    void Error(string text)
    {
        summary.Text = "Tidy can't run.";
        message.Text = text;
        Show("error");
    }

    static string Relative(string folder, string root)
    {
        if (root.Length == 0) return folder;
        var relative = Path.GetRelativePath(root, folder);
        return relative == "." ? Path.GetFileName(folder) : relative.StartsWith("..", StringComparison.Ordinal) ? folder : relative;
    }

    /// <summary>One line of the plan: the album (or a file) on the left, what happens on the right.</summary>
    static Grid Line(string left, string right, bool strong = false)
    {
        var grid = new Grid { ColumnSpacing = 16 };
        grid.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(2, GridUnitType.Star) });
        grid.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(3, GridUnitType.Star) });
        var a = new TextBlock { Text = left, TextTrimming = TextTrimming.CharacterEllipsis };
        if (strong) a.FontWeight = Microsoft.UI.Text.FontWeights.SemiBold;
        ToolTipService.SetToolTip(a, left);
        var b = new TextBlock { Text = right, TextWrapping = TextWrapping.Wrap };
        Grid.SetColumn(b, 1);
        grid.Children.Add(a);
        grid.Children.Add(b);
        return grid;
    }

    static void AddDetails(StackPanel details, FolderPlan folder)
    {
        foreach (var each in folder.Renames?.With(RenameStatus.Rename) ?? [])
            details.Children.Add(Line(Path.GetFileName(each.Source), $"→ {Path.GetFileName(each.Target)}"));
        foreach (var note in folder.Notes) details.Children.Add(Line("", note));
        foreach (var path in folder.Offer) details.Children.Add(Line(Path.GetFileName(path), "kept; offered for deletion at the end"));
    }

    void ShowPlan(TidyPlan result)
    {
        plan = result;
        albums.Children.Clear();
        foreach (var album in result.Albums)
        {
            var details = new StackPanel { Spacing = 2 };
            foreach (var folder in album.Folders)
            {
                if (folder.Folder == album.Folder)
                {
                    AddDetails(details, folder);
                    continue;
                }
                var actions = TidyText.FolderActions(folder);
                details.Children.Add(Line(Relative(folder.Folder, result.Root), actions.Count > 0 ? string.Join(", then ", actions) : "Nothing to do", strong: true));
                AddDetails(details, folder);
            }
            foreach (var each in album.Clutter)
                details.Children.Add(Line(each.IsFolder ? Relative(each.Path, album.Folder) : each.Name, $"{each.Why}: offered at the end"));
            var row = Line(Relative(album.Folder, result.Root), TidyText.AlbumText(album));
            row.Margin = new Thickness(0, 0, 8, 0);
            albums.Children.Add(details.Children.Count == 0
                ? new Border { Child = row, Padding = new Thickness(16, 10, 48, 10), Background = Ui.Brush("CardBackgroundFillColorDefaultBrush"), CornerRadius = new CornerRadius(4) }
                : new Expander { Header = row, Content = details, HorizontalAlignment = HorizontalAlignment.Stretch, HorizontalContentAlignment = HorizontalAlignment.Stretch });
        }
        // Clutter folders outside any album, under their parent folder.
        foreach (var group in result.Loose.GroupBy(i => Path.GetDirectoryName(i.Path)!))
        {
            var row = Line(Relative(group.Key, result.Root), string.Join("; ", group.Select(TidyText.LooseText)));
            albums.Children.Add(new Border { Child = row, Padding = new Thickness(16, 10, 48, 10), Background = Ui.Brush("CardBackgroundFillColorDefaultBrush"), CornerRadius = new CornerRadius(4) });
        }
        summary.Text = result.Summary() + (result.HasWork ? "." : ". Nothing to do.");
        message.Text = result.HasWork ? TidyText.RunNote : "";
        run.IsEnabled = result.HasWork;
        Show("plan");
    }

    void Run()
    {
        if (plan is null) return;
        int token;
        try
        {
            token = locks.Acquire(plan.Trees, TidyTask.Owner);
        }
        catch (LockedException e)
        {
            message.Text = e.Message;
            return;
        }
        running = true;
        summary.Text = "Tidying… Files in this folder can't be edited meanwhile.";
        message.Text = "";
        busy.IsIndeterminate = true;
        log.Text = "";
        Show("running");
        var options = settings.GetReplayGain();
        var reporter = new Reporter(this);
        var current = plan;
        Task.Run(() => TidyTask.Run(current, options, cancel.Token, reporter)).ContinueWith(task =>
        {
            locks.Release(token);
            ui.TryEnqueue(() => Ended(current, task.Result));
        }, TaskScheduler.Default);
    }

    void Stop()
    {
        if (!running) return;
        stop.IsEnabled = false;
        message.Text = "Stopping after the current step…";
        cancel.Cancel();
    }

    void Ended(TidyPlan current, TidyOutcome outcome)
    {
        running = false;
        Finished?.Invoke(current.Trees);
        var offers = outcome.Offers;
        if (offers.Count > 0)
        {
            var window = new RecycleWindow(current.Root.Length > 0 ? current.Root : folders[0], offers, TidyTask.Owner,
                TidyText.OfferIntro, locks);
            window.Finished += changed => Finished?.Invoke(changed);
            window.Start();
        }
        if (closing)
        {
            Close();
            return;
        }
        var text = outcome.Text();
        summary.Text = text.Split('\n')[0];
        message.Text = "";
        log.Text = text.Replace("\n", "\r");
        Show("result");
    }

    void Show(string state)
    {
        planScroll.Visibility = state == "plan" ? Visibility.Visible : Visibility.Collapsed;
        log.Visibility = state is "running" or "result" ? Visibility.Visible : Visibility.Collapsed;
        busy.Visibility = state is "planning" or "running" ? Visibility.Visible : Visibility.Collapsed;
        run.Visibility = state == "plan" ? Visibility.Visible : Visibility.Collapsed;
        stop.Visibility = state == "running" ? Visibility.Visible : Visibility.Collapsed;
        done.Visibility = state is "result" or "error" ? Visibility.Visible : Visibility.Collapsed;
        close.Visibility = state is "planning" or "plan" ? Visibility.Visible : Visibility.Collapsed;  // while it runs: Stop, then Done
        message.Visibility = message.Text.Length > 0 || state == "planning" ? Visibility.Visible : Visibility.Collapsed;
    }

    // -- live lines, from the run's threads --

    void AddLine(string line)
    {
        lock (pending)
        {
            pending.Add(line);
            if (pending.Count > 1) return;
        }
        ui.TryEnqueue(DispatcherQueuePriority.Low, () =>
        {
            string[] lines;
            lock (pending)
            {
                lines = [.. pending];
                pending.Clear();
            }
            if (running) log.Text += string.Concat(lines.Select(l => l + "\r"));
        });
    }

    sealed class Reporter(TidyWindow window) : IReporter
    {
        public void Info(string message) => window.AddLine(message);
        public void Warn(string message) => window.AddLine($"WARNING  {message}");
        public void Error(string message) => window.AddLine($"ERROR  {message}");
        public void Progress(int done, int total, string label) => window.ui.TryEnqueue(() =>
        {
            if (total <= 0) return;
            window.busy.IsIndeterminate = false;
            window.busy.Maximum = total;
            window.busy.Value = done;
            if (label.Length > 0 && label != "done") window.message.Text = $"Album {done + 1} of {total}: {label}";
            window.message.Visibility = Visibility.Visible;
        });
    }
}
