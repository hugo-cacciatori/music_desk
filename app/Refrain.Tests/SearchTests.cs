using Refrain.Core.Index;
using Refrain.Core.Search;
using Xunit;

namespace Refrain.Tests;

/// <summary>File search (SPEC §6): seek_lyrics' substring rule, names before tags, filters, order and rows; ported
/// from the Python app's tests/core/test_search.py.</summary>
public sealed class SearchTests : IDisposable
{
    readonly HandIndex lib = new();
    readonly long album;
    readonly long other;

    public SearchTests()
    {
        album = lib.Folder(lib.Top, "Björk - Homogenic");
        lib.Track(album, "01 Hunter.flac", title: "Hunter", artist: "Björk", album: "Homogenic", key: "k1", rg: 4,
            date: "1997-09-22", genre: "Electronic");
        lib.Track(album, "02 Jóga.flac", title: "Jóga", artist: "Björk", album: "Homogenic", key: "k2", rg: 2);
        other = lib.Folder(lib.Top, "Radiohead");
        lib.Track(other, "Glove.mp3", title: "Glove", artist: "Radiohead", albumArtist: "Various", key: "k3",
            date: "2001");
        lib.Track(other, "lovely.opus", title: "Lovely", artist: "The Cure", key: "k4");
        lib.Track(other, "untitled.flac");
    }

    public void Dispose() => lib.Dispose();

    HashSet<string> Find(string text) =>
        LibrarySearch.Find(lib.Conn, SearchQuery.Parse(text)).Ids
            .Select(id => (string)lib.Scalar("SELECT name FROM entries WHERE id = $id", ("$id", id))!)
            .ToHashSet();

    [Fact]
    public void A_substring_matches_mid_word()
    {
        Assert.Equal(["Glove.mp3", "lovely.opus"], Find("ove"));
        Assert.Equal(["01 Hunter.flac"], Find("unte"));
    }

    [Fact]
    public void Case_and_accents_are_ignored_both_ways()
    {
        HashSet<string> bjork = ["Björk - Homogenic", "01 Hunter.flac", "02 Jóga.flac"];
        Assert.Equal(bjork, Find("BJORK"));
        Assert.Equal(["02 Jóga.flac"], Find("jóga"));
        Assert.Equal(["02 Jóga.flac"], Find("joga"));
        Assert.Equal(bjork, Find("Bjôrk"));
    }

    [Fact]
    public void Names_tags_and_the_date_are_matched_not_the_genre()
    {
        Assert.Equal(["Glove.mp3"], Find("Various"));
        Assert.Empty(Find("Electronic"));
        Assert.Equal(["01 Hunter.flac"], Find("genre:tron"));
        Assert.Equal(["01 Hunter.flac"], Find("1997"));
        Assert.Equal(["01 Hunter.flac"], Find("09-2"));
        Assert.Equal(["01 Hunter.flac", "Glove.mp3"], Find("01"));
        Assert.Empty(Find("Homogenic 1997"));
    }

    [Fact]
    public void One_and_two_characters()
    {
        Assert.Equal(["Glove.mp3", "lovely.opus"], Find("Lo"));
        Assert.Equal(["01 Hunter.flac", "untitled.flac"], Find("un"));
        HashSet<string> withO = ["01 Hunter.flac", "02 Jóga.flac", "Glove.mp3", "lovely.opus", "Björk - Homogenic", "Radiohead"];
        Assert.Equal(withO, Find("ö"));
        Assert.Equal(withO, Find("ó"));
        Assert.Empty(Find("%"));
        Assert.Empty(Find("_"));
    }

    [Theory]
    [InlineData("AND")]
    [InlineData("glove AND x")]
    [InlineData("glo*")]
    [InlineData("a\"b")]
    [InlineData("artist : x")]
    [InlineData("(glove")]
    [InlineData("NEAR(a b)")]
    [InlineData("x OR y")]
    [InlineData("-glove")]
    [InlineData("^glove")]
    public void Fts_syntax_is_literal(string text)
    {
        lib.Track(other, "odd.flac", title: $"weird {text} title");
        Assert.Contains("odd.flac", Find(text));
    }

