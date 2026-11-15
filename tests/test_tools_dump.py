import contextlib
import hashlib
import sqlite3
import subprocess
import threading
from pathlib import Path

import pytest

from src import config
from src.tools import dump, service_restart, system

ACTIVE = "ActiveState=active\nUnitFileState=enabled\nMainPID=42\nNRestarts=0\nActiveEnterTimestamp=@1792319840\n"
FAILED = "ActiveState=failed\nUnitFileState=enabled\nMainPID=0\nNRestarts=0\nActiveEnterTimestamp=\n"
OLD_SESSION_START = 1792300000.0
NEW_SESSION_START = 1792319840.0
PLENTY_OF_SPACE = 10_000_000_000


def execute(sql: str, *params):
    with contextlib.closing(sqlite3.connect(config.DB_PATH)) as conn:
        conn.execute(sql, params)
        conn.commit()


def add_session(started_at: float) -> int:
    with contextlib.closing(sqlite3.connect(config.DB_PATH)) as conn:
        session_id = conn.execute("INSERT INTO sessions (started_at) VALUES (?)", (started_at,)).lastrowid
        conn.commit()
        return session_id


def add_measurement(session_id: int, timestamp: float, value: float = 1000.0):
    execute("INSERT INTO measurements (timestamp, value, session_id) VALUES (?, ?, ?)", timestamp, value, session_id)


def start_new_session():
    session_id = add_session(NEW_SESSION_START)
    add_measurement(session_id, NEW_SESSION_START + 1)


class FakeRun:
    def __init__(self):
        self.calls = []
        self.restart_status = 0
        self.restart_stderr = ""
        self.show_output = ACTIVE
        self.on_restart = start_new_session

    def __call__(self, args, **kwargs):
        self.calls.append(args)
        if args[:2] == ["systemctl", "show"]:
            return subprocess.CompletedProcess(args, 0, self.show_output, "")
        if self.restart_status == 0 and self.on_restart:
            self.on_restart()
        return subprocess.CompletedProcess(args, self.restart_status, "", self.restart_stderr)

    @property
    def restarted(self) -> bool:
        return any("restart" in call for call in self.calls)


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    monkeypatch.setenv("NO_COLOR", "1")
    monkeypatch.setattr(service_restart.time, "sleep", lambda _: None)
    monkeypatch.setattr(dump, "DUMP_DIR", tmp_path / "dumps")
    monkeypatch.setattr(dump, "NEW_SESSION_TIMEOUT", 0.0)
    monkeypatch.setattr(service_restart, "RESTART_TIMEOUT", 0.0)
    monkeypatch.setattr(system, "free_disk_bytes", lambda _: PLENTY_OF_SPACE)


@pytest.fixture
def fake_run(monkeypatch):
    fake = FakeRun()
    monkeypatch.setattr(system.subprocess, "run", fake)
    return fake


@pytest.fixture
def old_session() -> int:
    session_id = add_session(OLD_SESSION_START)
    for second in range(5):
        add_measurement(session_id, OLD_SESSION_START + second)
    execute(
        "INSERT INTO temperature_measurements (timestamp, sensor_address, value, session_id) VALUES (?, ?, ?, ?)",
        OLD_SESSION_START, "28-0000", 12.5, session_id,
    )
    execute("INSERT INTO pin_states (timestamp, pin, value, session_id) VALUES (?, ?, ?, ?)", OLD_SESSION_START, 2, 1, session_id)
    return session_id


def run_main(capsys, *argv: str) -> tuple[int, str]:
    code = dump.main(list(argv))
    return code, capsys.readouterr().out


def dump_path():
    return dump.DUMP_DIR / dump.DUMP_NAME


def count_rows(path, table: str) -> int:
    with contextlib.closing(sqlite3.connect(path)) as conn:
        return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


