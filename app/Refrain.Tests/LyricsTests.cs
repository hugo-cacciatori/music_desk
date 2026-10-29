using System.Net;
using System.Text;
using Refrain.Core.Index;
using Refrain.Core.Lyrics;
using Refrain.Core.Search;
using Xunit;

namespace Refrain.Tests;

/// <summary>A fake LRCLIB: answers each request from a function, and records the requests. Tests never reach the
/// network.</summary>
sealed class FakeLrclib(Func<HttpRequestMessage, HttpResponseMessage> answer) : HttpMessageHandler
{
    public List<string> Requests { get; } = [];

    protected override Task<HttpResponseMessage> SendAsync(HttpRequestMessage request, CancellationToken cancel)
    {
        lock (Requests) Requests.Add(request.RequestUri!.PathAndQuery);
        return Task.FromResult(answer(request));
    }

    public static HttpResponseMessage Json(string json) =>
        new(HttpStatusCode.OK) { Content = new StringContent(json, Encoding.UTF8, "application/json") };

    public static HttpResponseMessage Status(HttpStatusCode code) => new(code) { Content = new StringContent("") };

    public static LrclibClient Client(FakeLrclib fake) => new(fake, (_, _) => Task.CompletedTask, () => 0);
}

/// <summary>LRCLIB lookups (SPEC §9), the index side, the service, and Lyrics search (SPEC §6); ported from the
/// Python app's tests where they exist.</summary>
public sealed class LyricsTests : IDisposable
{
    static readonly DateOnly Today = new(2026, 10, 29);
    readonly HandIndex lib = new();

    public void Dispose() => lib.Dispose();

    static readonly LyricsTrack Glove = new("Radiohead", "Glove", "OK", 200);

    [Fact]
    public async Task The_exact_match_comes_first()
    {
        var fake = new FakeLrclib(r => r.RequestUri!.AbsolutePath.EndsWith("/get")
            ? FakeLrclib.Json("""{"id": 7, "plainLyrics": "Fits like a glove", "syncedLyrics": "[00:01.00]Fits like a glove"}""")
            : FakeLrclib.Json("[]"));
        var entry = await Lrclib.Fetch(Glove, FakeLrclib.Client(fake), Today, TestContext.Current.CancellationToken);
        Assert.Equal(new LyricsEntry(LyricsEntry.Found, 7, "Fits like a glove", "[00:01.00]Fits like a glove", "2026-10-29"), entry);
        Assert.Single(fake.Requests);
        Assert.Contains("album_name=OK", fake.Requests[0]);
        Assert.Contains("duration=200", fake.Requests[0]);
    }

    [Fact]
    public async Task A_search_keeps_the_same_recording_and_prefers_synced()
    {
        var fake = new FakeLrclib(r => r.RequestUri!.AbsolutePath.EndsWith("/get")
            ? FakeLrclib.Status(HttpStatusCode.NotFound)
            : FakeLrclib.Json("""
                [{"id": 1, "artistName": "Someone Else", "duration": 200, "plainLyrics": "no"},
                 {"id": 2, "artistName": "Radiohead", "duration": 260, "plainLyrics": "too long"},
                 {"id": 3, "artistName": "Radiohead", "duration": 201, "plainLyrics": "plain only"},
                 {"id": 4, "artistName": "radiohead!", "duration": 198, "syncedLyrics": "[00:01.00] synced"}]
                """));
        var entry = await Lrclib.Fetch(Glove, FakeLrclib.Client(fake), Today, TestContext.Current.CancellationToken);
        Assert.Equal(4, entry.LrclibId);
        Assert.Equal("synced", entry.Plain);  // plain text made from the synced lyrics
    }

    [Fact]
    public async Task Instrumental_and_not_found()
    {
        var instrumental = new FakeLrclib(_ => FakeLrclib.Json("""{"id": 9, "instrumental": true}"""));
        Assert.Equal(LyricsEntry.Instrumental,
            (await Lrclib.Fetch(Glove, FakeLrclib.Client(instrumental), Today, TestContext.Current.CancellationToken)).Status);
        var none = new FakeLrclib(r => r.RequestUri!.AbsolutePath.EndsWith("/get") ? FakeLrclib.Status(HttpStatusCode.NotFound) : FakeLrclib.Json("[]"));
        Assert.Equal(new LyricsEntry(LyricsEntry.NotFound, Checked: "2026-10-29"),
            await Lrclib.Fetch(Glove, FakeLrclib.Client(none), Today, TestContext.Current.CancellationToken));
        var noAlbum = new FakeLrclib(_ => FakeLrclib.Json("[]"));
        await Lrclib.Fetch(Glove with { Album = null }, FakeLrclib.Client(noAlbum), Today, TestContext.Current.CancellationToken);
        Assert.StartsWith("/api/search", Assert.Single(noAlbum.Requests));  // no album: no exact lookup
    }

