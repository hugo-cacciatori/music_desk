using Microsoft.UI.Dispatching;
using Refrain.Core;
using Refrain.Core.Playback;
using Windows.Media.Core;
using Windows.Media.Playback;

namespace Refrain.Views;

/// <summary>The player (SPEC §37 "The player"): an album's songs in order through Windows' own media playback, no
/// bundled codec; it only reads the files (Ground rule 1). Media keys aren't used yet (Windows' media overlay stays
/// off). It never plays a file a task holds (<see cref="PathLocks"/>): a task starting on the folder of the song playing
/// or queued stops it and says why, or, as the Player page chooses, skips to the next song the task doesn't touch.
/// Everything is raised on the UI thread.</summary>
public sealed partial class Player : IDisposable
{
    readonly MediaPlayer media = new() { AutoPlay = false, AudioCategory = MediaPlayerAudioCategory.Media };
    readonly DispatcherQueue ui = DispatcherQueue.GetForCurrentThread();
    readonly DispatcherQueueTimer ticker;
    readonly Settings settings;
    readonly PathLocks locks;
    TimeSpan startAt;
    bool playWhenOpen;
    int loading;

    public IReadOnlyList<PlayerSong> Queue { get; private set; } = [];
    public int Index { get; private set; } = -1;
    public PlayerSong? Current => Index >= 0 && Index < Queue.Count ? Queue[Index] : null;
    public bool Playing { get; private set; }
    public TimeSpan Position => Current is null ? TimeSpan.Zero : media.PlaybackSession.Position;
    public TimeSpan Duration { get; private set; }
    /// <summary>Why nothing plays, when that needs saying (a busy folder, a file Windows can't play); null otherwise.</summary>
    public string? Notice { get; private set; }

    /// <summary>The song, the state or the notice changed.</summary>
    public event Action? Changed;
    /// <summary>A few times a second while playing: the position moved.</summary>
    public event Action? Ticked;

    public Player(Settings settings, PathLocks locks)
    {
        this.settings = settings;
        this.locks = locks;
        media.CommandManager.IsEnabled = false;  // media keys: not yet (SPEC §37)
        media.Volume = Math.Clamp(settings.PlayerVolume, 0, 1);
        media.MediaOpened += (_, _) => ui.TryEnqueue(Opened);
        media.MediaEnded += (_, _) => ui.TryEnqueue(Ended);
        media.MediaFailed += (_, e) => ui.TryEnqueue(() => Failed(e.ErrorMessage));
        ticker = ui.CreateTimer();
        ticker.Interval = TimeSpan.FromMilliseconds(200);
        ticker.Tick += (_, _) => Ticked?.Invoke();
        locks.Changed += () => ui.TryEnqueue(CheckBusy);
    }

    public double Volume
    {
        get => media.Volume;
        set
        {
            media.Volume = Math.Clamp(value, 0, 1);
            settings.PlayerVolume = media.Volume;
        }
    }

    /// <summary>For an agent's off-screen check: no sound at all.</summary>
    public bool Muted
    {
        get => media.IsMuted;
        set => media.IsMuted = value;
    }

    bool Playable(int i) => locks.Conflict(Queue[i].Path) is null;

    /// <summary>Plays <paramref name="queue"/>'s song <paramref name="index"/> from <paramref name="start"/>; paused
    /// there instead with <paramref name="paused"/> (Refrain reopening on the last song). A busy song says why.</summary>
    public void Play(IReadOnlyList<PlayerSong> queue, int index, TimeSpan start = default, bool paused = false)
    {
        Queue = queue;
        if (locks.Conflict(queue[index].Path) is { } owner)
        {
            Halt(PathLocks.BusyText(Path.GetDirectoryName(queue[index].Path) ?? queue[index].Path, owner) + " Nothing plays from it until it ends.");
            Index = index;
            Changed?.Invoke();
            return;
        }
        _ = Load(index, start, !paused);
    }

    async Task Load(int index, TimeSpan start, bool play)
    {
        var ticket = ++loading;
        Index = index;
        Notice = null;
        Duration = TimeSpan.Zero;
        startAt = start;
        playWhenOpen = play;
        Playing = play;
        Changed?.Invoke();
        try
        {
            var file = await Windows.Storage.StorageFile.GetFileFromPathAsync(Queue[index].Path);
            if (ticket != loading) return;
            media.Source = MediaSource.CreateFromStorageFile(file);
        }
        catch (Exception e) when (e is IOException or UnauthorizedAccessException or System.Runtime.InteropServices.COMException or ArgumentException)
        {
            if (ticket == loading) Failed(e.Message);
        }
    }

