using Microsoft.UI.Dispatching;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Refrain.Core;
using Refrain.Core.Tidy;

namespace Refrain.Views;

/// <summary>The Redundant files window (SPEC §32, one window for Tidy and, later, Clean up): one row per file or whole
/// folder, every box checked; kept items below, greyed, with their reason. Delete selected sends the checked ones to
/// the Recycle Bin under a lock on them, then the window becomes its report. Closing it before keeps everything.</summary>
public sealed partial class RecycleWindow : Window
{
    readonly string owner;
    readonly PathLocks locks;
    readonly Action<string>? recycle;
    readonly DispatcherQueue ui = DispatcherQueue.GetForCurrentThread();
    readonly CancellationTokenSource cancel = new();
    readonly List<(OfferItem Item, CheckBox Box)> rows = [];
    readonly TextBlock intro = new() { TextWrapping = TextWrapping.Wrap };
    readonly TextBlock progress = Ui.Secondary();
    readonly StackPanel list = new() { Spacing = 2 };
    readonly ScrollViewer listScroll = new();
    readonly TextBox report = Ui.ReportBox();
    readonly StackPanel checks = new() { Orientation = Orientation.Horizontal, Spacing = 8 };
    readonly Button delete = Ui.Primary("Delete selected");
    readonly Button stop = Ui.Button("Stop", "Stop after the item being sent now; the others stay");
    readonly Button close = Ui.Button("Close", "Close this window; whatever is still listed is kept");
    bool running;
    bool closing;

    /// <summary>The folders whose contents changed, once the items are sent: the index re-reads them.</summary>
    public event Action<IReadOnlyList<string>>? Finished;

    /// <summary><paramref name="recycle"/>: replaced in tests, which never reach the real Recycle Bin.</summary>
    public RecycleWindow(string folder, IReadOnlyList<OfferItem> items, string owner, string introText, PathLocks locks,
        Action<string>? recycle = null, Func<string, string?>? canRecycle = null)
    {
        this.owner = owner;
        this.locks = locks;
        this.recycle = recycle;
        canRecycle ??= Recycle.CanRecycle;
        var name = Path.GetFileName(Path.TrimEndingDirectorySeparator(folder));
        Ui.Prepare(this, $"Refrain · Redundant files: {(name.Length > 0 ? name : folder)}", 900, 560);
        intro.Text = introText;

        var heading = new StackPanel { Spacing = 2 };
        heading.Children.Add(Ui.Title("Redundant files"));
        heading.Children.Add(Ui.Secondary(folder));

        var header = Row("File", "Folder", "Why", null, header: true);
        list.Children.Add(header);
        var kept = new List<(OfferItem, string)>();
        foreach (var item in items)
        {
            if (canRecycle(item.Path) is { } why) kept.Add((item, $"kept: {why}"));
            else
            {
                var box = new CheckBox { Content = item.Name, IsChecked = true, MinWidth = 0 };
                ToolTipService.SetToolTip(box, "Checked: goes to the Recycle Bin on Delete selected. Uncheck it to keep it");
                box.Checked += (_, _) => Update();
                box.Unchecked += (_, _) => Update();
                rows.Add((item, box));
                list.Children.Add(Row(box, Path.GetDirectoryName(item.Path) ?? "", item.Why, item));
            }
        }
        foreach (var (item, why) in kept)  // below the others, greyed, no checkbox: nothing is left out silently
        {
            var row = Row(item.Name, Path.GetDirectoryName(item.Path) ?? "", why, item);
            row.Opacity = 0.55;
            list.Children.Add(row);
        }
        listScroll.Content = list;

        var checkAll = Ui.Button("Check all", "Check every item");
        checkAll.Click += (_, _) => SetAll(true);
        var uncheckAll = Ui.Button("Uncheck all", "Uncheck every item: nothing goes");
        uncheckAll.Click += (_, _) => SetAll(false);
        checks.Children.Add(checkAll);
        checks.Children.Add(uncheckAll);

        ToolTipService.SetToolTip(delete, "Send the checked items to the Recycle Bin now");
        var buttons = new StackPanel { Orientation = Orientation.Horizontal, Spacing = 8, HorizontalAlignment = HorizontalAlignment.Right };
        buttons.Children.Add(delete);
        buttons.Children.Add(stop);
        buttons.Children.Add(close);

        var root = new Grid { Padding = new Thickness(24, 16, 24, 16), RowSpacing = 10 };
        FrameworkElement[] parts = [heading, intro, checks, listScroll, report, progress, buttons];
        for (var i = 0; i < parts.Length; i++)
        {
            var stretch = parts[i] == listScroll || parts[i] == report;
            root.RowDefinitions.Add(new RowDefinition { Height = stretch ? new GridLength(1, GridUnitType.Star) : GridLength.Auto });
            Grid.SetRow(parts[i], i);
            root.Children.Add(parts[i]);
        }
        Content = root;

        delete.Click += (_, _) => Run();
        stop.Click += (_, _) => Stop();
        close.Click += (_, _) => Close();
        AppWindow.Closing += (_, e) =>
        {
            if (!running) return;
            e.Cancel = true;  // closing while it recycles stops it first
            closing = true;
            Stop();
        };
        report.Visibility = Visibility.Collapsed;
        stop.Visibility = Visibility.Collapsed;
        Update();
    }