    [Fact]
    public async Task Busy_answers_and_network_errors_are_retried_then_raised()
    {
        var calls = 0;
        var busy = new FakeLrclib(_ => ++calls < 3
            ? new HttpResponseMessage(HttpStatusCode.ServiceUnavailable) { Headers = { RetryAfter = new(TimeSpan.FromSeconds(5)) } }
            : FakeLrclib.Json("""{"id": 1, "plainLyrics": "x"}"""));
        var pauses = new List<TimeSpan>();
        var client = new LrclibClient(busy, (t, _) =>
        {
            pauses.Add(t);
            return Task.CompletedTask;
        }, () => 0);
        Assert.Equal(LyricsEntry.Found, (await Lrclib.Fetch(Glove, client, Today, TestContext.Current.CancellationToken)).Status);
        Assert.Equal([TimeSpan.FromSeconds(5), TimeSpan.FromSeconds(5)], pauses);

        var broken = new FakeLrclib(_ => FakeLrclib.Status(HttpStatusCode.BadGateway));
        await Assert.ThrowsAsync<HttpRequestException>(() =>
            Lrclib.Fetch(Glove, FakeLrclib.Client(broken), Today, TestContext.Current.CancellationToken));
        Assert.Equal(4, broken.Requests.Count);  // four attempts

        var offline = new FakeLrclib(_ => throw new HttpRequestException("no network"));
        await Assert.ThrowsAsync<HttpRequestException>(() =>
            Lrclib.Fetch(Glove, FakeLrclib.Client(offline), Today, TestContext.Current.CancellationToken));
        Assert.Equal(4, offline.Requests.Count);
    }

    [Fact]
    public void Freshness_timestamps_and_links()
    {
        Assert.True(Lrclib.IsFresh(new LyricsEntry(LyricsEntry.Found, Checked: "2020-01-01"), Today));
        Assert.True(Lrclib.IsFresh(new LyricsEntry(LyricsEntry.NotFound, Checked: "2026-10-10"), Today));
        Assert.False(Lrclib.IsFresh(new LyricsEntry(LyricsEntry.NotFound, Checked: "2026-09-01"), Today));
        Assert.False(Lrclib.IsFresh(null, Today));
        Assert.Equal("one\ntwo", Lrclib.StripTimestamps("[00:01.00] one\r\n[01:02.345]two\n"));
        var (genius, google) = Lrclib.SearchUrls("Sigur Rós", "Hoppípolla (live)");
        Assert.Equal("https://genius.com/search?q=Sigur%20R%C3%B3s%20Hopp%C3%ADpolla%20(live)", genius);
        Assert.EndsWith("+lyrics", google);
        Assert.Equal("https://genius.com/search?q=Glove", Lrclib.SearchUrls(null, "Glove").Genius);
    }

    void Record(string key, LyricsEntry entry)
    {
        using var tx = lib.Conn.BeginTransaction();
        LyricsIndex.Record(lib.Conn, tx, key, entry);
        tx.Commit();
    }

