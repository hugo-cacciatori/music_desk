using Microsoft.UI.Dispatching;
using Microsoft.UI.Input;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Documents;
using Microsoft.UI.Xaml.Input;
using Microsoft.UI.Xaml.Media;
using Line = Microsoft.UI.Xaml.Shapes.Line;
using Polygon = Microsoft.UI.Xaml.Shapes.Polygon;
using Rectangle = Microsoft.UI.Xaml.Shapes.Rectangle;
using Refrain.Core;
using Refrain.Core.Cue;
using VirtualKey = Windows.System.VirtualKey;

namespace Refrain.Views;

/// <summary>The whole track's waveform (SPEC §23), 70 px high, with a start and an end handle; outside them is dimmed.
/// Positions are in milliseconds, as the fields show them. The peaks are fitted to the width only when the width
/// changes, so dragging a handle never decodes or redraws the waveform (Ground rule 2).</summary>
sealed partial class WaveformView : Grid
{
    public const double WaveformHeight = 70;  // filling the page, it was much too big (Hugo, M16)
    const double GrabPx = 8;  // how near a handle the pointer shows the resize cursor

    readonly Canvas canvas = new();
    readonly Polygon wave = new() { StrokeThickness = 1 };
    readonly Line middle = new() { StrokeThickness = 1 };
    readonly Rectangle dimStart = new(), dimEnd = new();
    readonly Rectangle startLine = new() { Width = 2 }, endLine = new() { Width = 2 };
    readonly Rectangle[] tabs = [new() { Width = 8, Height = 10 }, new() { Width = 8, Height = 10 }, new() { Width = 8, Height = 10 }, new() { Width = 8, Height = 10 }];
    readonly TextBlock message = new() { TextWrapping = TextWrapping.Wrap, HorizontalAlignment = HorizontalAlignment.Center, VerticalAlignment = VerticalAlignment.Center };
    Peaks? peaks;
    string? dragging;  // "start" or "end"

    public long TotalMs { get; private set; }
    public long StartMs { get; private set; }
    public long EndMs { get; private set; }

    public event Action<long>? StartMoved;  // ms, while dragging
    public event Action<long>? EndMoved;

    public WaveformView()
    {
        Height = WaveformHeight;
        Background = Ui.Brush("ControlFillColorDefaultBrush");
        BorderBrush = Ui.Brush("ControlStrokeColorDefaultBrush");
        BorderThickness = new Thickness(1);
        CornerRadius = new CornerRadius(4);
        wave.Fill = wave.Stroke = Ui.Brush("AccentFillColorDefaultBrush");
        middle.Stroke = Ui.Brush("TextFillColorTertiaryBrush");
        var dim = new SolidColorBrush(((SolidColorBrush)Ui.Brush("SolidBackgroundFillColorBaseBrush")).Color) { Opacity = 0.7 };
        dimStart.Fill = dimEnd.Fill = dim;
        startLine.Fill = endLine.Fill = Ui.Brush("TextFillColorPrimaryBrush");
        foreach (var tab in tabs) tab.Fill = startLine.Fill;
        message.Foreground = Ui.Brush("TextFillColorSecondaryBrush");
        canvas.Children.Add(middle);
        canvas.Children.Add(wave);
        UIElement[] parts = [dimStart, dimEnd, startLine, endLine, .. tabs];
        foreach (var part in parts) canvas.Children.Add(part);
        Children.Add(canvas);
        Children.Add(message);
        SizeChanged += (_, _) =>
        {
            DrawWave();
            DrawHandles();
        };
        PointerPressed += Pressed;
        PointerMoved += Moved;
        PointerReleased += (_, e) => Release(e.Pointer);
        PointerCaptureLost += (_, _) => dragging = null;
    }

    // -- what the window sets --

    public void SetTrack(long totalMs)
    {
        (TotalMs, StartMs, EndMs) = (totalMs, 0, totalMs);
        SetPeaks(null, "Reading the audio…");
    }

