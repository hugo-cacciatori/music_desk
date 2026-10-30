using System.Numerics;
using Microsoft.UI.Composition;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Hosting;
using Microsoft.UI.Xaml.Media;
using Microsoft.UI.Xaml.Shapes;
using Windows.Foundation;
using Windows.UI;

namespace Refrain.Views;

/// <summary>The soap-bubble look (SPEC §36 "11.", Hugo 2026-10-30): light and transparent, relaxing, never deep blue; a
/// dusk variant in dark mode. The bubbles themselves, and their slow drift.</summary>
public static class Bubbles
{
    public sealed record Palette(Color Background, Color Text, Color Secondary, Color Line, Color Fill, Color FillEdge,
        Color Rim, Color Sheen1, Color Sheen2, Color Highlight, Color Lit);

    static readonly Palette Light = new(
        Background: Color.FromArgb(255, 0xF3, 0xF8, 0xFA), Text: Color.FromArgb(255, 0x34, 0x4E, 0x5E),
        Secondary: Color.FromArgb(255, 0x6A, 0x85, 0x95), Line: Color.FromArgb(150, 0x96, 0xB9, 0xD2),
        Fill: Color.FromArgb(115, 255, 255, 255), FillEdge: Color.FromArgb(70, 0xC8, 0xB4, 0xE6),
        Rim: Color.FromArgb(180, 0xA0, 0xBE, 0xD7), Sheen1: Color.FromArgb(120, 0xF2, 0xB8, 0xD8),
        Sheen2: Color.FromArgb(120, 0x9E, 0xDC, 0xD5), Highlight: Color.FromArgb(215, 255, 255, 255),
        Lit: Color.FromArgb(220, 0xBE, 0xE1, 0xEB));

    static readonly Palette Dusk = new(
        Background: Color.FromArgb(255, 0x17, 0x21, 0x29), Text: Color.FromArgb(255, 0xD6, 0xE4, 0xEC),
        Secondary: Color.FromArgb(255, 0x93, 0xAB, 0xB9), Line: Color.FromArgb(120, 0x7F, 0xA4, 0xBE),
        Fill: Color.FromArgb(28, 255, 255, 255), FillEdge: Color.FromArgb(55, 0xA8, 0x94, 0xD6),
        Rim: Color.FromArgb(170, 0x8F, 0xB4, 0xCF), Sheen1: Color.FromArgb(110, 0xE0, 0x9C, 0xC6),
        Sheen2: Color.FromArgb(110, 0x7F, 0xCE, 0xC6), Highlight: Color.FromArgb(150, 255, 255, 255),
        Lit: Color.FromArgb(150, 0x8F, 0xC9, 0xD8));

    public static Palette For(ElementTheme theme) => theme == ElementTheme.Dark ? Dusk : Light;

    /// <summary>Whether Windows' "Animation effects" setting is on: off, nothing drifts and every move is instant.</summary>
    public static bool Animate { get; } = new Windows.UI.ViewManagement.UISettings().AnimationsEnabled;

    /// <summary>A bubble of <paramref name="diameter"/>: a clear, softly tinted fill, a rim catching pink and teal, and a
    /// small highlight. <paramref name="lit"/> fills it a little, for the chosen song.</summary>
    public static Grid Make(double diameter, Palette p, bool lit = false)
    {
        var fill = new RadialGradientBrush
        {
            Center = new Point(0.38, 0.32),
            GradientOrigin = new Point(0.38, 0.32),
            RadiusX = 0.75,
            RadiusY = 0.75,
        };
        fill.GradientStops.Add(new GradientStop { Color = lit ? p.Lit : p.Fill, Offset = 0 });
        fill.GradientStops.Add(new GradientStop { Color = p.Fill, Offset = 0.7 });
        fill.GradientStops.Add(new GradientStop { Color = p.FillEdge, Offset = 1 });
        var rim = new LinearGradientBrush { StartPoint = new Point(0, 0), EndPoint = new Point(1, 1) };
        rim.GradientStops.Add(new GradientStop { Color = p.Rim, Offset = 0 });
        rim.GradientStops.Add(new GradientStop { Color = p.Sheen1, Offset = 0.55 });
        rim.GradientStops.Add(new GradientStop { Color = p.Sheen2, Offset = 1 });
        var bubble = new Grid { Width = diameter, Height = diameter };
        bubble.Children.Add(new Ellipse { Fill = fill, Stroke = rim, StrokeThickness = Math.Max(1, diameter / 70) });
        if (diameter >= 12)
            bubble.Children.Add(new Ellipse
            {
                Width = diameter * 0.22,
                Height = diameter * 0.12,
                Fill = new SolidColorBrush(p.Highlight),
                HorizontalAlignment = HorizontalAlignment.Left,
                VerticalAlignment = VerticalAlignment.Top,
                Margin = new Thickness(diameter * 0.2, diameter * 0.16, 0, 0),
                RenderTransform = new RotateTransform { Angle = -25, CenterX = diameter * 0.11, CenterY = diameter * 0.06 },
                IsHitTestVisible = false,
            });
        return bubble;
    }