    [Fact]
    public void Pending_tracks_are_one_per_key_and_records_reach_the_search_index()
    {
        var album = lib.Folder(lib.Top, "Album");
        lib.Track(album, "01 Glove.mp3", title: "Glove", artist: "Radiohead", key: "radiohead\x1fglove");
        lib.Track(album, "02 Glove again.mp3", title: "Glove", artist: "Radiohead", key: "radiohead\x1fglove");
        lib.Track(album, "03 Old.mp3", title: "Old", artist: "Radiohead", key: "radiohead\x1fold");
        lib.Track(album, "04 Broken.mp3", key: "x", readError: "TagError");
        Record("radiohead\x1fold", new LyricsEntry(LyricsEntry.NotFound, Checked: "2026-01-01"));
        var pending = LyricsIndex.Pending(lib.Conn, Lrclib.RetryBefore(Today));
        Assert.Equal(["radiohead\x1fglove", "radiohead\x1fold"], pending.Select(p => p.Key));  // a stale not_found again
        Assert.Equal(2, LyricsIndex.PendingCount(lib.Conn, Lrclib.RetryBefore(Today)));
        Assert.Equal(new LyricsTrack("Radiohead", "Glove", null, 0), pending[0].Track);

        Record("radiohead\x1fglove", new LyricsEntry(LyricsEntry.Found, 1, "Fits like a glove", null, "2026-10-29"));
        Assert.Equal(2L, lib.Scalar("SELECT count(*) FROM search WHERE lrclib = 'Fits like a glove'"));
        Record("radiohead\x1fglove", new LyricsEntry(LyricsEntry.NotFound, Checked: "2026-10-29"));
        Assert.Equal(0L, lib.Scalar("SELECT count(*) FROM search WHERE lrclib IS NOT NULL"));
    }

    [Fact]
    public async Task The_service_looks_every_key_up_once()
    {
        using var temp = new TempFolder();
        var music = temp.Sub("Music");
        Setup.Copy(Path.Combine(Setup.Fixtures, "album"), music);
        using var db = new Database(Path.Combine(temp.Path, "library.db"));
        using (var indexer = new Indexer(db))
        {
            var root = await db.Write((c, t) => Roots.Add(c, t, music));
            indexer.ScanRoot(root.RootId!.Value);
            indexer.WaitIdle();
        }
        var fake = new FakeLrclib(r => r.RequestUri!.AbsolutePath.EndsWith("/get")
            ? FakeLrclib.Json("""{"id": 5, "plainLyrics": "words"}""")
            : FakeLrclib.Json("""[{"id": 6, "artistName": "Fixture Artist", "plainLyrics": "words"}]"""));
        long keys;
        using (var conn = db.Read()) keys = LyricsIndex.PendingCount(conn, Lrclib.RetryBefore(Today));
        Assert.True(keys > 1);
        var ready = new List<string>();
        using (var service = new LyricsService(db, FakeLrclib.Client(fake), today: () => Today))
        {
            service.EntryReady += key =>
            {
                lock (ready) ready.Add(key);
            };
            service.Start();
            await service.WaitIdle(TimeSpan.FromSeconds(20));
        }
        Assert.Equal(keys, ready.Distinct().Count());
        Assert.Equal(ready.Count, ready.Distinct().Count());  // never twice
        using (var conn = db.Read()) Assert.Equal(0, LyricsIndex.PendingCount(conn, Lrclib.RetryBefore(Today)));
    }

    [Fact]
    public async Task Ten_failures_in_a_row_pause_every_lookup()
    {
        var album = lib.Folder(lib.Top, "Album");
        for (var i = 0; i < 14; i++) lib.Track(album, $"{i:00} Song.mp3", title: $"Song {i}", artist: "A", key: $"a\x1fsong {i}");
        lib.Conn.Close();
        using var db = new Database(lib.Conn.DataSource);
        var fake = new FakeLrclib(_ => throw new HttpRequestException("down"));
        DateTime? until = null;
        using var service = new LyricsService(db, FakeLrclib.Client(fake), today: () => Today);
        service.Unreachable += t => until = t;
        service.Start();
        for (var i = 0; i < 200 && until is null; i++) await Task.Delay(20, TestContext.Current.CancellationToken);
        Assert.NotNull(until);
        Assert.NotNull(service.PausedUntil);
        await Task.Delay(200, TestContext.Current.CancellationToken);
        Assert.True(fake.Requests.Count < 14 * 4);  // the rest wait for the pause to end
        Assert.True(service.FailedKeys().Count >= 10);
    }

    // -- Lyrics search (ported from tests/core/test_search.py) --

    HashSet<string> Find(string text, SearchMode mode = SearchMode.Files, IReadOnlySet<string>? errors = null) =>
        LibrarySearch.Find(lib.Conn, SearchQuery.Parse(text), mode: mode, errorKeys: errors).Ids
            .Select(id => (string)lib.Scalar("SELECT name FROM entries WHERE id = $id", ("$id", id))!)
            .ToHashSet();

