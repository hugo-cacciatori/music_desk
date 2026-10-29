using System.Collections.ObjectModel;
using Microsoft.UI.Input;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Controls.Primitives;
using Microsoft.UI.Xaml.Input;
using Microsoft.UI.Xaml.Media;
using Refrain.Core;
using Refrain.Core.Browser;
using Refrain.Core.Index;
using Windows.System;

namespace Refrain.Views;

/// <summary>A list of folders and files with columns (SPEC §5): shown, hidden, moved, resized and sorted from the
/// header, the layout reported for saving. Folders stay first in both sort directions. Used by the file list and
/// the search results.</summary>
public sealed partial class FileTable : Grid
{
    const double MinColumnWidth = 24;
    const string ResultOrder = "folder";  // the search results' own order (SPEC §6)

    readonly IReadOnlyList<Column> definitions;
    readonly bool searchResults;
    readonly ScrollViewer headerScroll = new()
    {
        HorizontalScrollMode = ScrollMode.Enabled,
        HorizontalScrollBarVisibility = ScrollBarVisibility.Hidden,
        VerticalScrollMode = ScrollMode.Disabled,
        VerticalScrollBarVisibility = ScrollBarVisibility.Disabled,
    };
    readonly Grid header = new() { Height = 32 };
    readonly ObservableCollection<FileRow> items = [];
    List<FileRow> resultOrder = [];  // search results in their own order, for the Folder column's sort
    string typed = "";
    long typedAt;

    public ListView List { get; }
    public HeaderState State { get; private set; }
    public int LayoutVersion { get; private set; }
    public Brush HighlightBrush { get; } =
        (Brush)Application.Current.Resources["SystemFillColorCautionBackgroundBrush"];

    /// <summary>Enter or a double-click on a row.</summary>
    public event Action<FileRow>? Activated;
    /// <summary>A right-click: the rows under it (selected first), or none on the background.</summary>
    public event Action<IReadOnlyList<FileRow>, UIElement, Windows.Foundation.Point>? MenuRequested;
    /// <summary>The columns or the sort changed: save the layout.</summary>
    public event Action? LayoutChanged;
    /// <summary>Backspace in the list: go up (SPEC §5).</summary>
    public event Action? UpRequested;
    /// <summary>The user changed the selection, or a new set of rows replaced it (not while rows update in place).</summary>
    public event Action? SelectionChanged;
    bool quiet;

