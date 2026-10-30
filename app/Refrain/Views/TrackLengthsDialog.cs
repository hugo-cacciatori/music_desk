using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Refrain.Core.Cue;

namespace Refrain.Views;

/// <summary>Fill from track lengths… (SPEC §22 M13 "4."): a modal dialog with a track count and each track's length but
/// the last's, as a tracklist gives them; track 1 keeps its start, each next track starts where the one before ends.</summary>
static class TrackLengthsDialog
{
    const int MaxTracks = 99;  // what a cue sheet numbers

    /// <summary>The lengths in CD frames of every track but the last, or null when cancelled.</summary>
    public static async Task<List<long>?> Ask(XamlRoot root, IReadOnlyList<long?> lengths, long firstStart, long audioFrames)
    {
        var current = lengths.Count + 1;
        var typed = lengths.Select(n => n is { } frames ? CueDocument.FormatLength(frames) : "").ToList();
        var count = new NumberBox
        {
            Header = "Tracks:", Minimum = 2, Maximum = MaxTracks, Value = Math.Max(2, current),
            SpinButtonPlacementMode = NumberBoxSpinButtonPlacementMode.Inline, Width = 160, HorizontalAlignment = HorizontalAlignment.Left,
        };
        ToolTipService.SetToolTip(count, "How many tracks the sheet gets; the last one runs to the end of the audio");
        var fields = new StackPanel { Spacing = 6 };
        var rest = new TextBlock { TextWrapping = TextWrapping.Wrap };
        var deleted = new TextBlock { TextWrapping = TextWrapping.Wrap };
        var reason = new TextBlock { TextWrapping = TextWrapping.Wrap, Foreground = Ui.Brush("SystemFillColorCriticalBrush") };
        var body = new StackPanel { Spacing = 10, Width = 340 };
        body.Children.Add(new TextBlock
        {
            Text = "Type each track's length, as m:ss or m:ss:ff. Track 1 keeps its start; each next track starts where the one before ends.",
            TextWrapping = TextWrapping.Wrap,
        });
        body.Children.Add(count);
        body.Children.Add(new ScrollViewer { Content = fields, Height = 220 });
        body.Children.Add(rest);
        body.Children.Add(deleted);
        body.Children.Add(reason);
        var dialog = new ContentDialog
        {
            Title = "Fill from track lengths",
            Content = body,
            PrimaryButtonText = "OK",
            CloseButtonText = "Cancel",
            DefaultButton = ContentDialogButton.Primary,
            XamlRoot = root,
        };
        var boxes = new List<TextBox>();
        List<long>? result = null;

        void Check()
        {
            var (found, why) = CueDocument.CheckLengths(boxes.Select(b => b.Text).ToList(), firstStart, audioFrames);
            result = found;
            dialog.IsPrimaryButtonEnabled = found is not null;
            reason.Text = why;
            var last = (int)count.Value;
            rest.Text = found is not null
                ? $"Track {last}: the rest, {CueDocument.FormatLength(audioFrames - firstStart - found.Sum())}."
                : $"Track {last}: the rest of the audio.";
        }

        void Rebuild()
        {
            for (var i = 0; i < boxes.Count && i < typed.Count; i++) typed[i] = boxes[i].Text;
            fields.Children.Clear();
            boxes.Clear();
            var tracks = double.IsNaN(count.Value) ? 2 : (int)Math.Clamp(count.Value, 2, MaxTracks);
            for (var i = 0; i < tracks - 1; i++)
            {
                var box = new TextBox { Header = $"Track {i + 1}:", PlaceholderText = "m:ss", Text = i < typed.Count ? typed[i] : "" };
                ToolTipService.SetToolTip(box, $"Track {i + 1}'s length, as m:ss or m:ss:ff; track {i + 2} starts where it ends");
                box.TextChanged += (_, _) => Check();
                fields.Children.Add(box);
                boxes.Add(box);
            }
            for (var i = typed.Count; i < boxes.Count; i++) typed.Add("");
            var (first, lastTrack) = (tracks + 1, current);
            deleted.Text = tracks < current ? first == lastTrack ? $"Track {first} will be deleted." : $"Tracks {first}–{lastTrack} will be deleted." : "";
            deleted.Visibility = deleted.Text.Length > 0 ? Visibility.Visible : Visibility.Collapsed;
            Check();
        }

        count.ValueChanged += (_, _) => Rebuild();
        Rebuild();
        return await dialog.ShowAsync() == ContentDialogResult.Primary ? result : null;
    }
}