    void Songs()
    {
        var album = lib.Folder(lib.Top, "Björk - Homogenic");
        lib.Track(album, "01 Hunter.flac", title: "Hunter", artist: "Björk", album: "Homogenic", key: "k1");
        lib.Track(album, "02 Jóga.flac", title: "Jóga", artist: "Björk", album: "Homogenic", key: "k2");
        var other = lib.Folder(lib.Top, "Radiohead");
        lib.Track(other, "Glove.mp3", title: "Glove", artist: "Radiohead", key: "k3");
        lib.Track(other, "lovely.opus", title: "Lovely", artist: "The Cure", key: "k4");
        lib.Track(other, "untitled.flac");
        Record("k1", new LyricsEntry(LyricsEntry.Found, 1, "If travel is searching\nAnd home what's been found", "[00:01.00]If", "2026-10-15"));
        Record("k3", new LyricsEntry(LyricsEntry.Found, 3, "Fits like a glove", null, "2026-10-15"));
        Record("k4", new LyricsEntry(LyricsEntry.NotFound, Checked: "2026-10-15"));
        Record("k2", new LyricsEntry(LyricsEntry.Instrumental, Checked: "2026-10-15"));
    }

    [Fact]
    public void Lyrics_search_matches_lrclibs_text_only()
    {
        Songs();
        Assert.Equal(["01 Hunter.flac"], Find("travel is", SearchMode.Lyrics));
        Assert.Empty(Find("Hunter", SearchMode.Lyrics));
        Assert.Equal(["Glove.mp3"], Find("glove", SearchMode.Lyrics));
        Assert.Equal(["01 Hunter.flac", "Glove.mp3"], Find("ve", SearchMode.Lyrics));
        Assert.Empty(Find("artist:cure ove", SearchMode.Lyrics));
        Assert.Equal(["Glove.mp3"], Find("artist:radiohead ove", SearchMode.Lyrics));
        Assert.Empty(Find("travel is"));  // lyrics are Lyrics search's
        Assert.Equal(["Glove.mp3"], Find("  like a glove ", SearchMode.Lyrics));
        Assert.Empty(Find("glove like", SearchMode.Lyrics));
    }

    [Fact]
    public void The_lrclib_filter_follows_the_column()
    {
        Songs();
        var album = (long)lib.Scalar("SELECT id FROM entries WHERE name = 'Björk - Homogenic'")!;
        lib.Track(album, "03 Pending.flac", title: "Pending", artist: "Björk", key: "k5");
        lib.Track(album, "04 Failed.flac", title: "Failed", artist: "Björk", key: "k6");
        lib.Track(album, "05 Broken.flac", key: "k7", readError: "TagError: no");
        Assert.Equal(["01 Hunter.flac"], Find("lrclib:synced"));
        Assert.Equal(["Glove.mp3"], Find("lrclib:plain"));
        Assert.Equal(["02 Jóga.flac"], Find("lrclib:instrumental"));
        Assert.Equal(["lovely.opus"], Find("lrclib:notfound"));
        Assert.Equal(["03 Pending.flac", "04 Failed.flac"], Find("lrclib:pending"));
        Assert.Equal(["03 Pending.flac"], Find("lrclib:pending", errors: new HashSet<string> { "k6" }));
        Assert.Equal(["04 Failed.flac"], Find("lrclib:error", errors: new HashSet<string> { "k6" }));
        Assert.Equal(["01 Hunter.flac", "Glove.mp3"], Find("lrclib:synced lrclib:plain"));
        Assert.Equal(["untitled.flac"], Find("lrclib:notags"));
    }

    [Fact]
    public void A_card_shows_the_matching_line_and_its_neighbours()
    {
        Assert.Equal("one\ntwo match\nthree", LibrarySearch.Extract("zero\n\none\n[00:02.00]two match\nthree\nfour", ["match"]));
        Assert.Equal("match\nnext", LibrarySearch.Extract("match\nnext\nlast", ["match"]));
        Assert.Equal("", LibrarySearch.Extract("nothing here", ["match"]));
        Songs();
        var query = SearchQuery.Parse("home");
        var ids = LibrarySearch.Find(lib.Conn, query, mode: SearchMode.Lyrics).Ids;
        var row = Assert.Single(LibrarySearch.Rows(lib.Conn, ids, query, SearchMode.Lyrics, TestContext.Current.CancellationToken));
        Assert.Equal("If travel is searching\nAnd home what's been found", row.Extract);
        Assert.Equal([(27, 4)], row.ExtractSpans);
        Assert.Empty(row.TitleSpans);  // Lyrics search highlights its extract only
    }
}
