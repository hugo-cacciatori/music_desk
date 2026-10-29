using Microsoft.UI.Text;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Documents;
using Microsoft.UI.Xaml.Media;
using Refrain.Core.Browser;
using Refrain.Core.Index;

namespace Refrain.Views;

/// <summary>One row of a <see cref="FileTable"/>: a cell per visible column, rebuilt only when the columns change
/// (containers are recycled, so most calls just set texts).</summary>
public sealed partial class RowView : Grid
{
    FileTable? table;
    int version = -1;
    readonly List<(string Key, TextBlock Text)> cells = [];
    FontIcon? icon;

    public void Show(FileTable owner, FileRow row)
    {
        if (table != owner || version != owner.LayoutVersion) Build(owner);
        Opacity = row.IsDir || row.IsAudio ? 1 : 0.6;  // non-audio files are dimmed (SPEC §5)
        if (icon is not null) icon.Glyph = row.IsDir ? "" : row.IsAudio ? "" : "";
        foreach (var (key, text) in cells)
        {
            text.Text = Columns.Text(row, key);
            text.TextHighlighters.Clear();
            var spans = key switch
            {
                "title" => row.TitleSpans,
                "artist" => row.ArtistSpans,
                _ => [],
            };
            if (spans.Count == 0) continue;
            var highlighter = new TextHighlighter { Background = owner.HighlightBrush };
            foreach (var (start, length) in spans) highlighter.Ranges.Add(new TextRange { StartIndex = start, Length = length });
            text.TextHighlighters.Add(highlighter);
        }
        ToolTipService.SetToolTip(this, row.ReadError);
    }

    void Build(FileTable owner)
    {
        table = owner;
        version = owner.LayoutVersion;
        Children.Clear();
        ColumnDefinitions.Clear();
        cells.Clear();
        icon = null;
        var visible = owner.VisibleColumns;
        Width = visible.Sum(c => c.State.Width);
        for (var i = 0; i < visible.Count; i++)
        {
            var (column, state) = visible[i];
            ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(state.Width) });
            var text = new TextBlock
            {
                TextTrimming = TextTrimming.CharacterEllipsis,
                TextWrapping = TextWrapping.NoWrap,
                VerticalAlignment = VerticalAlignment.Center,
                TextAlignment = column.AlignRight ? TextAlignment.Right : TextAlignment.Left,
                Padding = new Thickness(8, 0, 8, 0),
            };
            FrameworkElement cell = text;
            if (column.Key == "name")
            {
                icon = new FontIcon { FontSize = 14, Margin = new Thickness(4, 0, 0, 0) };
                var panel = new Grid { ColumnSpacing = 0 };
                panel.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });
                panel.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
                panel.Children.Add(icon);
                SetColumn(text, 1);
                panel.Children.Add(text);
                cell = panel;
            }
            if (column.Key == "rg") text.FontWeight = FontWeights.SemiBold;
            SetColumn(cell, i);
            Children.Add(cell);
            cells.Add((column.Key, text));
        }
    }
}
