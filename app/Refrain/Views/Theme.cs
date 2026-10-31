using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Media;
using Windows.UI;
using Windows.UI.ViewManagement;

namespace Refrain.Views;

/// <summary>The app's one theme module, the only source of the look (SPEC §36 "11.", Hugo 2026-10-30): the soap-bubble
/// palette, light and transparent, never deep blue, and its dusk variant in dark mode. <see cref="Apply"/> recolours
/// WinUI's own resources with it, so every window and control keeps WinUI's shapes and behaviour in these colours;
/// High Contrast keeps Windows' colours.</summary>
public static class Theme
{
    public sealed record Palette(Color Background, Color Text, Color Secondary, Color Line, Color Fill, Color FillEdge,
        Color Rim, Color Sheen1, Color Sheen2, Color Highlight, Color Lit);

    static readonly Palette Light = new(
        Background: Rgb(0xF3, 0xF8, 0xFA), Text: Rgb(0x34, 0x4E, 0x5E),
        Secondary: Rgb(0x6A, 0x85, 0x95), Line: Color.FromArgb(150, 0x96, 0xB9, 0xD2),
        Fill: Color.FromArgb(115, 255, 255, 255), FillEdge: Color.FromArgb(70, 0xC8, 0xB4, 0xE6),
        Rim: Color.FromArgb(180, 0xA0, 0xBE, 0xD7), Sheen1: Color.FromArgb(120, 0xF2, 0xB8, 0xD8),
        Sheen2: Color.FromArgb(120, 0x9E, 0xDC, 0xD5), Highlight: Color.FromArgb(215, 255, 255, 255),
        Lit: Color.FromArgb(220, 0xBE, 0xE1, 0xEB));

    static readonly Palette Dusk = new(
        Background: Rgb(0x17, 0x21, 0x29), Text: Rgb(0xD6, 0xE4, 0xEC),
        Secondary: Rgb(0x93, 0xAB, 0xB9), Line: Color.FromArgb(120, 0x7F, 0xA4, 0xBE),
        Fill: Color.FromArgb(28, 255, 255, 255), FillEdge: Color.FromArgb(55, 0xA8, 0x94, 0xD6),
        Rim: Color.FromArgb(170, 0x8F, 0xB4, 0xCF), Sheen1: Color.FromArgb(110, 0xE0, 0x9C, 0xC6),
        Sheen2: Color.FromArgb(110, 0x7F, 0xCE, 0xC6), Highlight: Color.FromArgb(150, 255, 255, 255),
        Lit: Color.FromArgb(150, 0x8F, 0xC9, 0xD8));

    public static Palette For(ElementTheme theme) => theme == ElementTheme.Dark ? Dusk : Light;

    /// <summary>The palette's teal, from light to deep: the accent of every control (Tidy's filled button, selections,
    /// focus lines, toggles, the waveform). The deep shades carry white text in light mode (Dark1: 4.8:1), the light
    /// ones dark text at dusk.</summary>
    static readonly Color[] Teal =
    [
        Rgb(0xC4, 0xEA, 0xE6), Rgb(0x8E, 0xD3, 0xCC), Rgb(0x5C, 0xB8, 0xB0),  // Light3, Light2, Light1
        Rgb(0x3A, 0x9C, 0x95),                                                // the accent
        Rgb(0x2A, 0x7F, 0x79), Rgb(0x1F, 0x66, 0x61), Rgb(0x15, 0x4D, 0x49),  // Dark1, Dark2, Dark3
    ];

    /// <summary>How many of WinUI's brushes the theme recoloured at start (the agent's theme check logs it).</summary>
    public static int Recoloured { get; private set; }

    /// <summary>Recolours WinUI's theme resources at start, before any window. Measured (2026-10-31): an override by
    /// key reaches a lookup by key (<see cref="Ui.Resource{T}"/>) only from a merged dictionary after WinUI's, and the
    /// controls never: their brushes are WinUI's own, one instance per key for every theme, coloured once from WinUI's
    /// colour keys. So: the palette's colours, their brushes, the accent and the app's own keys (<c>Refrain…Brush</c>)
    /// go in that merged dictionary; then WinUI's brushes in the current theme's colours take the palette's (in place),
    /// and WinUI's colour keys are replaced too, for a theme switched while the app runs (inferred, unproven). High
    /// Contrast keeps Windows' colours.</summary>
    public static void Apply(Application app)
    {
        var resources = app.Resources;
        var winui = resources.MergedDictionaries.FirstOrDefault(m => m is Microsoft.UI.Xaml.Controls.XamlControlsResources);
        var theme = new ResourceDictionary();
        theme.ThemeDictionaries["Light"] = Dictionary(Light, dark: false, out var lightColors);
        theme.ThemeDictionaries["Dark"] = Dictionary(Dusk, dark: true, out var darkColors);
        theme.ThemeDictionaries["HighContrast"] = HighContrast();
        // The accent first: WinUI's brushes, once read or written, keep the accent they found (measured: WinUI's blue).
        resources.MergedDictionaries.Add(theme);
        if (winui is null || new AccessibilitySettings().HighContrast) return;
        var (winuiLight, winuiDark) = (Themed(winui, "Light"), Themed(winui, "Default"));  // WinUI's dark is "Default"
        var (colors, current) = app.RequestedTheme == ApplicationTheme.Dark ? (darkColors, winuiDark) : (lightColors, winuiLight);
        if (current is null) return;
        var replaced = new Dictionary<Color, Color>();
        foreach (var (name, color) in colors)
            if (current.TryGetValue(name, out var was) && was is Color old) replaced.TryAdd(old, color);
        Recoloured = Recolour(current, replaced);
        foreach (var (target, palette) in new[] { (winuiLight, lightColors), (winuiDark, darkColors) })
            foreach (var (name, color) in palette)
                if (target is not null && target.TryGetValue(name, out var was) && was is Color)
                    target[name] = color;
    }

