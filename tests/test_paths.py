from music_desk import paths


def test_bundled_rsgain_exists():
    assert paths.rsgain_path().is_file()
    assert paths.rsgain_path() == paths.resource_path("rsgain", "rsgain.exe")


def test_app_data_dir_uses_localappdata(monkeypatch, tmp_path):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    assert paths.app_data_dir() == tmp_path / "MusicDesk"
    assert paths.logs_dir().is_dir()
    assert paths.logs_dir() == tmp_path / "MusicDesk" / "logs"