def test_copy_of_wal_database_during_writes_is_consistent_standalone_file():
    session_id = add_session(OLD_SESSION_START)
    wal = Path(config.DB_PATH + "-wal")
    stop = threading.Event()
    baseline_written = threading.Event()

    def write_continuously():
        with contextlib.closing(sqlite3.connect(config.DB_PATH, timeout=5.0)) as conn:
            conn.execute("PRAGMA wal_autocheckpoint=0")
            second = 0
            while not stop.is_set():
                conn.execute(
                    "INSERT INTO measurements (timestamp, value, session_id) VALUES (?, ?, ?)",
                    (OLD_SESSION_START + second, 1000.0, session_id),
                )
                conn.commit()
                second += 1
                if second == 200:
                    baseline_written.set()

    writer = threading.Thread(target=write_continuously)
    writer.start()
    try:
        assert baseline_written.wait(timeout=10)
        assert wal.stat().st_size > 0
        dump.DUMP_DIR.mkdir(parents=True)
        result = dump.make_copy()
    finally:
        stop.set()
        writer.join()

    copy = dump_path()
    assert result.level is dump.Level.OK
    assert not copy.with_name(copy.name + "-wal").exists()
    assert not copy.with_name(copy.name + "-shm").exists()
    assert not (dump.DUMP_DIR / dump.PART_NAME).exists()
    assert copy.read_bytes()[18:20] == b"\x01\x01"
    assert count_rows(copy, "measurements") >= 200

    with contextlib.closing(sqlite3.connect(copy)) as conn:
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "delete"


def test_successful_dump_restarts_copies_and_summarizes(fake_run, old_session, capsys):
    code, out = run_main(capsys)

    assert code == dump.EXIT_OK
    assert ["sudo", "-n", "systemctl", "restart", "measurement-data-saver.service"] in fake_run.calls
    assert "Usługa zapisu danych uruchomiona ponownie." in out
    assert f"Nowa sesja {old_session + 1} zapisuje wskazania." in out
    assert "Kopia bazy gotowa" in out
    assert f"Sesja {old_session} zakończona zgraniem: wskazania 2026-10-18" in out
    assert "5 wskazań, 1 odczyt temperatury, 1 zmiana sygnałów." in out
    assert f"Nowa sesja {old_session + 1}: od 2026-10-18 " in out
    assert f"Kopia do pobrania: {dump_path()}" in out
    assert count_rows(dump_path(), "sessions") == 2


def test_checksum_file_matches_copy(fake_run, old_session, capsys):
    code, out = run_main(capsys)

    checksum = (dump.DUMP_DIR / dump.CHECKSUM_NAME).read_text()
    expected = hashlib.sha256(dump_path().read_bytes()).hexdigest()

    assert code == dump.EXIT_OK
    assert checksum == expected + "\n"
    assert f"SHA-256: {expected}" in out


def test_not_enough_space_skips_restart_and_copy(fake_run, old_session, monkeypatch, capsys):
    monkeypatch.setattr(system, "free_disk_bytes", lambda _: 1_000_000)

    code, out = run_main(capsys)

    assert code == dump.EXIT_NO_DUMP
    assert "Za mało miejsca na karcie" in out
    assert "Baza nie została zgrana." in out
    assert not fake_run.restarted
    assert not dump_path().exists()


def test_failed_restart_still_copies(fake_run, old_session, capsys):
    fake_run.restart_status = 1
    fake_run.restart_stderr = "Job for measurement-data-saver.service failed.\n"
    fake_run.show_output = FAILED

    code, out = run_main(capsys)

    assert code == dump.EXIT_PROBLEMS
    assert "Usługa nie uruchomiła się ponownie (stan: failed)." in out
    assert "Nowa sesja" not in out
    assert f"Sesja {old_session} trwa dalej (bez restartu)" in out
    assert dump_path().exists()
    assert dump.CHECK_HINT in out


