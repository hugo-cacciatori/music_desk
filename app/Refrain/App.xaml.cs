using Microsoft.UI.Dispatching;
using Microsoft.UI.Xaml;
using Refrain.Core;

namespace Refrain;

public static class Program
{
    [STAThread]
    static int Main(string[] args)
    {
        WinRT.ComWrappersSupport.InitializeComWrappers();
        Application.Start(init =>
        {
            SynchronizationContext.SetSynchronizationContext(
                new DispatcherQueueSynchronizationContext(DispatcherQueue.GetForCurrentThread()));
            _ = new App();  // lives on as Application.Current
        });
        return 0;
    }
}

public partial class App : Application
{
    MainWindow? window;

    public App()
    {
        InitializeComponent();
        UnhandledException += (_, e) => Log.Error("Unhandled error", e.Exception);
    }

    protected override void OnLaunched(LaunchActivatedEventArgs e)
    {
        Log.Info($"Refrain {typeof(App).Assembly.GetName().Version} started");
        window = new MainWindow();
        window.Activate();
    }
}
