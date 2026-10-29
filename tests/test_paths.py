from music_desk import paths


def test_bundled_rsgain_exists():
    assert paths.rsgain_path().is_file()
    assert paths.rsgain_path() == paths.resource_path("rsgain", "rsgain.exe")


def test_app_data_dir_uses_localappdata(monkeypatch, tmp_path):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    assert paths.app_data_dir() == tmp_path / "Refrain"
    assert paths.logs_dir().is_dir()
    assert paths.logs_dir() == tmp_path / "Refrain" / "logs"


def old_data(base):
    """MusicDesk's app data as 0.11 left it."""
    old = base / "MusicDesk"
    (old / "logs").mkdir(parents=True)
    (old / "logs" / "app.log").write_text("log", encoding="utf-8")
    (old / "library.db").write_bytes(b"db")
    (old / "settings.json").write_text("{}", encoding="utf-8")
    (old / "menu").mkdir()
    return old


def test_moving_musicdesk_data_into_refrain(monkeypatch, tmp_path):
    """SPEC §36 task 6: everything moves, and MusicDesk's folder goes."""
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    old_data(tmp_path)
    moved, left = paths.move_old_app_data()
    assert moved == ["library.db", "logs", "menu", "settings.json"] and left == []
    assert (tmp_path / "Refrain" / "library.db").read_bytes() == b"db"
    assert (tmp_path / "Refrain" / "logs" / "app.log").is_file()
    assert not (tmp_path / "MusicDesk").exists()
    assert paths.move_old_app_data() == ([], [])  # once only


def test_what_refrain_already_has_stays(monkeypatch, tmp_path):
    """The right-click menu may have made Refrain's menu folder first: the rest still moves, MusicDesk's empty menu
    folder goes, and a file Refrain already has is never overwritten (MusicDesk's folder then stays, with it)."""
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    old = old_data(tmp_path)
    (tmp_path / "Refrain" / "menu").mkdir(parents=True)
    (tmp_path / "Refrain" / "settings.json").write_text('{"new": 1}', encoding="utf-8")
    moved, left = paths.move_old_app_data()
    assert moved == ["library.db", "logs"] and left == []
    assert (tmp_path / "Refrain" / "settings.json").read_text(encoding="utf-8") == '{"new": 1}'
    assert not (old / "menu").exists() and (old / "settings.json").is_file()


def test_a_file_in_use_is_left_for_the_next_start(monkeypatch, tmp_path):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    old = old_data(tmp_path)
    real_rename = paths.Path.rename

    def busy(self, target):
        if self.name == "library.db":
            raise PermissionError(13, "The file is in use")
        return real_rename(self, target)

    monkeypatch.setattr(paths.Path, "rename", busy)
    moved, left = paths.move_old_app_data()
    assert "library.db" not in moved and left == ["library.db (The file is in use)"]
    assert (old / "library.db").is_file()
    monkeypatch.setattr(paths.Path, "rename", real_rename)
    assert paths.move_old_app_data() == (["library.db"], [])
    assert not old.exists()
