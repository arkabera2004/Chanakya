"""The lock is the only proof a writer is alive."""

import os

from chanakya.desklock import acquire, held, read_lock, release, request_stop


def test_a_dead_pid_does_not_hold_the_database(tmp_path):
    path = tmp_path / "watch.lock"
    acquire(path, {"pid": 2**30, "desk": "live", "mode": "watch", "started_at": "2026-10-10T00:00:00Z"})
    assert held(path) is None
    acquire(path, {"pid": os.getpid(), "desk": "live", "mode": "watch", "started_at": "2026-10-10T00:00:01Z"})
    assert held(path)["pid"] == os.getpid()
    assert request_stop(path)["stop"] is True
    assert read_lock(path)["stop"] is True
    release(path, os.getpid())
    assert held(path) is None
    assert not path.exists()


def test_a_command_cannot_start_while_another_writer_is_alive(tmp_path, monkeypatch):
    import pytest

    from chanakya.desklock import LockHeld, writer_lock

    path = tmp_path / "watch.lock"
    monkeypatch.setattr("chanakya.desklock.lock_path", lambda settings: path)
    with writer_lock(None, "watch"):
        assert held(path)["mode"] == "watch"
        with pytest.raises(LockHeld):
            with writer_lock(None, "analyze"):
                pass
        assert held(path)["mode"] == "watch"  # the refused command must not release the holder's lock
    assert not path.exists()


def test_cli_analyze_refuses_while_a_watch_holds_the_database(tmp_path, monkeypatch):
    from typer.testing import CliRunner

    from chanakya import cli

    path = tmp_path / "watch.lock"
    monkeypatch.setattr("chanakya.desklock.lock_path", lambda settings: path)
    acquire(path, {"pid": os.getpid(), "desk": "live", "mode": "watch", "started_at": "2026-10-10T00:00:00Z"})
    called: list[str] = []
    monkeypatch.setattr(cli, "run_pipeline", lambda *a, **k: called.append("ran"))
    result = CliRunner().invoke(cli.app, ["--config", "config/chanakya.demo.yaml", "analyze"])
    assert result.exit_code == 1
    assert called == []
    assert held(path)["mode"] == "watch"
