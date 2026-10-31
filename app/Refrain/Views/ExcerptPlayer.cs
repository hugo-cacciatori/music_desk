using Microsoft.UI.Dispatching;
using Refrain.Core;
using Refrain.Core.Playback;
using Windows.Media.Core;
using Windows.Media.Playback;

namespace Refrain.Views;

/// <summary>The preview's excerpt (SPEC §37): about 30 seconds from a third into the song, fading in and out, looping,
/// at the player's volume. It never interrupts the player: while the player plays, or when the preview's sound is off,
/// the excerpt plays silent. Media keys aren't used.</summary>
public sealed partial class ExcerptPlayer : IDisposable
{
    readonly MediaPlayer media = new() { AutoPlay = false, AudioCategory = MediaPlayerAudioCategory.Media, Volume = 0 };
    readonly DispatcherQueue ui = DispatcherQueue.GetForCurrentThread();
    readonly DispatcherQueueTimer timer;
    readonly Settings settings;
    readonly Player player;
    Excerpt? window;
    int ticket;

    /// <summary>For an agent's off-screen check: no sound at all.</summary>
    public bool Muted { get; set; }

    /// <summary>What plays now, for the panel; null when nothing does.</summary>
    public string? Playing { get; private set; }

    public ExcerptPlayer(Settings settings, Player player)
    {
        this.settings = settings;
        this.player = player;
        media.CommandManager.IsEnabled = false;
        media.MediaOpened += (_, _) => ui.TryEnqueue(Opened);
        media.MediaFailed += (_, e) => ui.TryEnqueue(() =>
        {
            Log.Info($"The preview couldn't play {Playing}: {e.ErrorMessage}");
            Stop();
        });
        timer = ui.CreateTimer();
        timer.Interval = TimeSpan.FromMilliseconds(80);
        timer.Tick += (_, _) => Tick();
    }

    public async Task Play(string path, string title)
    {
        Stop();
        var mine = ticket;
        Playing = title;
        try
        {
            var file = await Windows.Storage.StorageFile.GetFileFromPathAsync(path);
            if (mine != ticket) return;
            media.Source = MediaSource.CreateFromStorageFile(file);
        }
        catch (Exception e) when (e is IOException or UnauthorizedAccessException or System.Runtime.InteropServices.COMException or ArgumentException)
        {
            Log.Info($"The preview couldn't open {path}: {e.Message}");
            if (mine == ticket) Stop();
        }
    }

    void Opened()
    {
        window = Excerpt.For(media.PlaybackSession.NaturalDuration);
        media.Volume = 0;
        media.PlaybackSession.Position = window.Start;
        media.Play();
        timer.Start();
    }

    /// <summary>The fades, the loop, and silence while the player plays or the preview's sound is off.</summary>
    void Tick()
    {
        if (window is null) return;
        var position = media.PlaybackSession.Position;
        if (window.Over(position))
        {
            media.PlaybackSession.Position = window.Start;
            position = window.Start;
        }
        media.IsMuted = Muted || !settings.PreviewSound || player.Playing;
        media.Volume = Math.Clamp(settings.PlayerVolume, 0, 1) * window.Level(position);
    }

    public void Stop()
    {
        ticket++;
        timer.Stop();
        window = null;
        Playing = null;
        media.Pause();
        media.Source = null;
    }

    public void Dispose()
    {
        Stop();
        media.Dispose();
    }
}
