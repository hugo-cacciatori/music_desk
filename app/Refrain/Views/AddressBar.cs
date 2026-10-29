using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Media;
using Refrain.Core.Index;
using Windows.System;

namespace Refrain.Views;

/// <summary>The address bar (SPEC §5 "As built (M2 task 6)"): a crumb per level from the root's full path down,
/// each with a › dropdown of its subfolders. A click on the empty part, or Ctrl+L, turns it into a text path. When
/// space runs short, the leading crumbs go into a « menu.</summary>
public sealed partial class AddressBar : Grid
{
    readonly StackPanel crumbs = new() { Orientation = Orientation.Horizontal, VerticalAlignment = VerticalAlignment.Center };
    readonly Button overflow = new()
    {
        Content = "«",
        Visibility = Visibility.Collapsed,
        Background = new SolidColorBrush(Microsoft.UI.Colors.Transparent),
        BorderThickness = new Thickness(0),
    };
    readonly TextBox text = new() { Visibility = Visibility.Collapsed, VerticalAlignment = VerticalAlignment.Center };
    List<(long Id, string Name)> chain = [];
    readonly List<UIElement> crumbElements = [];

    /// <summary>A crumb or a dropdown entry: open this folder.</summary>
    public event Action<long>? FolderChosen;
    /// <summary>A typed path, Enter pressed.</summary>
    public event Action<string>? PathTyped;
    /// <summary>A folder's subfolders, for its dropdown (read off the UI thread by the owner).</summary>
    public Func<long, Task<List<FolderNode>>>? Subfolders { get; set; }

    public AddressBar()
    {
        Background = new SolidColorBrush(Microsoft.UI.Colors.Transparent);  // the empty part takes clicks
        BorderBrush = (Brush)Application.Current.Resources["ControlStrokeColorDefaultBrush"];
        BorderThickness = new Thickness(1);
        CornerRadius = new CornerRadius(4);
        MinHeight = 32;
        ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });
        ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
        Children.Add(overflow);
        SetColumn(crumbs, 1);
        Children.Add(crumbs);
        SetColumnSpan(text, 2);
        Children.Add(text);
        ToolTipService.SetToolTip(this, "Click here or press Ctrl+L to type a path");

        Tapped += (_, e) =>
        {
            if (ReferenceEquals(e.OriginalSource, this) || ReferenceEquals(e.OriginalSource, crumbs)) EditText();
        };
        text.KeyDown += (_, e) =>
        {
            if (e.Key == VirtualKey.Enter)
            {
                var typed = text.Text.Trim().Trim('"');
                ShowCrumbs();
                if (typed.Length > 0) PathTyped?.Invoke(typed);
                e.Handled = true;
            }
            else if (e.Key == VirtualKey.Escape)
            {
                ShowCrumbs();
                e.Handled = true;
            }
        };
        text.LostFocus += (_, _) => ShowCrumbs();
        SizeChanged += (_, _) => FitCrumbs();
    }

    string FullPath => chain.Count == 0 ? "" : Path.Combine(chain.Select(c => c.Name).ToArray());

    /// <summary>Shows a folder: its chain from the root's top entry down.</summary>
    public void Show(List<(long Id, string Name)> folders)
    {
        chain = folders;
        crumbs.Children.Clear();
        crumbElements.Clear();
        foreach (var (id, name) in folders)
        {
            var segment = new Button { Content = name, Padding = new Thickness(8, 2, 8, 2) };
            ToolTipService.SetToolTip(segment, $"Open {name}");
            segment.Background = new SolidColorBrush(Microsoft.UI.Colors.Transparent);
            segment.BorderThickness = new Thickness(0);
            var folder = id;
            segment.Click += (_, _) => FolderChosen?.Invoke(folder);
            var dropdown = new Button { Content = "›", Padding = new Thickness(4, 2, 4, 2) };
            dropdown.Background = segment.Background;
            dropdown.BorderThickness = new Thickness(0);
            ToolTipService.SetToolTip(dropdown, "Subfolders");
            dropdown.Click += async (_, _) => await ShowSubfolders(dropdown, folder);
            var pair = new StackPanel { Orientation = Orientation.Horizontal };
            pair.Children.Add(segment);
            pair.Children.Add(dropdown);
            crumbs.Children.Add(pair);
            crumbElements.Add(pair);
        }
        FitCrumbs();
    }

    async Task ShowSubfolders(FrameworkElement anchor, long folderId)
    {
        if (Subfolders is null) return;
        var menu = new MenuFlyout();
        foreach (var sub in await Subfolders(folderId))
        {
            var item = new MenuFlyoutItem { Text = sub.Name };
            var id = sub.Id;
            item.Click += (_, _) => FolderChosen?.Invoke(id);
            menu.Items.Add(item);
        }
        if (menu.Items.Count == 0) menu.Items.Add(new MenuFlyoutItem { Text = "No subfolders", IsEnabled = false });
        menu.Placement = Microsoft.UI.Xaml.Controls.Primitives.FlyoutPlacementMode.BottomEdgeAlignedLeft;
        menu.ShowAt(anchor);
    }

    /// <summary>The leading crumbs that don't fit go into the « menu.</summary>
    void FitCrumbs()
    {
        if (ActualWidth <= 0) return;
        foreach (var element in crumbElements) element.Visibility = Visibility.Visible;
        crumbs.Measure(new Windows.Foundation.Size(double.PositiveInfinity, double.PositiveInfinity));
        var room = ActualWidth - 40;
        var widths = crumbElements.Select(e => e.DesiredSize.Width).ToList();
        var total = widths.Sum();
        var hidden = 0;
        while (total > room && hidden < crumbElements.Count - 1)
        {
            total -= widths[hidden];
            crumbElements[hidden].Visibility = Visibility.Collapsed;
            hidden++;
        }
        overflow.Visibility = hidden > 0 ? Visibility.Visible : Visibility.Collapsed;
        var menu = new MenuFlyout();
        for (var i = hidden - 1; i >= 0; i--)
        {
            var (id, name) = chain[i];
            var item = new MenuFlyoutItem { Text = name };
            item.Click += (_, _) => FolderChosen?.Invoke(id);
            menu.Items.Add(item);
        }
        menu.Placement = Microsoft.UI.Xaml.Controls.Primitives.FlyoutPlacementMode.BottomEdgeAlignedLeft;
        overflow.Flyout = menu;
    }

    /// <summary>Text mode: the full path, selected, ready to be replaced or pasted over.</summary>
    public void EditText()
    {
        text.Text = FullPath;
        text.Visibility = Visibility.Visible;
        crumbs.Visibility = Visibility.Collapsed;
        overflow.Visibility = Visibility.Collapsed;
        text.Focus(FocusState.Keyboard);
        text.SelectAll();
    }

    void ShowCrumbs()
    {
        if (text.Visibility == Visibility.Collapsed) return;
        text.Visibility = Visibility.Collapsed;
        crumbs.Visibility = Visibility.Visible;
        FitCrumbs();
    }
}
