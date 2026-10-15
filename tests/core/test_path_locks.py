"""core/path_locks.py: jobs own folder trees, saves own files, overlaps are refused with the owner's name."""

import threading

import pytest

from music_desk.core.path_locks import LockedError, PathLocks


def test_a_folder_lock_covers_its_tree(tmp_path):
    locks = PathLocks()
    with locks.lock([tmp_path / "Album"], "ReplayGain (album)"):
        assert locks.owner(tmp_path / "Album") == "ReplayGain (album)"
        assert locks.owner(tmp_path / "album" / "CD2" / "01.flac") == "ReplayGain (album)"  # ignoring case
        assert locks.owner(tmp_path / "Album 2" / "01.flac") is None  # a sibling with a longer name
        assert locks.owner(tmp_path) is None  # the parent itself isn't locked
    assert locks.owner(tmp_path / "Album" / "01.flac") is None


@pytest.mark.parametrize(("held", "wanted"), [
    ("Album", "Album/01.flac"),  # a save of a file a job owns
    ("Album/01.flac", "Album"),  # a job on a folder with a save running inside
    ("Album", "ALBUM"),
    ("Artist", "Artist/Album"),
])
def test_overlaps_are_refused(tmp_path, held, wanted):
    locks = PathLocks()
    locks.acquire([tmp_path / held], "the first")
    with pytest.raises(LockedError) as error:
        locks.acquire([tmp_path / wanted], "the second")
    assert error.value.owner == "the first" and str(tmp_path / wanted) == error.value.path
    assert "in use by the first" in str(error.value)


def test_separate_trees_and_release(tmp_path):
    locks = PathLocks()
    a = locks.acquire([tmp_path / "A"], "job A")
    locks.acquire([tmp_path / "B", tmp_path / "C" / "x.flac"], "job B")
    assert [owner for owner, _paths in locks.held()] == ["job A", "job B"]
    locks.release(a)
    locks.release(a)  # twice is harmless
    locks.acquire([tmp_path / "A" / "x.flac"], "a tag save")
    with pytest.raises(LockedError):
        locks.acquire([tmp_path / "C"], "job C")


def test_lock_is_released_on_error(tmp_path):
    locks = PathLocks()
    with pytest.raises(ValueError), locks.lock([tmp_path], "job"):
        raise ValueError("boom")
    assert locks.held() == []


def test_listeners(tmp_path):
    locks = PathLocks()
    calls = []

    def broken():
        raise RuntimeError("a deleted window")

    locks.add_listener(broken)
    locks.add_listener(lambda: calls.append(len(locks.held())))
    with locks.lock([tmp_path], "job"):
        pass
    assert calls == [1, 0]  # the broken listener didn't stop the others, nor the lock
    locks.remove_listener(broken)
    locks.remove_listener(broken)


def test_concurrent_acquires_let_one_win(tmp_path):
    locks = PathLocks()
    results = []
    barrier = threading.Barrier(8)

    def take(n):
        barrier.wait()
        try:
            locks.acquire([tmp_path / "Album"], f"job {n}")
            results.append("ok")
        except LockedError:
            results.append("refused")

    threads = [threading.Thread(target=take, args=(n,)) for n in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(results) == ["ok"] + ["refused"] * 7
