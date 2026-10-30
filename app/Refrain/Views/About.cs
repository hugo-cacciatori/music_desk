using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Refrain.Core;

namespace Refrain.Views;

/// <summary>About Refrain (SPEC §12, §36 "10."): the version, the data folder, the licence, the components table, and
/// Open licences folder.</summary>
public static class About
{
    public static async Task Show(XamlRoot root)
    {
        var version = typeof(About).Assembly.GetName().Version;
        var content = new StackPanel { Spacing = 12 };
        content.Children.Add(new TextBlock
        {
            Text = $"Version {version?.ToString(3)} (preview of the rebuild)\n\nIts index and settings: {AppPaths.DataFolder}\n\n" +
                   "Licence: GPL-3.0-or-later, with an exception for the Windows App SDK. Refrain ships with these components:",
            TextWrapping = TextWrapping.Wrap,
        });
        // The Python app's About table (SPEC §12): name, version, licence; the texts are in the licences folder.
        var table = new Grid { ColumnSpacing = 16, RowSpacing = 2 };
        foreach (var width in new[] { GridLength.Auto, GridLength.Auto, new GridLength(1, GridUnitType.Star) })
            table.ColumnDefinitions.Add(new ColumnDefinition { Width = width });
        for (var i = 0; i < Licenses.Components.Length; i++)
        {
            var component = Licenses.Components[i];
            table.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });
            string[] cells = [component.Name, component.Version, component.Licence];
            for (var column = 0; column < cells.Length; column++)
            {
                var cell = new TextBlock { Text = cells[column], TextWrapping = TextWrapping.Wrap };
                Grid.SetRow(cell, i);
                Grid.SetColumn(cell, column);
                table.Children.Add(cell);
            }
        }
        content.Children.Add(table);
        var dialog = new ContentDialog
        {
            Title = "Refrain",
            Content = new ScrollViewer { Content = content },
            PrimaryButtonText = "Open licences folder",
            CloseButtonText = "Close",
            DefaultButton = ContentDialogButton.Close,
            XamlRoot = root,
        };
        if (await dialog.ShowAsync() == ContentDialogResult.Primary) Shell.Reveal(Licenses.Folder, select: false);
    }
}