    public FileTable(IReadOnlyList<Column> columns, HeaderState? saved, bool searchResults = false)
    {
        definitions = columns;
        this.searchResults = searchResults;
        State = Columns.Layout(columns, saved);
        RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });
        RowDefinitions.Add(new RowDefinition { Height = new GridLength(1, GridUnitType.Star) });

        headerScroll.Content = header;
        Children.Add(headerScroll);
        List = new ListView
        {
            SelectionMode = ListViewSelectionMode.Extended,
            ItemsSource = items,
            ItemTemplate = (DataTemplate)Application.Current.Resources["FileRowTemplate"],
            ItemContainerStyle = (Style)Application.Current.Resources["FileRowItemStyle"],
            IsTabStop = true,
        };
        ScrollViewer.SetHorizontalScrollMode(List, ScrollMode.Enabled);
        ScrollViewer.SetHorizontalScrollBarVisibility(List, ScrollBarVisibility.Auto);
        SetRow(List, 1);
        Children.Add(List);

        List.ContainerContentChanging += (_, args) =>
        {
            if (args.ItemContainer.ContentTemplateRoot is RowView view && args.Item is FileRow row)
                view.Show(this, row);
            args.Handled = true;
        };
        List.DoubleTapped += (_, e) =>
        {
            if (RowAt(e.OriginalSource) is { } row) Activated?.Invoke(row);
        };
        List.KeyDown += OnKeyDown;
        List.SelectionChanged += (_, _) =>
        {
            if (!quiet) SelectionChanged?.Invoke();
        };
        List.CharacterReceived += OnCharacter;
        List.ContextRequested += OnContextRequested;
        List.Loaded += (_, _) => FollowHorizontalScroll();
        header.ContextRequested += OnHeaderMenu;
        BuildHeader();
    }

    public IReadOnlyList<FileRow> Rows => items;

    public List<(Column Column, ColumnState State)> VisibleColumns =>
        State.Columns.Where(c => c.Visible)
            .Select(c => (definitions.First(d => d.Key == c.Key), c))
            .ToList();

    public List<FileRow> SelectedRows => List.SelectedItems.OfType<FileRow>().ToList();

    Comparison<FileRow>? Order =>
        searchResults && State.SortKey == ResultOrder ? null : Columns.Sort(State.SortKey, State.Descending);

    /// <summary>The sort the search service applies on its worker (null: the results' own order).</summary>
    public Comparison<FileRow>? WorkerOrder => Order;

    /// <summary>Another folder: every row replaced, the selection reset unless ids are given.</summary>
    public void SetRows(List<FileRow> rows, IEnumerable<long>? select = null) => Apply(Sorted(rows), false, select);

    /// <summary>The same folder again: rows removed, changed and added in place, so the selection and the scroll
    /// position stay (SPEC §5 "As built (M2 task 5)").</summary>
    public void UpdateRows(List<FileRow> rows) => Apply(Sorted(rows), true);

    /// <summary>Search results, in their own order and already sorted by the worker; a new query replaces them, the
    /// same query's live re-run updates them in place (SPEC §6).</summary>
    public void ShowResults(List<FileRow> inResultOrder, List<FileRow> sorted, bool inPlace)
    {
        resultOrder = inResultOrder;
        Apply(sorted, inPlace);
    }

    List<FileRow> Sorted(List<FileRow> rows)
    {
        if (Order is not { } order) return [.. resultOrder];
        var copy = rows.ToList();
        copy.Sort(order);
        return copy;
    }

    void Apply(List<FileRow> ordered, bool inPlace, IEnumerable<long>? select = null)
    {
        quiet = true;
        var before = SelectedRows.Select(r => r.Id).ToList();
        try
        {
            if (inPlace) Update(ordered);
            else
            {
                items.Clear();
                foreach (var row in ordered) items.Add(row);
                if (select is not null) Select(select);
            }
        }
        finally
        {
            quiet = false;
        }
        if (!inPlace || !before.SequenceEqual(SelectedRows.Select(r => r.Id))) SelectionChanged?.Invoke();
    }

    void Update(List<FileRow> ordered)
    {
        var selected = SelectedRows.Select(r => r.Id).ToHashSet();
        var wanted = ordered.Select(r => r.Id).ToHashSet();
        for (var i = items.Count - 1; i >= 0; i--)
            if (!wanted.Contains(items[i].Id))
                items.RemoveAt(i);
        for (var i = 0; i < ordered.Count; i++)
        {
            var row = ordered[i];
            if (i < items.Count && items[i].Id == row.Id)
            {
                if (items[i] != row) items[i] = row;
                continue;
            }
            for (var j = i + 1; j < items.Count; j++)
                if (items[j].Id == row.Id)
                {
                    items.RemoveAt(j);
                    break;
                }
            items.Insert(i, row);
        }
        Select(selected, scroll: false);
    }

    /// <summary>Selects these rows without raising <see cref="SelectionChanged"/>.</summary>
    public void Select(IEnumerable<long> ids, bool scroll = true)
    {
        var wasQuiet = quiet;
        quiet = true;
        try
        {
            SelectQuietly(ids, scroll);
        }
        finally
        {
            quiet = wasQuiet;
        }
    }

    void SelectQuietly(IEnumerable<long> ids, bool scroll)
    {
        var wanted = ids.ToHashSet();
        List.SelectedItems.Clear();
        FileRow? first = null;
        foreach (var row in items)
        {
            if (!wanted.Contains(row.Id)) continue;
            List.SelectedItems.Add(row);
            first ??= row;
        }
        if (first is not null && scroll) List.ScrollIntoView(first);
    }

    public void FocusFirst()
    {
        if (items.Count == 0) return;
        if (List.SelectedItems.Count == 0) List.SelectedIndex = 0;
        List.Focus(FocusState.Keyboard);
    }

    static FileRow? RowAt(object source) =>
        source is FrameworkElement element ? element.DataContext as FileRow : null;

    void OnKeyDown(object sender, KeyRoutedEventArgs e)
    {
        switch (e.Key)
        {
            case VirtualKey.Enter when List.SelectedItem is FileRow row:
                Activated?.Invoke(row);
                e.Handled = true;
                break;
            case VirtualKey.Back:
                UpRequested?.Invoke();
                e.Handled = true;
                break;
        }
    }

    /// <summary>Typing selects by name (SPEC §5): the letters typed within a second make one prefix.</summary>
    void OnCharacter(UIElement sender, CharacterReceivedRoutedEventArgs e)
    {
        if (char.IsControl(e.Character)) return;
        var now = Environment.TickCount64;
        typed = now - typedAt > 1000 ? e.Character.ToString() : typed + e.Character;
        typedAt = now;
        var start = typed.Length == 1 ? List.SelectedIndex + 1 : Math.Max(0, List.SelectedIndex);
        for (var n = 0; n < items.Count; n++)
        {
            var i = (start + n) % items.Count;
            if (!items[i].Name.StartsWith(typed, StringComparison.CurrentCultureIgnoreCase)) continue;
            List.SelectedItems.Clear();
            List.SelectedIndex = i;
            List.ScrollIntoView(items[i]);
            break;
        }
        e.Handled = true;
    }

    void OnContextRequested(UIElement sender, ContextRequestedEventArgs e)
    {
        var row = RowAt(e.OriginalSource);
        if (row is not null && !List.SelectedItems.Contains(row))
        {
            List.SelectedItems.Clear();
            List.SelectedItems.Add(row);
        }
        var rows = row is null ? [] : SelectedRows;
        var point = e.TryGetPosition(List, out var p) ? p : new Windows.Foundation.Point(0, 0);
        MenuRequested?.Invoke(rows, List, point);
        e.Handled = true;
    }

    // -- the header --

    void BuildHeader()
    {
        header.Children.Clear();
        header.ColumnDefinitions.Clear();
        var visible = VisibleColumns;
        header.Width = visible.Sum(c => c.State.Width);
        for (var i = 0; i < visible.Count; i++)
        {
            var (column, state) = visible[i];
            header.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(state.Width) });
            var cell = HeaderCell(column, state);
            SetColumn(cell, i);
            header.Children.Add(cell);
        }
    }

    Grid HeaderCell(Column column, ColumnState state)
    {
        var cell = new Grid { Background = new SolidColorBrush(Microsoft.UI.Colors.Transparent) };
        var sorted = State.SortKey == column.Key;
        var text = new TextBlock
        {
            Text = column.Header + (sorted ? State.Descending ? "  ▾" : "  ▴" : ""),
            VerticalAlignment = VerticalAlignment.Center,
            TextTrimming = TextTrimming.CharacterEllipsis,
            Padding = new Thickness(8, 0, 8, 0),
            TextAlignment = column.AlignRight ? TextAlignment.Right : TextAlignment.Left,
            Foreground = (Brush)Application.Current.Resources["TextFillColorSecondaryBrush"],
        };
        cell.Children.Add(text);
        var grip = new Grip { Width = 8, HorizontalAlignment = HorizontalAlignment.Right };
        cell.Children.Add(grip);

        // A click sorts; a drag moves the column.
        Windows.Foundation.Point? down = null;
        var dragging = false;
        cell.PointerPressed += (_, e) =>
        {
            if (!e.GetCurrentPoint(cell).Properties.IsLeftButtonPressed) return;
            down = e.GetCurrentPoint(header).Position;
            dragging = false;
            cell.CapturePointer(e.Pointer);
        };
        cell.PointerMoved += (_, e) =>
        {
            if (down is not { } start) return;
            if (!dragging && Math.Abs(e.GetCurrentPoint(header).Position.X - start.X) > 6)
            {
                dragging = true;
                cell.Opacity = 0.5;
            }
        };
        cell.PointerReleased += (_, e) =>
        {
            if (down is null) return;
            down = null;
            cell.ReleasePointerCapture(e.Pointer);
            cell.Opacity = 1;
            if (dragging) MoveColumn(column.Key, e.GetCurrentPoint(header).Position.X);
            else SortBy(column.Key);
        };

        // The grip resizes.
        double startX = 0, startWidth = 0;
        var resizing = false;
        grip.PointerPressed += (_, e) =>
        {
            resizing = true;
            startX = e.GetCurrentPoint(header).Position.X;
            startWidth = state.Width;
            grip.CapturePointer(e.Pointer);
            e.Handled = true;
        };
        grip.PointerMoved += (_, e) =>
        {
            if (!resizing) return;
            state.Width = Math.Max(MinColumnWidth, startWidth + e.GetCurrentPoint(header).Position.X - startX);
            Relayout(rebuildHeader: false);
            var index = VisibleColumns.FindIndex(c => c.State == state);
            header.ColumnDefinitions[index].Width = new GridLength(state.Width);
            header.Width = VisibleColumns.Sum(c => c.State.Width);
            e.Handled = true;
        };
        grip.PointerReleased += (_, e) =>
        {
            if (!resizing) return;
            resizing = false;
            grip.ReleasePointerCapture(e.Pointer);
            LayoutChanged?.Invoke();
            e.Handled = true;
        };
        grip.DoubleTapped += (_, e) => e.Handled = true;  // not a sort
        return cell;
    }

    void SortBy(string key)
    {
        if (State.SortKey == key) State.Descending = !State.Descending;
        else
        {
            State.SortKey = key;
            State.Descending = false;
        }
        var selected = SelectedRows.Select(r => r.Id).ToList();
        Apply(Sorted(searchResults ? resultOrder : items.ToList()), false, selected);
        BuildHeader();
        LayoutChanged?.Invoke();
    }

    void MoveColumn(string key, double x)
    {
        var visible = VisibleColumns;
        double left = 0;
        var target = visible.Count;  // past every column's middle: the end
        for (var i = 0; i < visible.Count; i++)
        {
            if (x < left + visible[i].State.Width / 2)
            {
                target = i;
                break;
            }
            left += visible[i].State.Width;
        }
        var moving = State.Columns.First(c => c.Key == key);
        var before = target < visible.Count ? visible[target].State : null;
        if (before == moving) return;
        State.Columns.Remove(moving);
        var at = before is null ? State.Columns.Count : State.Columns.IndexOf(before);
        if (at < 0) at = State.Columns.Count;
        State.Columns.Insert(at, moving);
        Relayout(rebuildHeader: true);
        LayoutChanged?.Invoke();
    }

    void OnHeaderMenu(UIElement sender, ContextRequestedEventArgs e)
    {
        var menu = new MenuFlyout();
        foreach (var state in State.Columns)
        {
            var column = definitions.First(d => d.Key == state.Key);
            var item = new ToggleMenuFlyoutItem
            {
                Text = column.Header == "#" ? "# (track number)" : column.Header,
                IsChecked = state.Visible,
                IsEnabled = column.Key != "name",  // the Name column always shows
            };
            item.Click += (_, _) =>
            {
                state.Visible = item.IsChecked;
                Relayout(rebuildHeader: true);
                LayoutChanged?.Invoke();
            };
            menu.Items.Add(item);
        }
        menu.Items.Add(new MenuFlyoutSeparator());
        var reset = new MenuFlyoutItem { Text = "Reset columns" };
        reset.Click += (_, _) =>
        {
            State = Columns.Defaults(definitions);
            if (searchResults) State.SortKey = ResultOrder;
            Apply(Sorted(searchResults ? resultOrder : items.ToList()), false, SelectedRows.Select(r => r.Id).ToList());
            Relayout(rebuildHeader: true);
            LayoutChanged?.Invoke();
        };
        menu.Items.Add(reset);
        if (e.TryGetPosition(header, out var point)) menu.ShowAt(header, point);
        else menu.ShowAt(header);
        e.Handled = true;
    }

    /// <summary>The columns changed: every realized row takes the new layout.</summary>
    void Relayout(bool rebuildHeader)
    {
        LayoutVersion++;
        if (rebuildHeader) BuildHeader();
        if (List.ItemsPanelRoot is null) return;
        foreach (var child in List.ItemsPanelRoot.Children)
            if (child is ListViewItem { ContentTemplateRoot: RowView view, Content: FileRow row })
                view.Show(this, row);
    }

    /// <summary>The header scrolls sideways with the list, and starts where the rows' content starts.</summary>
    void FollowHorizontalScroll()
    {
        if (FindChild<ScrollViewer>(List) is not { } scroll) return;
        scroll.ViewChanging += (_, e) =>
            headerScroll.ChangeView(e.NextView.HorizontalOffset, null, null, disableAnimation: true);
        List.ContainerContentChanging += AlignHeader;
    }

    void AlignHeader(ListViewBase sender, ContainerContentChangingEventArgs args)
    {
        if (args.ItemContainer.ContentTemplateRoot is not RowView view) return;
        view.Loaded += (_, _) =>
        {
            var offset = view.TransformToVisual(List).TransformPoint(new Windows.Foundation.Point(0, 0)).X;
            var scroll = FindChild<ScrollViewer>(List);
            header.Margin = new Thickness(offset + (scroll?.HorizontalOffset ?? 0), 0, 0, 0);
        };
        List.ContainerContentChanging -= AlignHeader;
    }

    static T? FindChild<T>(DependencyObject parent) where T : DependencyObject
    {
        for (var i = 0; i < VisualTreeHelper.GetChildrenCount(parent); i++)
        {
            var child = VisualTreeHelper.GetChild(parent, i);
            if (child is T found) return found;
            if (FindChild<T>(child) is { } deeper) return deeper;
        }
        return null;
    }

    /// <summary>The header's resize handle, with the sizing cursor.</summary>
    sealed partial class Grip : Grid
    {
        public Grip()
        {
            Background = new SolidColorBrush(Microsoft.UI.Colors.Transparent);
            ProtectedCursor = InputSystemCursor.Create(InputSystemCursorShape.SizeWestEast);
        }
    }
}
