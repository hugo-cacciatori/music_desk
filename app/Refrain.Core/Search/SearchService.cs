using Microsoft.Data.Sqlite;
using Refrain.Core.Index;

namespace Refrain.Core.Search;

/// <summary>A query's shown rows (in result order, and in the asked order), how many matched in all, and the notes
/// on filters it ignored.</summary>
public sealed record SearchOutcome(string Text, List<FileRow> Rows, List<FileRow> Sorted, int Total,
    IReadOnlyList<string> Errors);

/// <summary>Runs searches off the UI thread (SPEC §6 "Service"): a new query interrupts the running one, and a
/// stale result is never returned (null instead). Sorting by the shown column happens here too, so the UI thread
/// only swaps rows.</summary>
public sealed class SearchService(Database db)
{
    int generation;
    SqliteConnection? running;
    readonly Lock gate = new();

    /// <summary>The query's rows in result order (by folder, then name) and in <paramref name="order"/> (the same
    /// list when it is null); null when a newer query replaced this one.</summary>
    public async Task<SearchOutcome?> Run(string text, Comparison<FileRow>? order, SearchMode mode = SearchMode.Files,
        IReadOnlySet<string>? errorKeys = null)
    {
        var mine = Interlocked.Increment(ref generation);
        lock (gate)
            if (running is not null)
                SQLitePCL.raw.sqlite3_interrupt(running.Handle);  // the old query stops at its next step
        return await Task.Run(() =>
        {
            var query = SearchQuery.Parse(text);
            if (query.IsEmpty) return new SearchOutcome(text, [], [], 0, query.Errors);
            using var conn = db.Read();
            lock (gate) running = conn;
            try
            {
                if (mine != Volatile.Read(ref generation)) return null;
                var result = LibrarySearch.Find(conn, query, mode: mode, errorKeys: errorKeys);
                if (mine != Volatile.Read(ref generation)) return null;
                var rows = LibrarySearch.Rows(conn, result.Ids, query, mode);
                var sorted = rows;
                if (order is not null)
                {
                    sorted = rows.ToList();
                    sorted.Sort(order);
                }
                return mine == Volatile.Read(ref generation)
                    ? new SearchOutcome(text, rows, sorted, result.Total, query.Errors)
                    : null;
            }
            catch (SqliteException e) when (e.SqliteErrorCode == 9)  // SQLITE_INTERRUPT
            {
                return null;
            }
            finally
            {
                lock (gate)
                    if (running == conn)
                        running = null;
            }
        });
    }
}
