using Microsoft.Data.Sqlite;

namespace Refrain.Core.Index;

public enum AddOutcome { Added, Merged, AlreadyRoot, InsideRoot, IsAFile, NotAFolder }

/// <summary>What adding a folder did, and the folder to go to (the root's spelling of it when it was already in the
/// library).</summary>
public sealed record AddResult(AddOutcome Outcome, string Path, long? RootId = null);

public sealed record Root(long Id, string Path, bool Online);

/// <summary>The library folders (SPEC §3). A folder already in the library, or inside one, is never registered
/// again, under any spelling; a parent of existing roots merges them, keeping their entries.</summary>
public static class Roots
{
    public static List<Root> All(SqliteConnection conn) => All(conn, null);

    public static List<Root> All(SqliteConnection conn, SqliteTransaction? tx)
    {
        using var cmd = conn.Command("SELECT id, path, online FROM roots", tx);
        using var r = cmd.ExecuteReader();
        var roots = new List<Root>();
        while (r.Read()) roots.Add(new Root(r.GetInt64(0), r.GetString(1), r.GetInt64(2) != 0));
        roots.Sort((a, b) => NaturalOrder.Instance.Compare(a.Path, b.Path));
        return roots;
    }

    /// <summary>Sorts the path out on disk, then registers it if the rule allows. Touches the file system: never
    /// call it on the UI thread.</summary>
    public static AddResult Add(SqliteConnection conn, SqliteTransaction tx, string rawPath)
    {
        var path = RealPath.Normalize(rawPath);
        if (File.Exists(path)) return new AddResult(AddOutcome.IsAFile, path);
        if (!Directory.Exists(path)) return new AddResult(AddOutcome.NotAFolder, path);
        var real = RealPath.Of(path) ?? path;
        var roots = All(conn, tx);
        foreach (var root in roots)
        {
            // An offline root isn't probed (a network timeout): it is compared as written.
            var rootReal = root.Online ? RealPath.Of(root.Path) ?? root.Path : root.Path;
            foreach (var (mine, theirs) in new[] { (path, root.Path), (real, rootReal) })
            {
                if (mine.Equals(theirs, StringComparison.OrdinalIgnoreCase))
                    return new AddResult(AddOutcome.AlreadyRoot, root.Path, root.Id);
                if (RealPath.IsInside(mine, theirs))
                    return new AddResult(AddOutcome.InsideRoot, root.Path + mine[theirs.TrimEnd('\\').Length..], root.Id);
            }
        }
        var inside = roots.Where(root =>
        {
            var rootReal = root.Online ? RealPath.Of(root.Path) ?? root.Path : root.Path;
            return RealPath.IsInside(root.Path, path) || RealPath.IsInside(rootReal, real);
        }).ToList();
        var id = Insert(conn, tx, path);
        foreach (var old in inside) Absorb(conn, tx, id, path, real, old);
        return new AddResult(inside.Count > 0 ? AddOutcome.Merged : AddOutcome.Added, path, id);
    }

    /// <summary>A library folder brought over as it was (the Python app's migration, SPEC §36 "10."): no disk check,
    /// so an offline folder comes over too; the start's online check scans it.</summary>
    public static long Restore(SqliteConnection conn, SqliteTransaction tx, string path, string addedAt, bool online)
    {
        var id = Insert(conn, tx, path, addedAt);
        SetOnline(conn, tx, id, online);
        return id;
    }

    static long Insert(SqliteConnection conn, SqliteTransaction tx, string path, string? addedAt = null)
    {
        var id = (long)conn.Scalar("INSERT INTO roots (path, added_at) VALUES ($p, $t) RETURNING id", tx,
            ("$p", path), ("$t", addedAt ?? DateTime.UtcNow.ToString("O")))!;
        conn.Execute("INSERT INTO entries (root_id, parent_id, name, is_dir) VALUES ($r, NULL, $p, 1)", tx,
            ("$r", id), ("$p", path));
        conn.Execute("INSERT INTO search (rowid, name) SELECT id, NULL FROM entries WHERE root_id = $r", tx, ("$r", id));
        return id;
    }

    /// <summary>A former root inside the new one: its top entry becomes a folder under the new root (the folders in
    /// between created), and its whole subtree moves over.</summary>
    static void Absorb(SqliteConnection conn, SqliteTransaction tx, long newRoot, string path, string real, Root old)
    {
        var relative = RealPath.IsInside(old.Path, path)
            ? old.Path[path.TrimEnd('\\').Length..]
            : (RealPath.Of(old.Path) ?? old.Path)[real.TrimEnd('\\').Length..];
        var names = relative.Split('\\', StringSplitOptions.RemoveEmptyEntries);
        var parent = (long)conn.Scalar("SELECT id FROM entries WHERE root_id = $r AND parent_id IS NULL", tx,
            ("$r", newRoot))!;
        foreach (var name in names[..^1])
        {
            var existing = (long?)conn.Scalar(
                "SELECT id FROM entries WHERE parent_id = $p AND name = $n COLLATE NOCASE", tx,
                ("$p", parent), ("$n", name));
            if (existing is { } found)
            {
                parent = found;
                continue;
            }
            parent = (long)conn.Scalar("INSERT INTO entries (root_id, parent_id, name, is_dir) " +
                                       "VALUES ($r, $p, $n, 1) RETURNING id", tx,
                ("$r", newRoot), ("$p", parent), ("$n", name))!;
            conn.Execute("INSERT INTO search (rowid, name) VALUES ($id, $n)", tx, ("$id", parent), ("$n", name));
        }
        var top = (long)conn.Scalar("SELECT id FROM entries WHERE root_id = $r AND parent_id IS NULL", tx,
            ("$r", old.Id))!;
        // The top entry first leaves the top (one per root), then the subtree changes root.
        conn.Execute("UPDATE entries SET parent_id = $p, name = $n WHERE id = $id", tx,
            ("$p", parent), ("$n", names[^1]), ("$id", top));
        conn.Execute("UPDATE entries SET root_id = $new WHERE root_id = $old", tx, ("$new", newRoot), ("$old", old.Id));
        conn.Execute("INSERT OR REPLACE INTO search (rowid, name) VALUES ($id, $n)", tx, ("$id", top), ("$n", names[^1]));
        conn.Execute("DELETE FROM roots WHERE id = $id", tx, ("$id", old.Id));
    }

    /// <summary>Removes a folder from the index only; nothing on disk changes.</summary>
    public static void Remove(SqliteConnection conn, SqliteTransaction tx, long rootId) =>
        conn.Execute("DELETE FROM roots WHERE id = $id", tx, ("$id", rootId));

    public static void SetOnline(SqliteConnection conn, SqliteTransaction tx, long rootId, bool online) =>
        conn.Execute("UPDATE roots SET online = $o WHERE id = $id", tx, ("$o", online ? 1 : 0), ("$id", rootId));
}