    [Fact]
    public void Quoted_phrases_are_anded_with_the_rest()
    {
        Assert.Equal(["lovely.opus"], Find("\"the cure\""));
        Assert.Equal(["lovely.opus"], Find("lovely \"cure\""));
        Assert.Empty(Find("\"glove\" \"cure\""));
        Assert.Equal(["  a  b "], SearchQuery.Parse("\"  a  b \"").Text);
    }

    [Fact]
    public void Only_folders_and_audio_files_are_results()
    {
        lib.Unread(other, "glove.txt");
        lib.Unread(other, "glove.cue");
        lib.Folder(other, "Gloves");
        Assert.Equal(["Glove.mp3", "Gloves"], Find("glove"));
        Assert.Empty(Find("Music"));  // not the root's top entry, whose name is the root path
    }

    [Fact]
    public void Names_are_found_before_the_tags_are_read()
    {
        var takk = lib.Folder(lib.Top, "Sigur Rós - Takk");
        lib.Unread(takk, "01 Takk.flac");
        lib.Unread(takk, "02 Glósóli.FLAC");
        Assert.Equal(["Sigur Rós - Takk", "01 Takk.flac"], Find("takk"));
        Assert.Equal(["02 Glósóli.FLAC"], Find("glosoli"));
        Assert.Equal(["Sigur Rós - Takk", "02 Glósóli.FLAC"], Find("ós"));
        Assert.Equal(["01 Takk.flac"], Find("ext:flac takk"));
        HashSet<string> unread = ["Sigur Rós - Takk", "01 Takk.flac", "02 Glósóli.FLAC"];
        foreach (var text in new[] { "rg:none", "rg:partial", "year:2", "artist:s", "genre:e" })
            Assert.Empty(Find(text).Intersect(unread));
        Assert.Equal(["Glove.mp3", "lovely.opus", "untitled.flac"], Find("rg:none"));
    }

    [Fact]
    public void The_query_is_parsed_into_text_and_filters()
    {
        var query = SearchQuery.Parse("artist:\"Sigur Rós\" album:Takk year:2005 EXT:Opus rg:none Re: stuff \"x y\"");
        Assert.Equal(["Re: stuff", "x y"], query.Text);
        Assert.Equal(["Sigur Rós"], query.Filters["artist"]);
        Assert.Equal(["Takk"], query.Filters["album"]);
        Assert.Equal(["2005"], query.Filters["year"]);
        Assert.Equal([".opus"], query.Filters["ext"]);
        Assert.Equal(["none"], query.Filters["rg"]);
        Assert.Empty(query.Errors);
        var bad = SearchQuery.Parse("rg:maybe artist: hello");
        Assert.Contains("rg:maybe", bad.Errors[0]);
        Assert.Equal(["hello"], bad.Text);
        Assert.Empty(bad.Filters);
        Assert.True(SearchQuery.Parse("   ").IsEmpty);
    }

    [Fact]
    public void Text_filters()
    {
        Assert.Equal(["01 Hunter.flac", "02 Jóga.flac"], Find("artist:bjork"));
        Assert.Equal(["Glove.mp3"], Find("artist:various"));
        Assert.Equal(["01 Hunter.flac", "02 Jóga.flac", "lovely.opus"], Find("artist:bjork artist:cure"));
        Assert.Equal(["01 Hunter.flac"], Find("artist:bjork hunt"));
        Assert.Equal(["01 Hunter.flac", "02 Jóga.flac"], Find("album:homo"));
        Assert.Equal(["Glove.mp3"], Find("artist:ra"));
    }

    [Fact]
    public void Value_filters()
    {
        Assert.Equal(["01 Hunter.flac"], Find("year:1997"));
        Assert.Equal(["Glove.mp3"], Find("year:200"));
        Assert.Equal(["lovely.opus"], Find("ext:opus"));
        Assert.Equal(["Glove.mp3"], Find("ext:.MP3"));
        Assert.Equal(["01 Hunter.flac"], Find("rg:full"));
        Assert.Equal(["02 Jóga.flac"], Find("rg:partial"));
    }

