import sqlite3
from unittest.mock import MagicMock, patch

import pytest

from src import config, database


def all_tables(conn) -> set[str]:
    rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    return {row[0] for row in rows}


# =============================================================================
# init_db - schemat i tryb WAL
# =============================================================================

def test_init_db_creates_all_tables():
    with sqlite3.connect(config.DB_PATH) as conn:
        tables = all_tables(conn)

    assert {"sessions", "measurements", "temperature_measurements", "pin_states"} <= tables


def test_init_db_enables_wal_mode():
    with sqlite3.connect(config.DB_PATH) as conn:
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
    assert mode.lower() == "wal"


def test_init_db_is_idempotent():
    # Wywołanie drugi raz na tej samej bazie nie powinno nic zepsuć
    database.init_db()
    with sqlite3.connect(config.DB_PATH) as conn:
        tables = all_tables(conn)
    assert {"sessions", "measurements", "temperature_measurements", "pin_states"} <= tables


def test_init_db_migrates_old_measurements_table_without_session_id(tmp_path):
    """
    Symuluje bazę sprzed wprowadzenia session_id - init_db powinno dodać
    kolumnę bez utraty istniejących danych.
    """
    db_path = tmp_path / "old_style.db"
    config.DB_PATH = str(db_path)

    with sqlite3.connect(db_path) as conn:
        conn.execute("""
            CREATE TABLE measurements (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp REAL NOT NULL,
                value REAL NOT NULL
            )
        """)
        conn.execute("INSERT INTO measurements (timestamp, value) VALUES (500.0, 10.0)")
        conn.commit()

    database.init_db()

    with sqlite3.connect(db_path) as conn:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(measurements)").fetchall()}
        rows = conn.execute("SELECT timestamp, value, session_id FROM measurements").fetchall()

    assert "session_id" in columns
    assert rows == [(500.0, 10.0, None)]  # stary wiersz dostaje session_id = NULL


# =============================================================================
# create_session
# =============================================================================

def test_create_session_returns_incrementing_ids():
    sid1 = database.create_session()
    sid2 = database.create_session()
    assert sid2 == sid1 + 1


def test_create_session_persists_started_at():
    sid = database.create_session()
    with sqlite3.connect(config.DB_PATH) as conn:
        row = conn.execute("SELECT started_at FROM sessions WHERE id = ?", (sid,)).fetchone()
    assert row is not None
    assert row[0] > 0


# =============================================================================
# open_worker_connection - jedno długożyjące połączenie, nie per-zapis
# =============================================================================

def test_open_worker_connection_returns_usable_connection():
    conn = database.open_worker_connection("test")
    try:
        conn.execute("SELECT 1")
    finally:
        conn.close()


def test_same_connection_reused_across_multiple_saves_does_not_leak_fds():
    """
    Regresja dla błędu 'unable to open database file' po ~4 minutach działania:
    wcześniej KAŻDY zapis otwierał nowe połączenie (i nigdy go nie zamykał,
    bo `with sqlite3.connect(...) as conn` NIE wywołuje close()). Sprawdzamy,
    że liczba otwartych deskryptorów plików procesu NIE ROŚNIE w trakcie wielu
    kolejnych zapisów na tym samym, długożyjącym połączeniu.

    Uwaga: assert używa "<=", nie "==". W pełnym zestawie testów (uruchamianym
    razem z innymi plikami, np. run_e2e_test.py otwierającym pty i wątki)
    liczba FD całego procesu może w międzyczasie spaść z powodu GC/sprzątania
    niepowiązanych zasobów z WCZEŚNIEJSZYCH testów - to nie jest wyciek.
    Wyciek objawia się WZROSTEM, więc tylko to sprawdzamy.
    """
    import gc
    import os

    if not os.path.isdir(f"/proc/{os.getpid()}/fd"):
        pytest.skip("Test wymaga /proc/<pid>/fd (Linux)")

    def fd_count():
        return len(os.listdir(f"/proc/{os.getpid()}/fd"))

    sid = database.create_session()
    conn = database.open_worker_connection("test")
    try:
        for i in range(50):
            database.save_measurement(conn, float(i), float(i), sid)

        gc.collect()  # normalizujemy stan przed pomiarem bazowym
        baseline = fd_count()

        for i in range(200):
            database.save_measurement(conn, float(i), float(i), sid)

        after = fd_count()
        assert after <= baseline, (
            f"Liczba otwartych deskryptorów wzrosła z {baseline} do {after} "
            f"po 200 dodatkowych zapisach - podejrzenie wycieku FD."
        )
    finally:
        conn.close()


# =============================================================================
# save_measurement
# =============================================================================

def test_save_measurement_success():
    sid = database.create_session()
    conn = database.open_worker_connection("test")
    try:
        result = database.save_measurement(conn, 1718580000.0, 23.45, sid)
        assert result is True

        row = conn.execute("SELECT timestamp, value, session_id FROM measurements").fetchone()
        assert row == (1718580000.0, 23.45, sid)
    finally:
        conn.close()


class _FakeConnection:
    """
    sqlite3.Connection.cursor() to metoda zdefiniowana na poziomie C - nie da
    się jej podmienić przez unittest.mock.patch.object na konkretnej instancji
    (atrybut jest "read-only" z perspektywy instancji). Zamiast tego używamy
    prostego obiektu duck-typingowego: _run_with_retry() potrzebuje tylko
    .cursor() i .commit(), więc to w zupełności wystarcza do testowania logiki
    retry/fatal bez dotykania prawdziwego pliku SQLite.
    """
    def __init__(self, cursor_mock):
        self._cursor_mock = cursor_mock
        self.commit_calls = 0

    def cursor(self):
        return self._cursor_mock

    def commit(self):
        self.commit_calls += 1