    static ResourceDictionary? Themed(ResourceDictionary? winui, string theme) =>
        winui is not null && winui.ThemeDictionaries.TryGetValue(theme, out var d) ? As<ResourceDictionary>(d) : null;

    /// <summary>One theme's colours: each of WinUI's base colours the palette replaces, with its brush (also listed
    /// in <paramref name="colors"/>), the accent, and the app's own keys.</summary>
    static ResourceDictionary Dictionary(Palette p, bool dark, out List<(string Name, Color Color)> colors)
    {
        var d = new ResourceDictionary();
        var list = new List<(string, Color)>();
        void Set(string name, Color color)
        {
            d[name] = color;
            d[name + "Brush"] = new SolidColorBrush(color);
            list.Add((name, color));
        }
        var white = Color.FromArgb(255, 255, 255, 255);
        Color Of(Color c, byte alpha) => Color.FromArgb(alpha, c.R, c.G, c.B);
        Color blueGrey = Rgb(p.Rim.R, p.Rim.G, p.Rim.B);

        Set("TextFillColorPrimary", p.Text);
        Set("TextFillColorSecondary", p.Secondary);
        Set("TextFillColorTertiary", Of(p.Secondary, dark ? (byte)0xB0 : (byte)0xC0));
        Set("TextFillColorDisabled", Of(p.Secondary, dark ? (byte)0x70 : (byte)0x80));

        // Surfaces: translucent white over the pale background (a faint glow at dusk), soft blue-grey outlines.
        Set("SolidBackgroundFillColorBase", p.Background);
        Set("SolidBackgroundFillColorSecondary", dark ? Rgb(0x12, 0x1A, 0x21) : Rgb(0xEA, 0xF2, 0xF5));
        Set("SolidBackgroundFillColorTertiary", dark ? Rgb(0x1D, 0x28, 0x32) : Rgb(0xF8, 0xFB, 0xFC));
        Set("SolidBackgroundFillColorQuarternary", dark ? Rgb(0x24, 0x30, 0x3A) : white);
        Set("LayerFillColorDefault", Of(white, dark ? (byte)0x0A : (byte)0x8C));
        Set("LayerFillColorAlt", Of(white, dark ? (byte)0x10 : (byte)0xB8));
        Set("CardBackgroundFillColorDefault", Of(white, dark ? (byte)0x12 : (byte)0xB0));
        Set("CardBackgroundFillColorSecondary", Of(white, dark ? (byte)0x0A : (byte)0x80));
        Set("CardStrokeColorDefault", Of(blueGrey, dark ? (byte)0x40 : (byte)0x66));
        Set("DividerStrokeColorDefault", Of(blueGrey, dark ? (byte)0x38 : (byte)0x55));
        Set("SurfaceStrokeColorDefault", Of(blueGrey, dark ? (byte)0x50 : (byte)0x77));
        Set("SurfaceStrokeColorFlyout", Of(blueGrey, dark ? (byte)0x40 : (byte)0x55));

        // Controls: the bubbles' clear fill, a little whiter under the pointer, outlines in the rim's blue-grey.
        Set("ControlFillColorDefault", Of(white, dark ? (byte)0x14 : (byte)0xB8));
        Set("ControlFillColorSecondary", Of(white, dark ? (byte)0x1E : (byte)0xE0));
        Set("ControlFillColorTertiary", Of(white, dark ? (byte)0x0C : (byte)0x70));
        Set("ControlFillColorDisabled", Of(white, dark ? (byte)0x0A : (byte)0x50));
        Set("ControlFillColorInputActive", dark ? Rgb(0x1E, 0x2A, 0x34) : white);
        Set("ControlStrokeColorDefault", Of(blueGrey, dark ? (byte)0x30 : (byte)0x60));
        Set("ControlStrokeColorSecondary", Of(blueGrey, dark ? (byte)0x50 : (byte)0x90));
        Set("ControlStrongStrokeColorDefault", Of(p.Secondary, dark ? (byte)0xA0 : (byte)0xB0));
        Set("ControlStrongFillColorDefault", Of(p.Secondary, dark ? (byte)0xA0 : (byte)0xB0));
        Set("SubtleFillColorSecondary", Of(blueGrey, dark ? (byte)0x22 : (byte)0x2A));
        Set("SubtleFillColorTertiary", Of(blueGrey, dark ? (byte)0x16 : (byte)0x1C));

        // The accent: the palette's teal, in every shade WinUI derives its accent brushes from.
        string[] shades = ["SystemAccentColorLight3", "SystemAccentColorLight2", "SystemAccentColorLight1", "SystemAccentColor",
            "SystemAccentColorDark1", "SystemAccentColorDark2", "SystemAccentColorDark3"];
        for (var i = 0; i < shades.Length; i++) d[shades[i]] = Teal[i];
        if (dark)
        {
            Set("AccentFillColorDefault", Teal[1]);
            Set("AccentFillColorSecondary", Of(Teal[1], 0xE6));
            Set("AccentFillColorTertiary", Of(Teal[1], 0xCC));
            Set("AccentTextFillColorPrimary", Teal[0]);
            Set("TextOnAccentFillColorPrimary", Rgb(0x10, 0x2A, 0x28));
        }
        else
        {
            Set("AccentFillColorDefault", Teal[4]);
            Set("AccentFillColorSecondary", Of(Teal[4], 0xE6));
            Set("AccentFillColorTertiary", Of(Teal[4], 0xCC));
            Set("AccentTextFillColorPrimary", Teal[5]);
            Set("TextOnAccentFillColorPrimary", white);
        }

        // The app's own: the window behind everything (the pale background with a faint pink and teal sheen across
        // it), and the search's matched letters (the bubbles' pink).
        var window = new LinearGradientBrush { StartPoint = new Windows.Foundation.Point(0, 0), EndPoint = new Windows.Foundation.Point(1, 1) };
        window.GradientStops.Add(new GradientStop { Color = Blend(p.Background, p.Sheen1, dark ? 0.05 : 0.10), Offset = 0 });
        window.GradientStops.Add(new GradientStop { Color = p.Background, Offset = 0.45 });
        window.GradientStops.Add(new GradientStop { Color = Blend(p.Background, p.Sheen2, dark ? 0.06 : 0.14), Offset = 1 });
        d["RefrainWindowBrush"] = window;
        d["RefrainMatchBrush"] = new SolidColorBrush(Of(p.Sheen1, dark ? (byte)0x70 : (byte)0xA0));
        colors = list;
        return d;
    }

