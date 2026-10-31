namespace Refrain.Core.Playback;

/// <summary>The preview's excerpt (SPEC §37 "The excerpt"): about 30 seconds from a third into the song, fading in and
/// out, looping; a song of 30 seconds or less plays whole, with shorter fades.</summary>
public sealed record Excerpt(TimeSpan Start, TimeSpan Length, TimeSpan Fade)
{
    static readonly TimeSpan Full = TimeSpan.FromSeconds(30), LongFade = TimeSpan.FromSeconds(2);

    public static Excerpt For(TimeSpan duration)
    {
        if (duration <= TimeSpan.Zero) return new Excerpt(TimeSpan.Zero, Full, LongFade);
        if (duration <= Full) return new Excerpt(TimeSpan.Zero, duration, TimeSpan.FromTicks(Math.Min(LongFade.Ticks, duration.Ticks / 6)));
        var start = TimeSpan.FromTicks(Math.Min(duration.Ticks / 3, (duration - Full).Ticks));
        return new Excerpt(start, Full, LongFade);
    }

    /// <summary>The volume's share at <paramref name="position"/> in the song: rising over the fade in, full, falling over
    /// the fade out; 0 outside the excerpt.</summary>
    public double Level(TimeSpan position)
    {
        var into = (position - Start).TotalSeconds;
        var left = (Start + Length - position).TotalSeconds;
        if (into < 0 || left < 0) return 0;
        var fade = Math.Max(0.01, Fade.TotalSeconds);
        return Math.Clamp(Math.Min(into / fade, left / fade), 0, 1);
    }

    /// <summary>Whether the excerpt is over at <paramref name="position"/> and starts again.</summary>
    public bool Over(TimeSpan position) => position >= Start + Length;
}