def test_save_measurement_heals_on_transient_lock_error():
    """
    Pierwsza próba trafia na chwilowy błąd operacyjny ('database is locked'),
    druga się udaje - save_measurement powinno zwrócić True po jednym,
    krótkim odczekaniu (backoff dla kontencji, NIE ten sam co dla
    unable-to-open-file).
    """
    sid = 1
    mock_cursor = MagicMock()
    mock_cursor.execute.side_effect = [sqlite3.OperationalError("database is locked"), None]
    fake_conn = _FakeConnection(mock_cursor)

    with patch("time.sleep") as mock_sleep:
        result = database.save_measurement(fake_conn, 1.0, 2.0, sid)

    assert result is True
    assert fake_conn.commit_calls == 1
    mock_sleep.assert_called_once_with(0.2)


def test_save_measurement_does_not_raise_fatal_on_persistent_lock_error():
    """
    REGRESJA: 'database is locked' to zwykle chwilowa kontencja między dwoma
    wątkami piszącymi do tego samego pliku WAL - NIE powinno restartować
    całej usługi tak jak 'unable to open database file'. Nawet gdy blokada
    utrzymuje się przez wszystkie próby retry, save_measurement powinno
    zwrócić False (utrata jednego zapisu), a NIE rzucić DatabaseFatalError.
    """
    sid = 1
    mock_cursor = MagicMock()
    mock_cursor.execute.side_effect = sqlite3.OperationalError("database is locked")
    fake_conn = _FakeConnection(mock_cursor)

    with patch("time.sleep") as mock_sleep:
        result = database.save_measurement(fake_conn, 1.0, 2.0, sid)

    assert result is False
    assert fake_conn.commit_calls == 0
    # 3 próby backoff (0.2s, 0.5s, 1.0s), żadna nie eskaluje do sys.exit/fatal
    assert mock_sleep.call_count == 3


def test_save_measurement_raises_fatal_on_persistent_open_error():
    """
    Trwały błąd 'unable to open database file' przy obu próbach powinien
    rzucić DatabaseFatalError (NIE sys.exit - patrz docstring w database.py
    dlaczego sys.exit z wątku roboczego by nie zadziałało). To pozostaje
    fatalne w odróżnieniu od 'database is locked' - patrz test wyżej.
    """
    sid = 1
    mock_cursor = MagicMock()
    mock_cursor.execute.side_effect = sqlite3.OperationalError("unable to open database file")
    fake_conn = _FakeConnection(mock_cursor)

    with patch("time.sleep"):
        with pytest.raises(database.DatabaseFatalError):
            database.save_measurement(fake_conn, 1.0, 2.0, sid)


def test_save_measurement_returns_false_on_other_operational_error():
    """Błąd operacyjny inny niż lock/unable-to-open nie powinien być fatalny -
    tylko zwrócić False po dwóch próbach."""
    sid = 1
    mock_cursor = MagicMock()
    mock_cursor.execute.side_effect = sqlite3.OperationalError("disk I/O error")
    fake_conn = _FakeConnection(mock_cursor)

    result = database.save_measurement(fake_conn, 1.0, 2.0, sid)

    assert result is False


# =============================================================================
# save_temperature_batch
# =============================================================================

def test_save_temperature_batch_multiple_sensors():
    sid = database.create_session()
    conn = database.open_worker_connection("test")
    try:
        readings = [("AAA", 25.5), ("BBB", 26.1)]
        result = database.save_temperature_batch(conn, 100.0, readings, sid)
        assert result is True

        rows = conn.execute(
            "SELECT sensor_address, value, session_id FROM temperature_measurements ORDER BY id"
        ).fetchall()
        assert rows == [("AAA", 25.5, sid), ("BBB", 26.1, sid)]
    finally:
        conn.close()


def test_save_temperature_batch_empty_list_is_noop():
    sid = database.create_session()
    conn = database.open_worker_connection("test")
    try:
        result = database.save_temperature_batch(conn, 100.0, [], sid)
        assert result is True
        count = conn.execute("SELECT COUNT(*) FROM temperature_measurements").fetchone()[0]
        assert count == 0
    finally:
        conn.close()


# =============================================================================
# save_pin_states_batch
# =============================================================================

def test_save_pin_states_batch_writes_all_changes():
    sid = database.create_session()
    conn = database.open_worker_connection("test")
    try:
        result = database.save_pin_states_batch(conn, 200.0, {4: 1, 5: 0}, sid)
        assert result is True

        rows = conn.execute(
            "SELECT pin, value, session_id FROM pin_states ORDER BY pin"
        ).fetchall()
        assert rows == [(4, 1, sid), (5, 0, sid)]
    finally:
        conn.close()


def test_save_pin_states_batch_empty_dict_is_noop():
    sid = database.create_session()
    conn = database.open_worker_connection("test")
    try:
        result = database.save_pin_states_batch(conn, 200.0, {}, sid)
        assert result is True
        count = conn.execute("SELECT COUNT(*) FROM pin_states").fetchone()[0]
        assert count == 0
    finally:
        conn.close()


def test_pin_value_stored_as_integer_not_text():
    """Zgodnie z ustaleniami: pin ma być liczbą, nie tekstem (oszczędność
    miejsca, bo zawsze jest numeryczny w danych z Arduino)."""
    sid = database.create_session()
    conn = database.open_worker_connection("test")
    try:
        database.save_pin_states_batch(conn, 1.0, {4: 1}, sid)
        pin_type = conn.execute(
            "SELECT typeof(pin) FROM pin_states LIMIT 1"
        ).fetchone()[0]
        assert pin_type == "integer"
    finally:
        conn.close()