    public void SetPeaks(Peaks? read, string text = "")
    {
        peaks = read;
        message.Text = text;
        message.Visibility = text.Length > 0 ? Visibility.Visible : Visibility.Collapsed;
        DrawWave();
        DrawHandles();
    }

    /// <summary>A field was typed: its handle follows (clamped to the track; a bad value leaves it where it is).</summary>
    public void SetRange(long? startMs, long? endMs)
    {
        if (startMs is { } s) StartMs = Math.Clamp(s, 0, TotalMs);
        if (endMs is { } e) EndMs = Math.Clamp(e, 0, TotalMs);
        DrawHandles();
    }

    // -- geometry --

    double X(long ms) => TotalMs > 0 ? ms * (ActualWidth - 1) / TotalMs : 0;

    long MsAt(double x) =>
        ActualWidth <= 1 || TotalMs == 0 ? 0 : Math.Clamp((long)Math.Round(x * TotalMs / (ActualWidth - 1), MidpointRounding.ToEven), 0, TotalMs);

    /// <summary>The peaks as one filled outline, a point per pixel column: the highs left to right, then the lows back.</summary>
    void DrawWave()
    {
        var (width, height) = (ActualWidth, ActualHeight);
        middle.X1 = 0;
        middle.X2 = width;
        middle.Y1 = middle.Y2 = height / 2;
        middle.Visibility = peaks is null ? Visibility.Visible : Visibility.Collapsed;
        var points = new PointCollection();
        if (peaks is not null && width > 1)
        {
            var columns = Waveform.Fit(peaks, (int)width);
            var (mid, half) = (height / 2, height / 2 - 2);
            for (var x = 0; x < columns.Count; x++) points.Add(new Windows.Foundation.Point(x + 0.5, mid - columns[x].High * half));
            for (var x = columns.Count - 1; x >= 0; x--) points.Add(new Windows.Foundation.Point(x + 0.5, mid - columns[x].Low * half));
        }
        wave.Points = points;
    }

    void DrawHandles()
    {
        var visible = TotalMs > 0 ? Visibility.Visible : Visibility.Collapsed;
        FrameworkElement[] parts = [dimStart, dimEnd, startLine, endLine, .. tabs];
        foreach (var part in parts) part.Visibility = visible;
        if (TotalMs == 0) return;
        var (width, height) = (ActualWidth, ActualHeight);
        var (left, right) = (X(StartMs), X(EndMs));
        dimStart.Width = Math.Max(0, left);
        dimEnd.Width = Math.Max(0, width - right);
        dimStart.Height = dimEnd.Height = startLine.Height = endLine.Height = height;
        Canvas.SetLeft(dimEnd, right);
        Canvas.SetLeft(startLine, left - 1);
        Canvas.SetLeft(endLine, right - 1);
        var i = 0;
        foreach (var x in new[] { left, right })
        {
            var tab = Math.Clamp(x - 4, 0, Math.Max(0, width - 8));  // the grab tabs stay inside at the edges
            Canvas.SetLeft(tabs[i], tab);
            Canvas.SetLeft(tabs[i + 1], tab);
            Canvas.SetTop(tabs[i + 1], height - 10);
            i += 2;
        }
    }

    // -- dragging --

    /// <summary>The handle a press at <paramref name="x"/> takes: the nearer one (the start when they meet and the
    /// press is left of them).</summary>
    string HandleNear(double x)
    {
        var (left, right) = (Math.Abs(x - X(StartMs)), Math.Abs(x - X(EndMs)));
        if (left == right) return x <= X(StartMs) ? "start" : "end";
        return left < right ? "start" : "end";
    }

    void Pressed(object sender, PointerRoutedEventArgs e)
    {
        var point = e.GetCurrentPoint(this);
        if (!point.Properties.IsLeftButtonPressed || TotalMs == 0 || !IsHitTestVisible) return;
        dragging = HandleNear(point.Position.X);
        CapturePointer(e.Pointer);
        MoveTo(point.Position.X);
        e.Handled = true;
    }