    [Fact]
    public void Results_come_in_natural_folder_then_name_order_with_a_cap_and_a_total()
    {
        using var index = new HandIndex();
        var folders = new[] { "B10", "B2", "a" }.Select(name => index.Folder(index.Top, name)).ToList();
        foreach (var folder in folders)
            foreach (var name in new[] { "t10.flac", "t9.flac", "T1.flac" })
                index.Track(folder, name, title: "song");
        var inner = index.Folder(folders[1], "inner");
        index.Track(inner, "t1.flac", title: "song");
        var result = LibrarySearch.Find(index.Conn, SearchQuery.Parse("song"));
        Assert.Equal(10, result.Total);
        var paths = result.Ids.Select(id => Queries.PathOf(index.Conn, id)!["C:\\Music\\".Length..]).ToList();
        Assert.Equal(["a\\T1.flac", "a\\t9.flac", "a\\t10.flac", "B2\\T1.flac", "B2\\t9.flac", "B2\\t10.flac",
            "B2\\inner\\t1.flac", "B10\\T1.flac", "B10\\t9.flac", "B10\\t10.flac"], paths);
        var capped = LibrarySearch.Find(index.Conn, SearchQuery.Parse("song"), limit: 4);
        Assert.Equal(result.Ids[..4], capped.Ids);
        Assert.Equal(10, capped.Total);
    }

    [Fact]
    public void Folding_keeps_offsets_into_the_original()
    {
        void Check(string text, string folded, int[] offsets)
        {
            var (f, o) = Folding.FoldWithOffsets(text);
            Assert.Equal(folded, f);
            Assert.Equal(offsets, o);
        }

        Check("Jóga", "joga", [0, 1, 2, 3]);
        Check("Straße", "strasse", [0, 1, 2, 3, 4, 4, 5]);
        Check("Jóga", "joga", [0, 1, 3, 4]);  // "o" and its accent are one character, at 1
        Assert.Equal([(4, 2), (7, 3)], Folding.Spans("Straße Jóga", ["sse", "jog"]));
        Assert.Equal([(0, 4)], Folding.Spans("abab", ["ab"]));
        Assert.Equal([(0, 3)], Folding.Spans("aaa", ["aa", "a"]));
    }

    [Fact]
    public void Rows_carry_their_folder_and_highlights()
    {
        var query = SearchQuery.Parse("homogenic artist:bjö");
        var ids = LibrarySearch.Find(lib.Conn, SearchQuery.Parse("homogenic")).Ids;
        var rows = LibrarySearch.Rows(lib.Conn, ids, query, TestContext.Current.CancellationToken);
        Assert.Equal(["Björk - Homogenic", "01 Hunter.flac", "02 Jóga.flac"], rows.Select(r => r.Name));
        var folder = rows[0];
        Assert.True(folder.IsDir);
        Assert.Equal(album, folder.Id);
        Assert.Equal("C:\\Music", folder.Folder);
        Assert.Equal(lib.Top, folder.FolderId);
        Assert.Null(folder.TrackCount);
        var hunter = rows[1];
        Assert.Equal("C:\\Music\\Björk - Homogenic", hunter.Folder);
        Assert.Equal([(0, 3)], hunter.ArtistSpans);
        Assert.Equal(ReplayGainState.Full, hunter.ReplayGain);
        Assert.Equal("pending", hunter.Lrclib);
        Assert.Equal(ReplayGainState.Partial, rows[2].ReplayGain);
    }

    [Fact]
    public void Rows_gone_meanwhile_are_dropped()
    {
        var ids = LibrarySearch.Find(lib.Conn, SearchQuery.Parse("homogenic")).Ids;
        lib.Scalar("DELETE FROM entries WHERE id = $id", ("$id", ids[1]));
        Assert.Equal(2, LibrarySearch.Rows(lib.Conn, ids, SearchQuery.Parse("homogenic"), TestContext.Current.CancellationToken).Count);
    }

    [Fact]
    public void A_deleted_folder_takes_its_search_rows_along()
    {
        lib.Scalar("DELETE FROM entries WHERE id = $id", ("$id", album));
        // Radiohead and its three tracks are left: the album's tracks went with it (a cascade).
        Assert.Equal(4L, lib.Scalar("SELECT count(*) FROM search WHERE rowid != $top", ("$top", lib.Top)));
        Assert.Empty(Find("hunter"));
    }
}
