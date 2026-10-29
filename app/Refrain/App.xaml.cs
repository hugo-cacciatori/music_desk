using System.Runtime.InteropServices;
using Microsoft.UI.Dispatching;
using Microsoft.UI.Xaml;
using Refrain.Core;
using Refrain.Views;

namespace Refrain;

public static partial class Program
{
    [LibraryImport("user32.dll")]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static partial bool AllowSetForegroundWindow(int processId);

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

/// <summary>The app's windows: the main window, and the Options window shared by the … menu and Explorer's Settings
/// entry. The process ends when its last window closes.</summary>
public partial class App : Application
{
    readonly Instance? instance;
    readonly StartRequest request;
    readonly Settings settings = Settings.Load(AppPaths.Settings);
    MainWindow? window;
    OptionsWindow? options;

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
