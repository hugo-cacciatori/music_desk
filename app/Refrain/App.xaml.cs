using System.Runtime.InteropServices;
using Microsoft.UI.Dispatching;
using Microsoft.UI.Xaml;
using Refrain.Core;

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

public partial class App : Application
{
    readonly Instance? instance;
    readonly StartRequest request;
    MainWindow? window;

    public App(Instance? instance, StartRequest request)
    {
        this.instance = instance;
        this.request = request;
        InitializeComponent();
        UnhandledException += (_, e) => Log.Error("Unhandled error", e.Exception);
    }

    protected override void OnLaunched(LaunchActivatedEventArgs e)
    {
        Log.Info($"Refrain {typeof(App).Assembly.GetName().Version} started");
        window = new MainWindow(request);
        if (instance is not null)
            instance.Received += later => window.DispatcherQueue.TryEnqueue(() => _ = window.OpenFromOutside(later));
        window.Activate();
    }
}