    void Moved(object sender, PointerRoutedEventArgs e)
    {
        var x = e.GetCurrentPoint(this).Position.X;
        if (dragging is not null)
        {
            MoveTo(x);
            return;
        }
        var near = TotalMs > 0 && Math.Min(Math.Abs(x - X(StartMs)), Math.Abs(x - X(EndMs))) <= GrabPx;
        ProtectedCursor = InputSystemCursor.Create(near ? InputSystemCursorShape.SizeWestEast : InputSystemCursorShape.Arrow);
    }

    void Release(Pointer pointer)
    {
        dragging = null;
        ReleasePointerCapture(pointer);
    }

    /// <summary>A handle can't pass the other one: they stay at least a millisecond apart.</summary>
    void MoveTo(double x)
    {
        var ms = MsAt(x);
        if (dragging == "start")
        {
            ms = Math.Min(ms, EndMs - 1);
            if (ms == StartMs || ms < 0) return;
            StartMs = ms;
            DrawHandles();
            StartMoved?.Invoke(ms);
        }
        else if (dragging == "end")
        {
            ms = Math.Max(ms, StartMs + 1);
            if (ms == EndMs || ms > TotalMs) return;
            EndMs = ms;
            DrawHandles();
            EndMoved?.Invoke(ms);
        }
    }
}

/// <summary>"Trim this track…" and the Metadata tab's Trim… (SPEC §23, §31): one track, its waveform with two crop
/// handles, the Start and End fields, the subfolder (… > Options…, Trim), Preview in player, Trim and Stop, then the
/// report. Each edit is planned again on a worker once it pauses; the waveform is read once, on its own worker. A trim
/// is just a trim: it writes one FLAC copy into the subfolder, and nothing runs after it.</summary>
public sealed partial class TrimWindow : Window
{
    const int PlanDelayMs = 80;  // after an edit (a handle dragged, a key typed), the plan follows once it pauses

    static readonly Dictionary<string, string> Tips = new()  // the fields' hover texts while they have no problem (SPEC §24)
    {
        ["start"] = "Where the copy starts, as m:ss.mmm; Up and Down move it by 10 ms, Page Up and Page Down by 1 s",
        ["end"] = "Where the copy ends, as m:ss.mmm; Up and Down move it by 10 ms, Page Up and Page Down by 1 s",
        ["subfolder"] = "The copy goes in this subfolder of the track's folder, created if missing; the original isn't changed",
    };

    readonly IReadOnlyList<string> paths;
    readonly Settings settings;
    readonly PathLocks locks;
    readonly DispatcherQueue ui = DispatcherQueue.GetForCurrentThread();
    readonly DispatcherQueueTimer planTimer;
    readonly CancellationTokenSource cancel = new();
    readonly Flac flac = new(Flac.Exe);
    readonly TextBlock heading = Ui.Title("Trim");
    readonly TextBlock where = Ui.Secondary();
    readonly TextBlock message = new() { Text = "Reading the track…", TextWrapping = TextWrapping.Wrap };
    readonly TextBlock formatLine = new() { TextWrapping = TextWrapping.Wrap };
    readonly WaveformView waveform = new();
    readonly TextBox startBox = new() { PlaceholderText = "m:ss.mmm", Width = 110 };
    readonly TextBox endBox = new() { PlaceholderText = "m:ss.mmm", Width = 110 };
    readonly TextBlock summary = new() { TextWrapping = TextWrapping.Wrap, VerticalAlignment = VerticalAlignment.Center };
    readonly TextBlock subfolderLine = new() { TextWrapping = TextWrapping.Wrap };
    readonly TextBlock copyPath = new() { TextWrapping = TextWrapping.Wrap, IsTextSelectionEnabled = true };
    readonly StackPanel editor = new() { Spacing = 10, Padding = new Thickness(0, 0, 12, 0) };
    readonly ScrollViewer editorScroll = new() { HorizontalScrollMode = ScrollMode.Disabled };  // never clipped
    readonly TextBox report = Ui.ReportBox();
    readonly TextBlock note = new() { TextWrapping = TextWrapping.Wrap, VerticalAlignment = VerticalAlignment.Center };
    readonly ProgressBar busy = new() { IsIndeterminate = true, Width = 120, VerticalAlignment = VerticalAlignment.Center };
    readonly Button preview = Ui.Button("Preview in player", "Open the cut start, the kept part and the cut end as three tracks in your default player, to hear the cuts before trimming");
    readonly Button trim = Ui.Primary("Trim");
    readonly Button stop = Ui.Button("Stop", "Stop the trim; nothing is left half-written");
    readonly Button close = Ui.Button("Close", "Close this window without trimming; the times you chose aren't kept");
    readonly Button done = Ui.Button("Done", "Close this window");
    string state = "loading";  // loading, editing, running, result, error
    string? path;
    SourceInfo? info;
    (string Title, string Artist, string Album) tags = ("", "", "");
    string subfolder = Trim.DefaultSubfolder;
    TrimPlan? plan;
    int planToken;
    bool planning;  // an edit waits for its plan
    bool filling;  // the window sets a field itself
    bool running;
    bool closing;

