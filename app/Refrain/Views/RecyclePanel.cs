using Microsoft.UI.Dispatching;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Refrain.Core;
using Refrain.Core.Tidy;

namespace Refrain.Views;

/// <summary>The redundant files (SPEC §32, §36 "Tidy's redundant files in Tidy's own window"), shown in Tidy's window
/// under its report: one row per file or whole folder, every box checked; kept items below, greyed, with their reason.
/// Delete selected sends the checked ones to the Recycle Bin under a lock on them, then the list becomes its report.
/// Closing the window before keeps everything.</summary>
public sealed partial class RecyclePanel : Grid
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

    /// <summary>The folders whose contents changed, once the items are sent: the index re-reads them.</summary>
    public event Action<IReadOnlyList<string>>? Finished;

    /// <summary>Items are being sent to the Recycle Bin.</summary>
    public bool Running { get; private set; }

    /// <summary><paramref name="recycle"/>: replaced in tests, which never reach the real Recycle Bin.</summary>
    public RecyclePanel(IReadOnlyList<OfferItem> items, string owner, string introText, PathLocks locks,
        Action<string>? recycle = null, Func<string, string?>? canRecycle = null)
    {
        this.owner = owner;
        this.locks = locks;
        this.recycle = recycle;
        canRecycle ??= Recycle.CanRecycle;
        intro.Text = introText;
        RowSpacing = 8;

        list.Children.Add(Row("File", "Folder", "Why", null, header: true));
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
        var buttons = new StackPanel { Orientation = Orientation.Horizontal, Spacing = 8 };
        buttons.Children.Add(delete);
        buttons.Children.Add(stop);
        buttons.Children.Add(progress);
        progress.VerticalAlignment = VerticalAlignment.Center;

        var heading = new TextBlock { Text = "Redundant files", FontSize = 16, FontWeight = Microsoft.UI.Text.FontWeights.SemiBold };
        FrameworkElement[] parts = [heading, intro, checks, listScroll, buttons];
        for (var i = 0; i < parts.Length; i++)
        {
            RowDefinitions.Add(new RowDefinition { Height = parts[i] == listScroll ? new GridLength(1, GridUnitType.Star) : GridLength.Auto });
            SetRow(parts[i], i);
            Children.Add(parts[i]);
        }
        SetRow(report, Grid.GetRow(listScroll));  // the report takes the list's place, in its row
        Children.Add(report);

        delete.Click += (_, _) => Run();
        stop.Click += (_, _) => Stop();
        report.Visibility = Visibility.Collapsed;
        stop.Visibility = Visibility.Collapsed;
        Update();
    }

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
            SetColumn((FrameworkElement)cells[i], i);
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

    void Update() => delete.IsEnabled = !Running && rows.Any(r => r.Box.IsChecked == true);

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
        Running = true;
        foreach (var (_, box) in rows) box.IsEnabled = false;
        checks.Visibility = Visibility.Collapsed;
        delete.Visibility = Visibility.Collapsed;
        stop.Visibility = Visibility.Visible;
        Task.Run(() => RecycleTask.Run(chosen, cancel.Token, recycle,
                (done, total, name) => ui.TryEnqueue(() => progress.Text = name.Length > 0 ? $"Sending to the Recycle Bin ({done + 1} of {total}): {name}" : "")))
            .ContinueWith(task =>
            {
                locks.Release(token);
                ui.TryEnqueue(() => Ended(task.Result));
            }, TaskScheduler.Default);
    }

    /// <summary>Stops after the item being sent now (the window closing, or Stop).</summary>
    public void Stop()
    {
        if (!Running) return;
        stop.IsEnabled = false;
        cancel.Cancel();
    }

    void Ended(RecycleReport result)
    {
        Running = false;
        intro.Text = "Done. What went to the Recycle Bin, and what stayed:";
        listScroll.Visibility = Visibility.Collapsed;
        report.Text = result.Text().Replace("\n", "\r");
        report.Visibility = Visibility.Visible;
        progress.Text = "";
        stop.Visibility = Visibility.Collapsed;
        Finished?.Invoke(result.Folders);
    }
}