    public void Start() => Ui.Show(this);

    /// <summary>A row of three columns; a file's or folder's row opens in File Explorer from its context menu.</summary>
    static Grid Row(object file, string folder, string why, OfferItem? item, bool header = false)
    {
        var grid = new Grid { ColumnSpacing = 12, Padding = new Thickness(4, 2, 4, 2) };
        grid.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(3, GridUnitType.Star) });
        grid.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(3, GridUnitType.Star) });
        grid.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(2, GridUnitType.Star) });
        UIElement Cell(object content) => content as UIElement ?? new TextBlock
        {
            Text = (string)content,
            TextTrimming = TextTrimming.CharacterEllipsis,
            VerticalAlignment = VerticalAlignment.Center,
            FontWeight = header ? Microsoft.UI.Text.FontWeights.SemiBold : Microsoft.UI.Text.FontWeights.Normal,
        };
        var cells = new[] { Cell(file), Cell(folder), Cell(why) };
        for (var i = 0; i < cells.Length; i++)
        {
            Grid.SetColumn((FrameworkElement)cells[i], i);
            grid.Children.Add(cells[i]);
        }
        if (item is not null)
        {
            ToolTipService.SetToolTip(cells[1], folder);
            var menu = new MenuFlyout();
            var reveal = new MenuFlyoutItem { Text = "Open in File Explorer" };
            reveal.Click += (_, _) => Shell.Reveal(item.Path, select: true);
            menu.Items.Add(reveal);
            grid.ContextFlyout = menu;
        }
        return grid;
    }

    void SetAll(bool value)
    {
        foreach (var (_, box) in rows) box.IsChecked = value;
        Update();
    }

    void Update() => delete.IsEnabled = !running && rows.Any(r => r.Box.IsChecked == true);

    void Run()
    {
        var chosen = rows.Where(r => r.Box.IsChecked == true).Select(r => r.Item).ToList();
        if (chosen.Count == 0) return;
        int token;
        try
        {
            token = locks.Acquire(chosen.Select(i => i.Path), owner);
        }
        catch (LockedException e)
        {
            progress.Text = e.Message;
            return;
        }
        running = true;
        foreach (var (_, box) in rows) box.IsEnabled = false;
        checks.Visibility = Visibility.Collapsed;
        delete.Visibility = Visibility.Collapsed;
        close.Visibility = Visibility.Collapsed;
        stop.Visibility = Visibility.Visible;
        Task.Run(() => RecycleTask.Run(chosen, cancel.Token, recycle,
                (done, total, name) => ui.TryEnqueue(() => progress.Text = name.Length > 0 ? $"Sending to the Recycle Bin ({done + 1} of {total}): {name}" : "")))
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

    void Ended(RecycleReport result)
    {
        running = false;
        Finished?.Invoke(result.Folders);
        if (closing)
        {
            Close();
            return;
        }
        intro.Text = "Done. What went to the Recycle Bin, and what stayed:";
        listScroll.Visibility = Visibility.Collapsed;
        report.Text = result.Text().Replace("\n", "\r");
        report.Visibility = Visibility.Visible;
        progress.Text = "";
        stop.Visibility = Visibility.Collapsed;
        close.Visibility = Visibility.Visible;
    }
}