    /// <summary>Every brush of WinUI's whose colour the palette replaces takes the palette's colour, gradients (the
    /// controls' raised edges) stop by stop; the rest (the accent's, the status colours) stay WinUI's. The controls'
    /// own keys (ButtonBackground…) share these brushes, so each is changed once.</summary>
    static int Recolour(ResourceDictionary winui, Dictionary<Color, Color> replaced)
    {
        var done = new HashSet<object>(ReferenceEqualityComparer.Instance);
        foreach (var value in winui.Values)
        {
            if (value is null or Color || !done.Add(value)) continue;
            if (As<SolidColorBrush>(value) is { } solid && replaced.TryGetValue(solid.Color, out var color))
            {
                solid.Color = color;
            }
            else if (As<GradientBrush>(value) is { } gradient && gradient.GradientStops.Any(s => replaced.ContainsKey(s.Color)))
            {
                foreach (var stop in gradient.GradientStops) stop.Color = replaced.GetValueOrDefault(stop.Color, stop.Color);
            }
            else
            {
                done.Remove(value);
            }
        }
        return done.Count;
    }

    /// <summary>A resource as <typeparamref name="T"/>, or null. Under Native AOT a value can come back as a bare object
    /// that only answers to As&lt;T&gt; (<see cref="Ui.Resource{T}"/>).</summary>
    static T? As<T>(object? value) where T : class
    {
        if (value is T typed) return typed;
        if (value is not WinRT.IWinRTObject) return null;
        try
        {
            return WinRT.CastExtensions.As<T>(value);
        }
        catch (InvalidCastException)
        {
            return null;
        }
    }

    /// <summary>High Contrast keeps every WinUI colour; the app's own keys take Windows' contrast colours.</summary>
    static ResourceDictionary HighContrast()
    {
        var settings = new UISettings();
        return new ResourceDictionary
        {
            ["RefrainWindowBrush"] = new SolidColorBrush(settings.UIElementColor(UIElementType.Window)),
            ["RefrainMatchBrush"] = new SolidColorBrush(settings.UIElementColor(UIElementType.Highlight)),
        };
    }

    static Color Rgb(byte r, byte g, byte b) => Color.FromArgb(255, r, g, b);

    /// <summary><paramref name="tint"/> laid over <paramref name="ground"/> at <paramref name="amount"/> of its own
    /// opacity, as one solid colour.</summary>
    static Color Blend(Color ground, Color tint, double amount)
    {
        var a = amount * tint.A / 255;
        byte Mix(byte g, byte t) => (byte)Math.Round(g + (t - g) * a);
        return Rgb(Mix(ground.R, tint.R), Mix(ground.G, tint.G), Mix(ground.B, tint.B));
    }
}