def test_restart_without_permission_shows_install_hint(fake_run, old_session, capsys):
    fake_run.restart_status = 1
    fake_run.restart_stderr = "sudo-rs: interactive authentication is required\n"

    code, out = run_main(capsys)

    assert code == dump.EXIT_PROBLEMS
    assert "Brak uprawnień do ponownego uruchomienia usługi." in out
    assert "deploy/install-tools.sh" in out
    assert dump_path().exists()


def test_new_session_without_measurements_warns_and_still_copies(fake_run, old_session, capsys):
    fake_run.on_restart = None

    code, out = run_main(capsys)

    assert code == dump.EXIT_PROBLEMS
    assert "Nowa sesja nie zapisała wskazań w ciągu 0 s." in out
    assert dump.CHECK_HINT in out
    assert dump_path().exists()


def test_old_dump_files_are_removed_before_dump(fake_run, old_session, monkeypatch, capsys):
    dump.DUMP_DIR.mkdir(parents=True)
    (dump.DUMP_DIR / dump.PART_NAME).write_text("stary")
    (dump.DUMP_DIR / dump.CHECKSUM_NAME).write_text("stary")
    (dump.DUMP_DIR / dump.DUMP_NAME).write_text("stary")
    monkeypatch.setattr(system, "free_disk_bytes", lambda _: 1_000_000)

    code, _ = run_main(capsys)

    assert code == dump.EXIT_NO_DUMP
    assert not any((dump.DUMP_DIR / name).exists() for name in dump.DUMP_FILES)


def test_session_summary_counts_rows_of_one_session(old_session):
    start_new_session()

    with contextlib.closing(sqlite3.connect(config.DB_PATH)) as conn:
        summary = dump.read_session_summary(conn, old_session)

    assert summary.id == old_session
    assert summary.started_at == OLD_SESSION_START
    assert summary.first == OLD_SESSION_START
    assert summary.last == OLD_SESSION_START + 4
    assert (summary.measurements, summary.temperatures, summary.pin_changes) == (5, 1, 1)


def test_cleanup_removes_only_dump_files(capsys):
    dump.DUMP_DIR.mkdir(parents=True)
    for name in dump.DUMP_FILES:
        (dump.DUMP_DIR / name).write_text("zrzut")
    other = dump.DUMP_DIR / "notatka.txt"
    other.write_text("zostaje")

    code, out = run_main(capsys, "--cleanup")

    assert code == dump.EXIT_OK
    assert "Kopia bazy usunięta z Raspberry Pi." in out
    assert not any((dump.DUMP_DIR / name).exists() for name in dump.DUMP_FILES)
    assert other.exists()


def test_checksum_write_failure_removes_copy(fake_run, old_session, monkeypatch, capsys):
    def fail(_):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(dump, "sha256", fail)

    code, out = run_main(capsys)

    assert code == dump.EXIT_NO_DUMP
    assert "Nie udało się zapisać sumy SHA-256 kopii (No space left on device)." in out
    assert "Baza nie została zgrana." in out
    assert not any((dump.DUMP_DIR / name).exists() for name in dump.DUMP_FILES)


def test_dump_dir_failure_is_reported_without_restart(fake_run, old_session, monkeypatch, tmp_path, capsys):
    blocker = tmp_path / "plik"
    blocker.write_text("")
    monkeypatch.setattr(dump, "DUMP_DIR", blocker / "dumps")

    code, out = run_main(capsys)

    assert code == dump.EXIT_NO_DUMP
    assert "Nie można przygotować katalogu kopii" in out
    assert not fake_run.restarted


def test_leftover_part_journal_is_removed(fake_run, old_session, capsys):
    dump.DUMP_DIR.mkdir(parents=True)
    (dump.DUMP_DIR / dump.PART_JOURNAL_NAME).write_text("stary")

    code, _ = run_main(capsys)

    assert code == dump.EXIT_OK
    assert not (dump.DUMP_DIR / dump.PART_JOURNAL_NAME).exists()
