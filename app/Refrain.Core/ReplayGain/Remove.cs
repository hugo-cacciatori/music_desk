using System.Text.RegularExpressions;
using Refrain.Core.Options;

namespace Refrain.Core.ReplayGain;

/// <summary>Removing ReplayGain (SPEC §10 B, Hugo 2026-10-16): the REPLAYGAIN_* values and the LUFS comment lines.
/// Ported from the Python app's <c>core/replaygain/remove.py</c>.</summary>
public static class Remove
{
    const string Number = @"[-+]?\d+(?:[.,]\d+)?";

    /// <summary>A comment line the ReplayGain step writes, for any target: the template ("LUFS Target : {target}")
    /// with {target} as a number. A template without {target} matches only itself.</summary>
    public static Regex LufsCommentPattern(string template)
    {
        var body = string.Join(Number, template.Trim().Split("{target}").Select(Regex.Escape));
        return new Regex(@"^\s*" + body + @"\s*$");
    }

    /// <summary>Removes a file's ReplayGain tags: every REPLAYGAIN_* value, and the comment lines the ReplayGain step
    /// wrote (the page's comment template, any target). Other comment lines stay. Saves only when something changed;
    /// returns whether it did. Throws the file's errors to the caller.</summary>
    public static bool Run(string path, ReplayGainOptions options)
    {
        if (!RgTags.Taggable(path)) return false;  // raw AAC: nothing to remove, nothing can be written
        if (!RgTags.Writable(Path.GetExtension(path)))
            throw new NotSupportedException($"Refrain can't write the tags of {Path.GetExtension(path).TrimStart('.').ToUpperInvariant()} files.");
        using var tags = RgTags.Open(path);
        var changed = tags.RemoveAll() > 0;
        if (tags.Comment is { } comment)
        {
            var pattern = LufsCommentPattern(options.Comment);
            var lines = Report.SplitLines(comment);
            var kept = lines.Where(l => !pattern.IsMatch(l)).ToList();
            if (kept.Count != lines.Count)
            {
                var text = string.Join("\n", kept).Trim();
                tags.SetComment(text.Length > 0 ? text : null);
                changed = true;
            }
        }
        if (changed) tags.Save();
        return changed;
    }
}
