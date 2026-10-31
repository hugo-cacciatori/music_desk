using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Controls.Primitives;
using Microsoft.UI.Xaml.Media;
using Refrain.Core;

namespace Refrain.Views;

/// <summary>A song's rating (SPEC §37 "Ratings and notes"): five stars by halves (the left half of a star gives a half;
/// a click on the rating shown clears it), and a note behind "…". Saved at once into the app's ratings.</summary>
public sealed partial class Stars : UserControl
{
    const double Size = 20;
    readonly Ratings ratings;
    readonly FontIcon[] filled = new FontIcon[5];
    readonly Button more = new() { Content = "…", Padding = new Thickness(8, 0, 8, 2), MinHeight = 26, VerticalAlignment = VerticalAlignment.Center };
    readonly TextBox note = new() { AcceptsReturn = true, TextWrapping = TextWrapping.Wrap, Width = 280, Height = 130, PlaceholderText = "A note about this song" };
    string? key;
    Rating value = Rating.None;

    public Stars(Ratings ratings)
    {
        this.ratings = ratings;
        var row = new StackPanel { Orientation = Orientation.Horizontal, Spacing = 2 };
        for (var i = 0; i < 5; i++)
        {
            var star = new Grid { Width = Size, Height = Size, Background = new SolidColorBrush(Microsoft.UI.Colors.Transparent) };
            star.Children.Add(new FontIcon { Glyph = "", FontSize = Size - 2, Foreground = Ui.Brush("TextFillColorSecondaryBrush") });
            filled[i] = new FontIcon { Glyph = "", FontSize = Size - 2, Foreground = Ui.Brush("AccentFillColorDefaultBrush") };
            star.Children.Add(filled[i]);
            var place = i;
            star.Tapped += (_, e) =>
            {
                var stars = place + (e.GetPosition(star).X < Size / 2 ? 0.5 : 1);
                Save(value with { Stars = stars == value.Stars ? 0 : stars });
            };
            row.Children.Add(star);
        }
        var flyoutBody = new StackPanel { Spacing = 6 };
        flyoutBody.Children.Add(note);
        flyoutBody.Children.Add(Ui.Secondary("Kept in Refrain, not in the file."));
        var flyout = new Flyout { Content = flyoutBody };
        flyout.Closed += (_, _) =>
        {
            if (note.Text != value.Note) Save(value with { Note = note.Text.Trim() });
        };
        FlyoutBase.SetAttachedFlyout(more, flyout);
        more.Click += (_, _) =>
        {
            note.Text = value.Note;
            FlyoutBase.ShowAttachedFlyout(more);
        };
        more.Margin = new Thickness(8, 0, 0, 0);
        row.Children.Add(more);
        Content = row;
        Visibility = Visibility.Collapsed;
    }

    /// <summary>The song whose rating shows; none (hidden) without an artist and a title to tie it to.</summary>
    public void Load(string? ratingKey)
    {
        key = ratingKey;
        value = key is null ? Rating.None : ratings.Get(key);
        Visibility = key is null ? Visibility.Collapsed : Visibility.Visible;
        Draw();
    }

    void Save(Rating rating)
    {
        if (key is null) return;
        ratings.Set(key, rating);
        value = ratings.Get(key);
        Draw();
    }

    void Draw()
    {
        for (var i = 0; i < 5; i++)
        {
            var share = Math.Clamp(value.Stars - i, 0, 1);
            filled[i].Visibility = share > 0 ? Visibility.Visible : Visibility.Collapsed;
            filled[i].Clip = share is > 0 and < 1 ? new RectangleGeometry { Rect = new Windows.Foundation.Rect(0, 0, Size / 2, Size) } : null;
        }
        ToolTipService.SetToolTip(Content as UIElement, value.Stars > 0 ? $"{value.Stars:0.#} of 5 (click the same star again to clear)" : "Rate this song: the left half of a star gives a half");
        ToolTipService.SetToolTip(more, value.Note.Length > 0 ? value.Note : "Add a note");
        more.Content = value.Note.Length > 0 ? "… note" : "…";
    }
}
