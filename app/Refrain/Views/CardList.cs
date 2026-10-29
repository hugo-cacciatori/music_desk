using System.Collections.ObjectModel;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Documents;
using Microsoft.UI.Xaml.Input;
using Refrain.Core.Index;
using Windows.System;

namespace Refrain.Views;

/// <summary>What the window needs of a list of rows: the file list, the search table and the lyrics cards.</summary>
public interface IRowList
{
    List<FileRow> SelectedRows { get; }
    void Select(IEnumerable<long> ids, bool scroll = true);
    void FocusFirst();
    event Action? SelectionChanged;
}

/// <summary>Lyrics search's results as cards (SPEC §6 "Direction task 6", §36 "6."): the title in bold, the artist and
/// album dimmed, then three lines of lyrics around the match, highlighted. A click selects a card (the side panel
/// shows its lyrics); a double-click or Enter goes to its folder.</summary>
public sealed partial class CardList : Grid, IRowList
{
    readonly ObservableCollection<FileRow> items = [];
    bool quiet;

    public ListView List { get; }
    public Microsoft.UI.Xaml.Media.Brush HighlightBrush { get; } = Ui.Brush("SystemFillColorCautionBackgroundBrush");

    public event Action<FileRow>? Activated;
    public event Action<IReadOnlyList<FileRow>, UIElement, Windows.Foundation.Point>? MenuRequested;
    public event Action? SelectionChanged;
    /// <summary>A card was clicked: its lyrics come forward in the side panel.</summary>
    public event Action? Clicked;

    public CardList()
    {
        List = new ListView
        {
            SelectionMode = ListViewSelectionMode.Extended,
            ItemsSource = items,
            ItemTemplate = (DataTemplate)Application.Current.Resources["CardTemplate"],
            ItemContainerStyle = (Style)Application.Current.Resources["CardItemStyle"],
            Padding = new Thickness(4),
        };
        Children.Add(List);
        List.ContainerContentChanging += (_, args) =>
        {
            if (args.ItemContainer.ContentTemplateRoot is CardView view && args.Item is FileRow row) view.Show(row, HighlightBrush);
            args.Handled = true;
        };
        List.SelectionChanged += (_, _) =>
        {
            if (!quiet) SelectionChanged?.Invoke();
        };
        List.Tapped += (_, _) => Clicked?.Invoke();
        List.DoubleTapped += (_, e) =>
        {
            if (FileTable.RowAt(List, e.OriginalSource) is { } row) Activated?.Invoke(row);
        };
        List.KeyDown += (_, e) =>
        {
            if (e.Key != VirtualKey.Enter || List.SelectedItem is not FileRow row) return;
            e.Handled = true;
            Activated?.Invoke(row);
        };
        List.ContextRequested += (_, e) =>
        {
            var row = FileTable.RowAt(List, e.OriginalSource);
            if (row is not null && !List.SelectedItems.Contains(row))
            {
                List.SelectedItems.Clear();
                List.SelectedItems.Add(row);
            }
            var point = e.TryGetPosition(List, out var p) ? p : new Windows.Foundation.Point(0, 0);
            MenuRequested?.Invoke(row is null ? [] : SelectedRows, List, point);
            e.Handled = true;
        };
    }

    public List<FileRow> SelectedRows => List.SelectedItems.OfType<FileRow>().ToList();

    /// <summary>A new query replaces the cards; its live re-run updates them in place, keeping the selection.</summary>
    public void SetRows(List<FileRow> rows, bool inPlace)
    {
        var before = SelectedRows.Select(r => r.Id).ToList();
        quiet = true;
        try
        {
            if (!inPlace)
            {
                items.Clear();
                foreach (var row in rows) items.Add(row);
            }
            else
            {
                var wanted = rows.Select(r => r.Id).ToHashSet();
                for (var i = items.Count - 1; i >= 0; i--)
                    if (!wanted.Contains(items[i].Id))
                        items.RemoveAt(i);
                for (var i = 0; i < rows.Count; i++)
                {
                    if (i < items.Count && items[i].Id == rows[i].Id)
                    {
                        if (items[i] != rows[i]) items[i] = rows[i];
                        continue;
                    }
                    for (var j = i + 1; j < items.Count; j++)
                        if (items[j].Id == rows[i].Id)
                        {
                            items.RemoveAt(j);
                            break;
                        }
                    items.Insert(i, rows[i]);
                }
                Select(before, scroll: false);
            }
        }
        finally
        {
            quiet = false;
        }
        if (!inPlace || !before.SequenceEqual(SelectedRows.Select(r => r.Id))) SelectionChanged?.Invoke();
    }

    public void Select(IEnumerable<long> ids, bool scroll = true)
    {
        var wasQuiet = quiet;
        quiet = true;
        try
        {
            var wanted = ids.ToHashSet();
            List.SelectedItems.Clear();
            FileRow? first = null;
            foreach (var row in items.Where(r => wanted.Contains(r.Id)))
            {
                List.SelectedItems.Add(row);
                first ??= row;
            }
            if (first is not null && scroll) List.ScrollIntoView(first);
        }
        finally
        {
            quiet = wasQuiet;
        }
    }

    public void FocusFirst()
    {
        if (items.Count == 0) return;
        if (List.SelectedItems.Count == 0) List.SelectedIndex = 0;
        List.Focus(FocusState.Keyboard);
    }
}

/// <summary>One card. Every card has the same height, so thousands of results stay quick to scroll.</summary>
public sealed partial class CardView : StackPanel
{
    readonly TextBlock title = new()
    {
        FontSize = 17,
        FontWeight = Microsoft.UI.Text.FontWeights.SemiBold,
        TextTrimming = TextTrimming.CharacterEllipsis,
    };
    readonly TextBlock sub = Ui.Secondary();
    readonly TextBlock extract = new() { FontSize = 15, MaxLines = 3, Height = 66, TextWrapping = TextWrapping.NoWrap, TextTrimming = TextTrimming.CharacterEllipsis };

    public CardView()
    {
        Spacing = 2;
        Padding = new Thickness(12, 10, 12, 10);
        sub.TextWrapping = TextWrapping.NoWrap;
        sub.TextTrimming = TextTrimming.CharacterEllipsis;
        Children.Add(title);
        Children.Add(sub);
        Children.Add(extract);
    }

    public void Show(FileRow row, Microsoft.UI.Xaml.Media.Brush highlight)
    {
        title.Text = row.Title ?? row.Name;
        sub.Text = string.Join(" · ", new[] { row.Artist, row.Album }.Where(s => !string.IsNullOrEmpty(s)));
        extract.Text = row.Extract;
        extract.TextHighlighters.Clear();
        if (row.ExtractSpans.Count > 0)
        {
            var marks = new TextHighlighter { Background = highlight };
            foreach (var (start, length) in row.ExtractSpans) marks.Ranges.Add(new TextRange { StartIndex = start, Length = length });
            extract.TextHighlighters.Add(marks);
        }
        ToolTipService.SetToolTip(this, Path.Combine(row.Folder, row.Name));
    }
}
