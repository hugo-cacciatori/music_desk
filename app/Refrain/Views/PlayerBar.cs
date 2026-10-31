using Microsoft.UI.Text;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Controls.Primitives;
using Microsoft.UI.Xaml.Media;
using Refrain.Core.Playback;
using Refrain.Core.Search;

namespace Refrain.Views;

/// <summary>The player's bar along the bottom of the Search window, below everything, there even when nothing plays
/// (SPEC §37, a first draft): the song's title and album, previous, play or pause, next, a seek bar with its times, and
/// the volume. A click on the title takes you to the song, inside its album's bubble.</summary>
public sealed partial class PlayerBar : UserControl
{
    readonly Player player;
    readonly TextBlock title = new() { FontWeight = FontWeights.SemiBold, TextTrimming = TextTrimming.CharacterEllipsis, MaxLines = 1 };
    readonly TextBlock detail = new() { FontSize = 12, TextTrimming = TextTrimming.CharacterEllipsis, MaxLines = 2, TextWrapping = TextWrapping.Wrap };
    readonly Button song = new()
    {
        HorizontalAlignment = HorizontalAlignment.Stretch, HorizontalContentAlignment = HorizontalAlignment.Left,
        Background = new SolidColorBrush(Microsoft.UI.Colors.Transparent), BorderThickness = new Thickness(0), Padding = new Thickness(8, 4, 8, 4),
    };
    readonly Button previous = Round("", "Previous song (or this one from its start)");
    readonly Button play = Round("", "Play", 40);
    readonly Button next = Round("", "Next song");
    readonly Slider seek = new() { Minimum = 0, Maximum = 1, StepFrequency = 0.1, IsThumbToolTipEnabled = false, VerticalAlignment = VerticalAlignment.Center };
    readonly TextBlock elapsed = new() { FontSize = 11, VerticalAlignment = VerticalAlignment.Center, MinWidth = 36, TextAlignment = TextAlignment.Right };
    readonly TextBlock total = new() { FontSize = 11, VerticalAlignment = VerticalAlignment.Center, MinWidth = 36 };
    readonly Slider volume = new() { Minimum = 0, Maximum = 100, Width = 110, VerticalAlignment = VerticalAlignment.Center };
    bool updating;

    /// <summary>The title was clicked: open the song in the discography view.</summary>
    public event Action<PlayerSong>? SongClicked;

    public PlayerBar(Player player)
    {
        this.player = player;
        var info = new StackPanel { Spacing = 1 };
        info.Children.Add(title);
        info.Children.Add(detail);
        song.Content = info;
        ToolTipService.SetToolTip(song, "Show this song in its album");

        var buttons = new StackPanel { Orientation = Orientation.Horizontal, Spacing = 6, HorizontalAlignment = HorizontalAlignment.Center };
        buttons.Children.Add(previous);
        buttons.Children.Add(play);
        buttons.Children.Add(next);
        var timeline = new Grid { ColumnSpacing = 8 };
        timeline.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });
        timeline.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
        timeline.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });
        timeline.Children.Add(elapsed);
        Grid.SetColumn(seek, 1);
        timeline.Children.Add(seek);
        Grid.SetColumn(total, 2);
        timeline.Children.Add(total);
        var middle = new StackPanel { Spacing = 0, VerticalAlignment = VerticalAlignment.Center };
        middle.Children.Add(buttons);
        middle.Children.Add(timeline);

        var sound = new StackPanel { Orientation = Orientation.Horizontal, Spacing = 6, VerticalAlignment = VerticalAlignment.Center, HorizontalAlignment = HorizontalAlignment.Right };
        sound.Children.Add(new FontIcon { Glyph = "", FontSize = 14 });
        sound.Children.Add(volume);
        ToolTipService.SetToolTip(volume, "Volume");

        var row = new Grid { ColumnSpacing = 12, Padding = new Thickness(4, 6, 12, 6) };
        row.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
        row.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(2, GridUnitType.Star), MaxWidth = 620 });
        row.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
        row.Children.Add(song);
        Grid.SetColumn(middle, 1);
        row.Children.Add(middle);
        Grid.SetColumn(sound, 2);
        row.Children.Add(sound);
        Content = new Border
        {
            Child = row, BorderThickness = new Thickness(0, 1, 0, 0), BorderBrush = Ui.Brush("CardStrokeColorDefaultBrush"),
            Margin = new Thickness(-8, 0, -8, 0),
        };

        volume.Value = player.Volume * 100;
        song.Click += (_, _) =>
        {
            if (player.Current is { } current) SongClicked?.Invoke(current);
        };
        play.Click += (_, _) => player.Toggle();
        previous.Click += (_, _) => player.Step(-1);
        next.Click += (_, _) => player.Step(1);
        seek.ValueChanged += (_, e) =>
        {
            if (!updating) player.Seek(TimeSpan.FromSeconds(e.NewValue));
        };
        volume.ValueChanged += (_, e) => player.Volume = e.NewValue / 100;
        player.Changed += Refresh;
        player.Ticked += Tick;
        Refresh();
    }

    static Button Round(string glyph, string tip, double size = 32)
    {
        var button = new Button
        {
            Content = new FontIcon { Glyph = glyph, FontSize = size * 0.4 }, Width = size, Height = size, Padding = new Thickness(0),
            CornerRadius = new CornerRadius(size / 2), VerticalAlignment = VerticalAlignment.Center,
        };
        ToolTipService.SetToolTip(button, tip);
        return button;
    }

    void Refresh()
    {
        var current = player.Current;
        title.Text = current?.Title ?? "Nothing playing";
        detail.Text = player.Notice ?? (current is null ? "Choose a song in the discography view." : $"{current.Album} · {current.Artist}");
        detail.Foreground = player.Notice is null ? Ui.Brush("TextFillColorSecondaryBrush") : Ui.Brush("SystemFillColorCautionBrush");
        song.IsEnabled = current is not null;
        foreach (var control in new Control[] { previous, play, next, seek }) control.IsEnabled = current is not null;
        ((FontIcon)play.Content).Glyph = player.Playing ? "" : "";
        ToolTipService.SetToolTip(play, player.Playing ? "Pause" : "Play");
        updating = true;
        seek.Maximum = Math.Max(1, player.Duration.TotalSeconds);
        updating = false;
        total.Text = player.Duration > TimeSpan.Zero ? SongInfo.Length(player.Duration.TotalSeconds) : "";
        Tick();
    }

    void Tick()
    {
        updating = true;
        seek.Value = Math.Min(seek.Maximum, player.Position.TotalSeconds);
        updating = false;
        elapsed.Text = player.Current is null ? "" : SongInfo.Length(player.Position.TotalSeconds);
    }
}
