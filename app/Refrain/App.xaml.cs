using System.Runtime.InteropServices;
using Microsoft.UI.Dispatching;
using Microsoft.UI.Xaml;
using Refrain.Core;
using Refrain.Core.ReplayGain;
using Refrain.Views;
using RefrainMenu;

namespace Refrain;

public static partial class Program
{
    [LibraryImport("user32.dll")]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static partial bool AllowSetForegroundWindow(int processId);

    [LibraryImport("user32.dll", EntryPoint = "MessageBoxW", StringMarshalling = StringMarshalling.Utf16)]
    private static partial int MessageBox(nint owner, string text, string caption, uint type);

    [STAThread]
    static int Main(string[] args)
    {
        // One Refrain at a time (SPEC §29): a later start hands its folder or entry over, lets the running window
        // come to the front, and exits.
        var request = StartRequest.Parse(args);
        var instance = Instance.TryStart();
        if (instance is null)
        {
            AllowSetForegroundWindow(-1);  // ASFW_ANY: the running Refrain may take the foreground
            if (Instance.HandOver(request)) return 0;
            Log.Info("Another Refrain holds the pipe but didn't answer; starting anyway.");
        }
        // The first start after the Python app moves its settings, library folders and LRCLIB results over (SPEC §36
        // "10."), before anything opens them. A failure changes nothing and says why.
        if (Migration.Run(AppPaths.DataFolder) is { Error: { } error })
        {
            MessageBox(0, error, "Refrain", 0x30);  // MB_ICONWARNING
            instance?.Dispose();
            return 1;
        }
        WinRT.ComWrappersSupport.InitializeComWrappers();
        Application.Start(init =>
        {
            SynchronizationContext.SetSynchronizationContext(
                new DispatcherQueueSynchronizationContext(DispatcherQueue.GetForCurrentThread()));
            _ = new App(instance, request);  // lives on as Application.Current
        });
        instance?.Dispose();
        return 0;
    }
}

/// <summary>The app's windows: the main window, the Options window shared by the … menu and Explorer's Settings entry,
/// and each task's own window (SPEC §35). The process ends when its last window closes.</summary>
public partial class App : Application
{
    readonly Instance? instance;
    readonly StartRequest request;
    readonly Settings settings = Settings.Load(AppPaths.Settings);
    MainWindow? window;
    OptionsWindow? options;

    /// <summary>The process's one set of path locks: every task window and the main window share it (SPEC §35).</summary>
    public PathLocks Locks { get; } = new();

    public Settings Settings => settings;

    public App(Instance? instance, StartRequest request)
    {
        this.instance = instance;
        this.request = request;
        InitializeComponent();
        UnhandledException += (_, e) => Log.Error("Unhandled error", e.Exception);
    }

    public static App Instance => (App)Current;

    protected override void OnLaunched(LaunchActivatedEventArgs e)
    {
        Log.Info($"Refrain {typeof(App).Assembly.GetName().Version} started");
        var queue = DispatcherQueue.GetForCurrentThread();
        if (instance is not null)
            instance.Received += later => queue.TryEnqueue(() => Handle(later));
        Handle(request);
    }

    /// <summary>A start's request: Settings opens only the Options window (SPEC §31 "Settings"); anything else needs
    /// the main window.</summary>
    void Handle(StartRequest start)
    {
        if (start.Entry == "settings")
        {
            ShowOptions(start.Paths.FirstOrDefault());
            return;
        }
        if (start.Entry is { } entry && IsTask(entry))
        {
            _ = RunEntry(entry, start.Paths);
            return;
        }
        if (window is null)
        {
            window = new MainWindow(start, settings);
            window.Closed += (_, _) => window = null;
            if (MainWindow.Offscreen) window.AppWindow.Show(activateWindow: false);  // an agent's check: never the focus
            else window.Activate();
            return;
        }
        _ = window.OpenFromOutside(start);
    }

    static readonly Dictionary<string, ReplayGainKind> ReplayGainKinds = new()
    {
        [MenuLogic.ReplayGainAlbum] = ReplayGainKind.Album,
        [MenuLogic.ReplayGainDiscography] = ReplayGainKind.Discography,
        [MenuLogic.ReplayGainAlbums] = ReplayGainKind.Albums,
        [MenuLogic.ReplayGainTracks] = ReplayGainKind.Tracks,
        [MenuLogic.ReplayGainLoose] = ReplayGainKind.Loose,
    };

    static bool IsTask(string entry) =>
        ReplayGainKinds.ContainsKey(entry) ||
        entry is MenuLogic.RemoveReplayGain or MenuLogic.Tidy or MenuLogic.Split or MenuLogic.EditCue or MenuLogic.NewCue or MenuLogic.Trim;

    /// <summary>A task entry, from Explorer or from the app (SPEC §35): unsaved Metadata edits on the files it changes
    /// are asked about first, then the task opens its own window, which alone comes to the front.</summary>
    public async Task RunEntry(string entry, IReadOnlyList<string> paths)
    {
        if (paths.Count == 0) return;
        if (window is not null && !await window.AllowChanges(paths)) return;
        var replayGain = settings.GetReplayGain();
        if (ReplayGainKinds.TryGetValue(entry, out var kind))
        {
            var items = paths.Select(p => (p, Directory.Exists(p))).ToList();
            var label = MenuLogic.Entries(MenuLogic.Classify(false, items), items, replayGain.Target)
                .FirstOrDefault(e => e.Verb == entry)?.Label ?? "ReplayGain";
            var task = new ReplayGainWindow(label, kind, paths, replayGain, Locks);
            task.Finished += folders => window?.TaskChanged(folders);
            task.Start();
        }
        else if (entry is MenuLogic.EditCue or MenuLogic.NewCue)
        {
            var task = new CueEditorWindow(paths[0], entry == MenuLogic.NewCue, settings, Locks);
            task.Finished += folders => window?.TaskChanged(folders);
            task.Start();
        }
        else if (entry == MenuLogic.Split)
        {
            var task = new SplitWindow(paths, settings, Locks);
            task.Finished += folders => window?.TaskChanged(folders);
            task.Start();
        }
        else if (entry == MenuLogic.Trim)
        {
            var task = new TrimWindow(paths, settings, Locks);
            task.Finished += folders => window?.TaskChanged(folders);
            task.Start();
        }
        else if (entry == MenuLogic.Tidy)
        {
            var task = new TidyWindow(paths, settings, Locks);
            task.Finished += folders => window?.TaskChanged(folders);
            task.Start();
        }
        else
        {
            var task = new RemoveWindow(paths, replayGain, Locks);
            task.Finished += folders => window?.TaskChanged(folders);
            task.Start();
        }
    }

    /// <summary>The one Options window, brought forward if already open; the File names example reads its first track
    /// from <paramref name="examplePath"/>.</summary>
    public void ShowOptions(string? examplePath)
    {
        if (options is null)
        {
            options = new OptionsWindow(settings, () => settings.Save(AppPaths.Settings), examplePath);
            options.Closed += (_, _) => options = null;
        }
        options.ShowWindow();
    }
}
