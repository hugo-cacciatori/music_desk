namespace Refrain.Core.Cue;

/// <summary>Per column, the lowest and highest sample value, from -1.0 to 1.0. Fewer than <see cref="Waveform.Columns"/>
/// for a track with fewer frames; a column of silence has both at 0.</summary>
public sealed record Peaks(IReadOnlyList<double> Lows, IReadOnlyList<double> Highs)
{
    public int Count => Lows.Count;
}

/// <summary>Fed raw PCM (little-endian, signed, interleaved: what <see cref="Flac.Decode"/> gives) in chunks of any
/// size. Only each sample's most significant byte is kept, as the Python app's <c>core/waveform.py</c> does: 256 levels
/// are more than a 70-pixel waveform shows, and the peaks come out equal to the Python app's.</summary>
public sealed class PeakReducer
{
    readonly int width;  // bytes per sample, as flac writes raw PCM
    readonly int frame;
    readonly long samples;
    readonly int columns;
    readonly double scale;  // the top byte of a 20-bit sample (in 3 bytes) only reaches ±8: scale by what it can reach
    byte[] carry = [];
    long done;  // frames reduced so far
    int column;
    long end;
    int? low, high;  // signed top bytes of the current column
    readonly List<double> lows = [], highs = [];

    public PeakReducer(long samples, int channels, int bits, int columns = Waveform.Columns)
    {
        width = (bits + 7) / 8;
        frame = width * channels;
        this.samples = samples;
        this.columns = (int)Math.Max(1, Math.Min(columns, samples));
        scale = 1 << Math.Max(0, bits - 8 * (width - 1) - 1);
        end = ColumnEnd(0);
    }

    /// <summary>One past the last frame of a column; the last column takes any frames beyond the declared length.</summary>
    long ColumnEnd(int index) => index >= columns - 1 ? 1L << 62 : (index + 1) * samples / columns;

    public void Feed(ReadOnlySpan<byte> chunk)
    {
        byte[]? joined = null;
        if (carry.Length > 0)
        {
            joined = [.. carry, .. chunk];
            chunk = joined;
        }
        var usable = chunk.Length - chunk.Length % frame;
        carry = chunk[usable..].ToArray();
        var position = 0;
        while (position < usable)
        {
            var frames = Math.Min(end - done, (usable - position) / frame);
            var stop = position + (int)frames * frame;
            int lo = low ?? int.MaxValue, hi = high ?? int.MinValue;
            for (var i = position + width - 1; i < stop; i += width)
            {
                var value = (sbyte)chunk[i];
                if (value < lo) lo = value;
                if (value > hi) hi = value;
            }
            (low, high) = (lo, hi);
            done += frames;
            position = stop;
            if (done == end) CloseColumn();
        }
    }

    void CloseColumn()
    {
        lows.Add(low!.Value / scale);
        highs.Add(high!.Value / scale);
        (low, high) = (null, null);
        column++;
        end = ColumnEnd(column);
    }

    /// <summary>The columns read; a column begun but not finished (the stream was shorter than declared) counts.</summary>
    public Peaks Result()
    {
        List<double> l = [.. lows], h = [.. highs];
        if (low is { } lo && high is { } hi)
        {
            l.Add(lo / scale);
            h.Add(hi / scale);
        }
        return new Peaks(l, h);
    }
}

/// <summary>The Trim window's waveform (SPEC §23): a minimum and a maximum per column, all channels together, reduced
/// from flac's raw PCM as its chunks arrive, never holding the whole audio (a 70-minute CD image is 740 MB of PCM).
/// The peaks are read once; the window fits them to its width, so dragging a handle or resizing never decodes again.</summary>
public static class Waveform
{
    public const int Columns = 4096;  // more than any screen is wide

    public static Peaks Reduce(IEnumerable<byte[]> chunks, SourceInfo info, int columns = Columns)
    {
        var reducer = new PeakReducer(info.Samples, info.Channels, info.Bits, columns);
        foreach (var chunk in chunks) reducer.Feed(chunk);
        return reducer.Result();
    }

    /// <summary>The waveform of a whole track, read by flac: a FLAC decoded directly, WAV and AIFF through
    /// <see cref="Flac.DecodeRange"/>. Throws FlacException (FlacCancelledException on Stop).</summary>
    public static Peaks Read(Flac flac, string path, SourceInfo info, CancellationToken cancel = default, int columns = Columns) =>
        Reduce(info.Format == "FLAC" ? flac.Decode(path, cancel) : flac.DecodeRange(path, cancel: cancel), info, columns);

    /// <summary>The peaks reduced (or stretched) to <paramref name="width"/> pixel columns: each pixel takes the
    /// lowest low and the highest high of the columns it covers.</summary>
    public static List<(double Low, double High)> Fit(Peaks peaks, int width)
    {
        var count = peaks.Count;
        var result = new List<(double, double)>();
        if (count == 0 || width <= 0) return result;
        for (var x = 0; x < width; x++)
        {
            var first = (int)((long)x * count / width);
            var last = Math.Max(first + 1, (int)((long)(x + 1) * count / width));
            double lo = double.MaxValue, hi = double.MinValue;
            for (var i = first; i < last; i++)
            {
                lo = Math.Min(lo, peaks.Lows[i]);
                hi = Math.Max(hi, peaks.Highs[i]);
            }
            result.Add((lo, hi));
        }
        return result;
    }
}