    /// <summary>A slow float on the spot, forever: a loop through four points around the element's place, out of step
    /// with its neighbours (<paramref name="seed"/>). Nothing when animations are off.</summary>
    public static void Drift(UIElement element, float amplitude, int seed, double seconds = 7)
    {
        if (!Animate) return;
        var random = new Random(seed);
        ElementCompositionPreview.SetIsTranslationEnabled(element, true);
        var visual = ElementCompositionPreview.GetElementVisual(element);
        var compositor = visual.Compositor;
        var ease = compositor.CreateCubicBezierEasingFunction(new Vector2(0.45f, 0), new Vector2(0.55f, 1));
        var animation = compositor.CreateVector3KeyFrameAnimation();
        float Jitter() => amplitude * (0.6f + (float)random.NextDouble() * 0.4f);
        animation.InsertKeyFrame(0.25f, new Vector3(Jitter(), -Jitter(), 0), ease);
        animation.InsertKeyFrame(0.5f, new Vector3(0, -Jitter() * 1.4f, 0), ease);
        animation.InsertKeyFrame(0.75f, new Vector3(-Jitter(), -Jitter() * 0.6f, 0), ease);
        animation.InsertKeyFrame(1f, Vector3.Zero, ease);
        animation.Duration = TimeSpan.FromSeconds(seconds * (0.8 + random.NextDouble() * 0.5));
        animation.DelayTime = TimeSpan.FromSeconds(random.NextDouble() * 2);
        animation.IterationBehavior = AnimationIterationBehavior.Forever;
        visual.StartAnimation("Translation", animation);
    }

    /// <summary>A tiny bubble rising through <paramref name="height"/> and fading out at the top, again and again.</summary>
    public static void Rise(UIElement element, float height, int seed)
    {
        if (!Animate) return;
        var random = new Random(seed);
        ElementCompositionPreview.SetIsTranslationEnabled(element, true);
        var visual = ElementCompositionPreview.GetElementVisual(element);
        var compositor = visual.Compositor;
        var duration = TimeSpan.FromSeconds(9 + random.NextDouble() * 8);
        var delay = TimeSpan.FromSeconds(random.NextDouble() * 8);
        var sway = 6 + (float)random.NextDouble() * 8;
        var move = compositor.CreateVector3KeyFrameAnimation();
        move.InsertKeyFrame(0, Vector3.Zero);
        move.InsertKeyFrame(0.33f, new Vector3(sway, -height / 3, 0));
        move.InsertKeyFrame(0.66f, new Vector3(-sway, -height * 2 / 3, 0));
        move.InsertKeyFrame(1, new Vector3(0, -height, 0));
        move.Duration = duration;
        move.DelayTime = delay;
        move.IterationBehavior = AnimationIterationBehavior.Forever;
        var fade = compositor.CreateScalarKeyFrameAnimation();
        fade.InsertKeyFrame(0, 0);
        fade.InsertKeyFrame(0.15f, 0.8f);
        fade.InsertKeyFrame(0.8f, 0.6f);
        fade.InsertKeyFrame(1, 0);
        fade.Duration = duration;
        fade.DelayTime = delay;
        fade.IterationBehavior = AnimationIterationBehavior.Forever;
        visual.Opacity = 0;
        visual.StartAnimation("Translation", move);
        visual.StartAnimation("Opacity", fade);
    }
}
