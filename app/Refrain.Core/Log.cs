namespace Refrain.Core;

/// <summary>The app's log, <c>logs\app.log</c> in the data folder; kept under 1 MB by moving the old one aside.</summary>
public static class Log
{
    const long MaxBytes = 1 << 20;
    static readonly Lock Gate = new();

    public static void Info(string message) => Write("INFO", message);

    public static void Error(string message, Exception? error = null) =>
        Write("ERROR", error is null ? message : $"{message}: {error}");

    static void Write(string level, string message)
    {
        try
        {
            lock (Gate)
            {
                var path = Path.Combine(AppPaths.Logs, "app.log");
                if (File.Exists(path) && new FileInfo(path).Length > MaxBytes)
                    File.Move(path, path + ".1", overwrite: true);
                File.AppendAllText(path, $"{DateTime.Now:yyyy-MM-dd HH:mm:ss} {level} {message}{Environment.NewLine}");
            }
        }
        catch (IOException)
        {
            // Logging never stops the app.
        }
    }
}