    /// <summary>The folders written to, when the trim ends: the index re-reads them.</summary>
    public event Action<IReadOnlyList<string>>? Finished;

    public TrimWindow(IReadOnlyList<string> paths, Settings settings, PathLocks locks)
    {
        this.paths = paths;
        this.settings = settings;
        this.locks = locks;
        Ui.Prepare(this, "Refrain · Trim");
        planTimer = ui.CreateTimer();
        planTimer.Interval = TimeSpan.FromMilliseconds(PlanDelayMs);
        planTimer.IsRepeating = false;
        planTimer.Tick += (_, _) => RequestPlan();
        ToolTipService.SetToolTip(trim, "Write the kept part as a FLAC copy in the subfolder; the original isn't changed");
        ToolTipService.SetToolTip(waveform, "Drag the two handles to where the copy starts and ends; a click moves the nearer handle");
        ToolTipService.SetToolTip(subfolderLine, Tips["subfolder"]);
        where.IsTextSelectionEnabled = true;
        note.Foreground = Ui.Brush("SystemFillColorCriticalBrush");

        var times = new StackPanel { Orientation = Orientation.Horizontal, Spacing = 8 };
        foreach (var (label, box) in new[] { ("Start:", startBox), ("End:", endBox) })
        {
            times.Children.Add(new TextBlock { Text = label, VerticalAlignment = VerticalAlignment.Center });
            times.Children.Add(box);
            box.TextChanged += (_, _) => TimeTyped();
            box.PreviewKeyDown += Nudge;
        }
        foreach (var part in new FrameworkElement[] { formatLine, waveform, times, summary, subfolderLine, copyPath }) editor.Children.Add(part);
        waveform.StartMoved += ms => HandleMoved(startBox, ms);
        waveform.EndMoved += ms => HandleMoved(endBox, ms);

        var head = new StackPanel { Spacing = 2 };
        head.Children.Add(heading);
        head.Children.Add(where);
        var buttons = new Grid { ColumnSpacing = 8 };  // Preview on the left; the reasons, then Trim and Close, on the right
        buttons.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });
        buttons.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
        buttons.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });
        buttons.Children.Add(preview);
        Grid.SetColumn(note, 1);
        buttons.Children.Add(note);
        var right = new StackPanel { Orientation = Orientation.Horizontal, Spacing = 8 };
        foreach (var part in new FrameworkElement[] { busy, trim, stop, close, done }) right.Children.Add(part);
        Grid.SetColumn(right, 2);
        buttons.Children.Add(right);

        var root = new Grid { Padding = new Thickness(24, 16, 24, 16), RowSpacing = 10 };
        editorScroll.Content = editor;
        FrameworkElement[] parts = [head, message, editorScroll, buttons];
        for (var i = 0; i < parts.Length; i++)
        {
            var stretch = parts[i] == editorScroll;  // the spare height goes below the fields
            root.RowDefinitions.Add(new RowDefinition { Height = stretch ? new GridLength(1, GridUnitType.Star) : GridLength.Auto });
            Grid.SetRow(parts[i], i);
            root.Children.Add(parts[i]);
        }
        Grid.SetRow(report, 2);  // the report takes the editor's place once the trim has run
        root.Children.Add(report);
        Content = root;

        preview.Click += (_, _) => Preview();
        trim.Click += (_, _) => Run();
        stop.Click += (_, _) => Stop();
        close.Click += (_, _) => Close();
        done.Click += (_, _) => Close();
        // The subfolder is … > Options…, Trim (SPEC §12): a change made there applies when the window is back in front.
        Activated += (_, e) =>
        {
            if (e.WindowActivationState != WindowActivationState.Deactivated) SubfolderChanged();
        };
        AppWindow.Closing += async (_, e) =>
        {
            if (!running || closing) return;
            e.Cancel = true;
            var dialog = new ContentDialog
            {
                Title = "Trimming",
                Content = "Stop the trim and close? The copy being written is deleted; the original is unchanged.",
                PrimaryButtonText = "Stop and close",
                CloseButtonText = "Keep trimming",
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

    /// <summary>The subfolder name from the Options, or lite when the saved one can't be used.</summary>
    string OptionsSubfolder() =>
        Trim.CheckSubfolderName(settings.TrimSubfolder) is null ? settings.TrimSubfolder : Trim.DefaultSubfolder;

    /// <summary>Shows the window, then reads the track; the waveform follows on its own worker.</summary>
    public void Start()
    {
        Ui.Show(this);
        if (paths.Count != 1 || Directory.Exists(paths[0]))
        {
            Fail("Trim didn't start: choose one track (FLAC, WAV or AIFF).");
            return;
        }
        var track = paths[0];
        where.Text = track;
        if (!File.Exists(Flac.Exe))
        {
            Fail($"flac isn't where Refrain expects it ({Flac.Exe}). Reinstall Refrain.");
            return;
        }
        Task.Run(() =>
        {
            var read = Trim.ReadSource(track);
            var named = ("", "", "");
            try
            {
                var values = Core.Tags.TagFile.Read(track).Values;
                named = (values[Core.Tags.Field.Title], values[Core.Tags.Field.Artist], values[Core.Tags.Field.Album]);
            }
            catch (Exception)
            {
                // without tags, Preview in player names the parts by the file name
            }
            return (read, named);
        }).ContinueWith(task => ui.TryEnqueue(() =>
        {
            if (task.Exception?.InnerException is { } error)
            {
                Fail(error is CueException ? error.Message : $"{Path.GetFileName(track)} can't be read: {error.Message}");
                return;
            }
            (info, tags) = task.Result;
            path = track;
            ShowTrack();
            ReadWaveform(track, info);
        }), TaskScheduler.Default);
    }

    void ReadWaveform(string track, SourceInfo source)
    {
        var token = cancel.Token;
        Task.Run(() => Waveform.Read(flac, track, source, token), token).ContinueWith(task =>
        {
            if (token.IsCancellationRequested || task.IsCanceled) return;
            ui.TryEnqueue(() =>
            {
                if (task.Exception?.InnerException is { } error)
                {
                    if (error is FlacCancelledException) return;
                    Log.Error($"The waveform of {track} couldn't be read", error);
                    waveform.SetPeaks(null, $"The audio can't be read: {error.Message}");  // the trim only needs the length
                    return;
                }
                waveform.SetPeaks(task.Result);
            });
        }, TaskScheduler.Default);
    }

    void ShowTrack()
    {
        var (source, read) = (path!, info!);
        var total = Trim.LengthMs(read);
        formatLine.Inlines.Clear();
        formatLine.Inlines.Add(new Run { Text = Path.GetFileName(source), FontWeight = Microsoft.UI.Text.FontWeights.SemiBold });
        formatLine.Inlines.Add(new Run { Text = " · " + Trim.FormatLine(read) });
        filling = true;
        startBox.Text = Trim.FormatTime(0);
        endBox.Text = Trim.FormatTime(total);
        filling = false;
        subfolder = OptionsSubfolder();
        waveform.SetTrack(total);
        Show("editing");
        Edited();
    }

    // -- the fields and the handles, in step --

    void HandleMoved(TextBox box, long ms)
    {
        filling = true;
        box.Text = Trim.FormatTime(ms);
        filling = false;
        Edited();
    }

    void TimeTyped()
    {
        if (filling) return;
        waveform.SetRange(Trim.ParseTime(startBox.Text), Trim.ParseTime(endBox.Text));
        Edited();
    }

    /// <summary>Up / Down nudge by 10 ms, Page Up / Page Down by 1 s, within the track.</summary>
    void Nudge(object sender, KeyRoutedEventArgs e)
    {
        var step = e.Key switch
        {
            VirtualKey.Up => Trim.NudgeMs,
            VirtualKey.Down => -Trim.NudgeMs,
            VirtualKey.PageUp => Trim.PageMs,
            VirtualKey.PageDown => -Trim.PageMs,
            _ => 0,
        };
        if (step == 0 || sender is not TextBox box) return;
        e.Handled = true;
        if (Trim.ParseTime(box.Text) is not { } current) return;
        var text = Trim.FormatTime(Math.Clamp(current + step, 0, waveform.TotalMs));
        if (text == box.Text) return;
        box.Text = text;  // TextChanged moves its handle and plans again
        box.SelectionStart = text.Length;
    }

    void SubfolderChanged()
    {
        var name = OptionsSubfolder();
        if (state != "editing" || name == subfolder) return;
        subfolder = name;
        Edited();
    }

    // -- the plan --

    void Edited()
    {
        if (info is null || state != "editing") return;
        planning = true;
        planTimer.Stop();
        planTimer.Start();
        ShowButtons();
    }

    void RequestPlan()
    {
        if (path is null || info is null) return;
        var token = ++planToken;
        var (source, read, start, end, sub) = (path, info, startBox.Text, endBox.Text, subfolder);
        Task.Run(() => Trim.Plan(source, read, start, end, sub)).ContinueWith(task => ui.TryEnqueue(() =>
        {
            if (token != planToken || state != "editing") return;  // a newer edit came meanwhile
            if (task.Exception is { } error)
            {
                Log.Error($"Planning the trim of {source} failed", error);
                return;
            }
            planning = planTimer.IsRunning;
            plan = task.Result;
            ShowPlan(plan);
            ShowButtons();
        }), TaskScheduler.Default);
    }

    static void Mark(Control control, string? why, string tip)
    {
        control.BorderBrush = why is not null ? Ui.Brush("SystemFillColorCriticalBrush") : null;
        control.BorderThickness = why is not null ? new Thickness(2) : new Thickness(1);
        ToolTipService.SetToolTip(control, why ?? tip);  // a problem's reason first (SPEC §24)
    }

    /// <summary>The marks, the summary, the copy's path.</summary>
    void ShowPlan(TrimPlan planned)
    {
        Mark(startBox, planned.Problem("start"), Tips["start"]);
        Mark(endBox, planned.Problem("end"), Tips["end"]);
        var line = $"Subfolder: {subfolder} (… > Options…, Trim)";
        var why = planned.Problem("subfolder");
        subfolderLine.Text = why is null ? line : $"{line}: {why}";
        subfolderLine.Foreground = why is null ? Ui.Brush("TextFillColorPrimaryBrush") : Ui.Brush("SystemFillColorCriticalBrush");
        summary.Text = planned.TimesOk && planned.Length > 0 ? planned.Summary : "";
        copyPath.Text = planned.Name.Length == 0 ? ""
            : planned.Note is { } numbered ? $"The copy: {planned.ShownPath}\n{numbered}" : $"The copy: {planned.ShownPath}";
    }

    /// <summary>Trim is enabled only on a plan without problems, every reason beside it.</summary>
    void ShowButtons()
    {
        var current = planning ? null : plan;
        var reasons = current?.Problems.Select(p => p.Why).Distinct().ToList() ?? [];
        trim.IsEnabled = current is { Ok: true } && !running;
        preview.IsEnabled = current is { TimesOk: true };
        note.Text = string.Join(" ", reasons);
    }

    // -- Preview in player --

    /// <summary>The current edit as a temporary cue sheet in the default player (SPEC §23). Nothing in the library is
    /// written.</summary>
    void Preview()
    {
        if (plan is not { TimesOk: true } || planning) return;
        var text = Trim.PreviewText(plan, tags.Title, tags.Artist, tags.Album);
        var name = Path.GetFileName(plan.Source);
        Task.Run(() => Playlists.WritePreview(text)).ContinueWith(task => ui.TryEnqueue(() =>
        {
            if (task.Exception is { } error)
            {
                Log.Error("Writing the preview sheet failed", error);
                note.Text = "The preview sheet couldn't be written; see the log.";
                return;
            }
            message.Text = Shell.TryOpen(task.Result)
                ? $"Previewing {name} in the player…"
                : "No app opens cue sheets: choose one in Settings > Apps > Default apps (search for .cue).";
            message.Visibility = Visibility.Visible;
        }), TaskScheduler.Default);
    }

    // -- the run --

    void Run()
    {
        if (plan is not { Ok: true } || planning || running) return;
        var folder = Path.GetDirectoryName(plan.Source)!;
        int token;
        try
        {
            token = locks.Acquire([folder], Trim.Owner);  // the track's folder, which holds the subfolder
        }
        catch (LockedException e)
        {
            note.Text = e.Message;
            return;
        }
        running = true;
        message.Text = $"Trimming {Path.GetFileName(plan.Source)}… Files in this folder can't be edited meanwhile.";
        Show("running");
        var current = plan;
        Task.Run(() => TrimTask.Run(current, flac, cancel.Token)).ContinueWith(task =>
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

    void Ended(TrimOutcome outcome)
    {
        running = false;
        Finished?.Invoke([Path.GetDirectoryName(outcome.Plan.Source)!]);  // the subfolder and the copy
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

    void Show(string next)
    {
        if (next != "loading") Ui.Fit(this);  // the track's editor (or why it can't be trimmed) sets the window's size
        state = next;
        var editing = next is "editing" or "running";
        message.Visibility = next == "editing" ? Visibility.Collapsed : Visibility.Visible;
        editorScroll.Visibility = editing ? Visibility.Visible : Visibility.Collapsed;
        editor.IsHitTestVisible = waveform.IsHitTestVisible = next == "editing";
        startBox.IsEnabled = endBox.IsEnabled = next == "editing";
        report.Visibility = next == "result" ? Visibility.Visible : Visibility.Collapsed;
        busy.Visibility = next is "loading" or "running" ? Visibility.Visible : Visibility.Collapsed;
        preview.Visibility = trim.Visibility = editing ? Visibility.Visible : Visibility.Collapsed;
        note.Visibility = next == "editing" ? Visibility.Visible : Visibility.Collapsed;
        stop.Visibility = next == "running" ? Visibility.Visible : Visibility.Collapsed;
        done.Visibility = next is "result" or "error" ? Visibility.Visible : Visibility.Collapsed;
        close.Visibility = next is "loading" or "editing" ? Visibility.Visible : Visibility.Collapsed;  // while it runs: Stop; then Done
        if (next == "running") preview.IsEnabled = trim.IsEnabled = false;
    }
}