    void Opened()
    {
        Duration = media.PlaybackSession.NaturalDuration;
        if (startAt > TimeSpan.Zero && startAt < Duration) media.PlaybackSession.Position = startAt;
        if (playWhenOpen)
        {
            media.Play();
            ticker.Start();
        }
        Changed?.Invoke();
        Ticked?.Invoke();
    }

    void Failed(string why)
    {
        var name = Current is { } song ? System.IO.Path.GetFileName(song.Path) : "This file";
        Log.Info($"The player couldn't play {Current?.Path}: {why}");
        Halt($"Windows can't play {name}.");
        Changed?.Invoke();
    }

    /// <summary>The album goes on (SPEC §37): the next song that may play, the first again after the last unless the
    /// album plays once; nothing playable left, it stops.</summary>
    void Ended()
    {
        if (PlayQueue.Next(Queue.Count, Index, settings.GetAlbumEnd(), Playable) is { } next) _ = Load(next, TimeSpan.Zero, play: true);
        else
        {
            Halt(null);
            media.PlaybackSession.Position = TimeSpan.Zero;
            Changed?.Invoke();
        }
    }

    public void Toggle()
    {
        if (Current is null) return;
        if (Playing)
        {
            media.Pause();
            ticker.Stop();
            Playing = false;
            Changed?.Invoke();
            return;
        }
        if (locks.Conflict(Current.Path) is not null || media.Source is null)
        {
            Play(Queue, Index, Position);  // says why, or loads it again (after a failure or a busy folder)
            return;
        }
        Notice = null;
        media.Play();
        ticker.Start();
        Playing = true;
        Changed?.Invoke();
    }

    /// <summary>The next song; the previous one, or this one from its start when more than 3 seconds have played.</summary>
    public void Step(int direction)
    {
        if (Current is null) return;
        if (direction < 0 && Position > TimeSpan.FromSeconds(3))
        {
            Seek(TimeSpan.Zero);
            return;
        }
        // The buttons go round the album whatever the Player page says: "once" is about the album ending by itself.
        var target = direction > 0 ? PlayQueue.Next(Queue.Count, Index, AlbumEnd.Loop, Playable)
                                   : PlayQueue.Previous(Queue.Count, Index, AlbumEnd.Loop, Playable);
        if (target is { } i) _ = Load(i, TimeSpan.Zero, play: true);
    }

    public void Seek(TimeSpan to)
    {
        if (Current is null) return;
        media.PlaybackSession.Position = to < TimeSpan.Zero ? TimeSpan.Zero : to;
        Ticked?.Invoke();
    }

    void Halt(string? notice)
    {
        ++loading;
        media.Pause();
        ticker.Stop();
        Playing = false;
        Notice = notice;
    }

    /// <summary>A task took or released files (SPEC §37): one starting on the folder of the song playing or of a song
    /// queued after it stops the player and says why, or skips the playing song to the next one it doesn't touch.</summary>
    void CheckBusy()
    {
        if (Current is null || !Playing) return;
        var busy = Enumerable.Range(0, Queue.Count).Where(i => !Playable(i)).ToList();
        if (busy.Count == 0) return;
        var owner = locks.Conflict(Queue[busy.Contains(Index) ? Index : busy[0]].Path)!;
        if (settings.GetBusyFolder() == BusyFolder.Skip)
        {
            if (!busy.Contains(Index)) return;  // the album goes on; the busy songs are skipped when they come
            if (PlayQueue.Next(Queue.Count, Index, AlbumEnd.Loop, Playable) is { } next)
            {
                _ = Load(next, TimeSpan.Zero, play: true);
                Notice = $"{owner} is changing the folder of {Queue[busy[0]].Album}: skipped to {Queue[next].Title}.";
                Changed?.Invoke();
                return;
            }
        }
        Halt($"{owner} is changing the folder of {Queue[busy[0]].Album}: playback stopped. Press play when it ends.");
        Changed?.Invoke();
    }

    public void Dispose()
    {
        ticker.Stop();
        media.Pause();
        media.Dispose();
    }
}
