using System.Collections.Concurrent;
using Microsoft.Data.Sqlite;

namespace Refrain.Core.Index;

/// <summary>The index file: one writer thread that runs every change in its own transaction (SPEC §2), and
/// read-only connections for everyone else. Readers see a consistent snapshot per transaction (WAL).</summary>
public sealed class Database : IDisposable
{
    readonly string path;
    readonly BlockingCollection<Action<SqliteConnection>> jobs = new();
    readonly Thread writer;

    public Database(string path)
    {
        this.path = path;
        var conn = new SqliteConnection(ConnectionString(write: true));
        conn.Open();
        Schema.Configure(conn, write: true);
        Schema.Migrate(conn);
        writer = new Thread(() => Run(conn)) { IsBackground = true, Name = "Index writer" };
        writer.Start();
    }

    string ConnectionString(bool write) => new SqliteConnectionStringBuilder
    {
        DataSource = path,
        Mode = write ? SqliteOpenMode.ReadWriteCreate : SqliteOpenMode.ReadOnly,
        Pooling = !write,
    }.ToString();

    void Run(SqliteConnection conn)
    {
        using (conn)
            foreach (var job in jobs.GetConsumingEnumerable())
                job(conn);
    }

    /// <summary>Runs <paramref name="work"/> on the writer thread inside BEGIN IMMEDIATE; a failing job is rolled
    /// back alone and its exception goes to the returned task.</summary>
    public Task<T> Write<T>(Func<SqliteConnection, SqliteTransaction, T> work)
    {
        var done = new TaskCompletionSource<T>(TaskCreationOptions.RunContinuationsAsynchronously);
        jobs.Add(conn =>
        {
            try
            {
                using var tx = conn.BeginTransaction(deferred: false);
                var result = work(conn, tx);
                tx.Commit();
                done.SetResult(result);
            }
            catch (Exception e)
            {
                done.SetException(e);
            }
        });
        return done.Task;
    }

    public Task Write(Action<SqliteConnection, SqliteTransaction> work) =>
        Write<bool>((conn, tx) =>
        {
            work(conn, tx);
            return true;
        });

    /// <summary>A new read-only connection (pooled); dispose it after use. It refuses to write.</summary>
    public SqliteConnection Read()
    {
        var conn = new SqliteConnection(ConnectionString(write: false));
        conn.Open();
        Schema.Configure(conn, write: false);
        return conn;
    }

    /// <summary>Finishes the queued jobs, then closes the writer.</summary>
    public void Dispose()
    {
        jobs.CompleteAdding();
        writer.Join();
        SqliteConnection.ClearAllPools();
    }
}

static class Sql
{
    public static SqliteCommand Command(this SqliteConnection conn, string sql, SqliteTransaction? tx = null,
        params (string Name, object? Value)[] parameters)
    {
        var cmd = conn.CreateCommand();
        cmd.CommandText = sql;
        cmd.Transaction = tx;
        foreach (var (name, value) in parameters)
            cmd.Parameters.AddWithValue(name, value ?? DBNull.Value);
        return cmd;
    }

    public static int Execute(this SqliteConnection conn, string sql, SqliteTransaction? tx = null,
        params (string Name, object? Value)[] parameters)
    {
        using var cmd = conn.Command(sql, tx, parameters);
        return cmd.ExecuteNonQuery();
    }

    public static object? Scalar(this SqliteConnection conn, string sql, SqliteTransaction? tx = null,
        params (string Name, object? Value)[] parameters)
    {
        using var cmd = conn.Command(sql, tx, parameters);
        var value = cmd.ExecuteScalar();
        return value is DBNull ? null : value;
    }

    public static string? NullableString(this SqliteDataReader r, int i) => r.IsDBNull(i) ? null : r.GetString(i);
    public static long? NullableLong(this SqliteDataReader r, int i) => r.IsDBNull(i) ? null : r.GetInt64(i);
    public static double? NullableDouble(this SqliteDataReader r, int i) => r.IsDBNull(i) ? null : r.GetDouble(i);
}
