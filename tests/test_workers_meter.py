import logging
import sqlite3
import threading
import time

import pytest

from src import config, database
from src.heartbeat import HeartbeatMonitor, FatalErrorSignal
from src.workers import meter_worker


class FakeReader:
    """
    Zastępuje SerialReader w testach - zwraca z góry przygotowaną kolejkę
    ramek (bajty albo None), bez dotykania fizycznego portu. Śledzi też
    wywołania force_reconnect(), żeby testy mogły to zweryfikować.
    """
    def __init__(self, frames=None):
        self._frames = list(frames) if frames else []
        self.ser = None  # worker sprawdza to w finally - musi istnieć
        self.force_reconnect_calls = []

    def read_next_frame(self):
        if self._frames:
            return self._frames.pop(0)
        return None

    def force_reconnect(self, reason: str = ""):
        self.force_reconnect_calls.append(reason)


def run_worker_briefly(reader, session_id, duration: float, fatal_signal=None, heartbeat=None):
    """Uruchamia meter_worker.run() w wątku na `duration` sekund, po czym
    grzecznie zatrzymuje (stop_event) i czeka na zakończenie."""
    stop_event = threading.Event()
    heartbeat = heartbeat or HeartbeatMonitor()
    fatal_signal = fatal_signal or FatalErrorSignal()

    t = threading.Thread(
        target=meter_worker.run,
        args=(session_id, stop_event, heartbeat, fatal_signal, reader),
    )
    t.start()
    time.sleep(duration)
    stop_event.set()
    t.join(timeout=5.0)
    assert not t.is_alive(), "Wątek workera nie zakończył się w oczekiwanym czasie"
    return heartbeat, fatal_signal


def read_measurements(session_id):
    with sqlite3.connect(config.DB_PATH) as conn:
        return conn.execute(
            "SELECT timestamp, value FROM measurements WHERE session_id = ? ORDER BY id",
            (session_id,),
        ).fetchall()


@pytest.fixture(autouse=True)
def fast_save_delay():
    """Skracamy SAVE_DELAY, żeby testy nie musiały czekać całą sekundę."""
    original = config.SAVE_DELAY
    config.SAVE_DELAY = 0.05
    yield
    config.SAVE_DELAY = original


# =============================================================================
# Podstawowy zapis
# =============================================================================

def test_meter_worker_saves_parsed_value():
    session_id = database.create_session()
    reader = FakeReader(frames=[b"23.45"])

    run_worker_briefly(reader, session_id, duration=0.2)

    rows = read_measurements(session_id)
    assert len(rows) == 1
    assert rows[0][1] == 23.45


def test_meter_worker_saves_only_latest_value_within_window():
    session_id = database.create_session()
    # Trzy ramki przychodzą "naraz" (kolejka), zanim minie SAVE_DELAY -
    # do bazy powinna trafić tylko ostatnia poprawna wartość.
    reader = FakeReader(frames=[b"1.0", b"2.0", b"3.0"])

    run_worker_briefly(reader, session_id, duration=0.2)

    rows = read_measurements(session_id)
    assert len(rows) == 1
    assert rows[0][1] == 3.0


# =============================================================================
# Warningi - cisza na porcie vs niesparsowana ramka
# =============================================================================

def test_meter_worker_logs_silence_warning_when_no_frames_at_all(caplog):
    session_id = database.create_session()
    reader = FakeReader(frames=[])  # zero ramek w ogóle

    with caplog.at_level(logging.WARNING):
        run_worker_briefly(reader, session_id, duration=0.15)

    assert any("cisza na porcie" in msg for msg in caplog.messages)
    assert not read_measurements(session_id)


def test_meter_worker_logs_unparsed_frame_content_in_warning(caplog):
    session_id = database.create_session()
    reader = FakeReader(frames=[b"ERR_SENSOR_DISCONNECTED"])

    with caplog.at_level(logging.WARNING):
        run_worker_briefly(reader, session_id, duration=0.15)

    matching = [msg for msg in caplog.messages if "ERR_SENSOR_DISCONNECTED" in msg]
    assert matching, "Warning powinien zawierać treść niesparsowanej ramki"
    assert "nie odebrano poprawnej ramki danych" in matching[0]


# =============================================================================
# force_reconnect przy braku poprawnych danych
# =============================================================================

def test_meter_worker_forces_reconnect_after_no_data_timeout():
    original_timeout = config.METER_NO_DATA_TIMEOUT
    config.METER_NO_DATA_TIMEOUT = 0.1
    try:
        session_id = database.create_session()
        reader = FakeReader(frames=[])  # nigdy żadnych poprawnych danych

        run_worker_briefly(reader, session_id, duration=0.3)

        assert len(reader.force_reconnect_calls) >= 1
        assert "brak poprawnych danych przez" in reader.force_reconnect_calls[0]
    finally:
        config.METER_NO_DATA_TIMEOUT = original_timeout


def test_meter_worker_does_not_force_reconnect_when_data_keeps_flowing():
    original_timeout = config.METER_NO_DATA_TIMEOUT
    config.METER_NO_DATA_TIMEOUT = 10.0  # wysoki próg, nie powinien się wyzwolić
    try:
        session_id = database.create_session()
        reader = FakeReader(frames=[b"1.0"] * 50)

        run_worker_briefly(reader, session_id, duration=0.2)

        assert reader.force_reconnect_calls == []
    finally:
        config.METER_NO_DATA_TIMEOUT = original_timeout


# =============================================================================
# Ścieżka fatalna (DatabaseFatalError)
# =============================================================================

def test_meter_worker_triggers_fatal_signal_on_database_fatal_error(monkeypatch):
    session_id = database.create_session()
    reader = FakeReader(frames=[b"1.0"])

    def raising_save(*args, **kwargs):
        raise database.DatabaseFatalError("symulowana awaria dysku")

    monkeypatch.setattr(meter_worker, "save_measurement", raising_save)

    _, fatal_signal = run_worker_briefly(reader, session_id, duration=0.2)

    assert fatal_signal.is_set() is True
    assert "symulowana awaria dysku" in fatal_signal.reason()


def test_meter_worker_triggers_fatal_signal_on_unexpected_exception(monkeypatch):
    session_id = database.create_session()
    reader = FakeReader(frames=[b"1.0"])

    def raising_save(*args, **kwargs):
        raise RuntimeError("coś nieoczekiwanego")

    monkeypatch.setattr(meter_worker, "save_measurement", raising_save)

    _, fatal_signal = run_worker_briefly(reader, session_id, duration=0.2)

    assert fatal_signal.is_set() is True
    assert "coś nieoczekiwanego" in fatal_signal.reason()


# =============================================================================
# Heartbeat
# =============================================================================

def test_meter_worker_beats_heartbeat():
    session_id = database.create_session()
    reader = FakeReader(frames=[])

    heartbeat, _ = run_worker_briefly(reader, session_id, duration=0.05)

    assert heartbeat.is_healthy("miernik", max_age=5.0) is True


# =============================================================================
# Czyste zamknięcie
# =============================================================================

def test_meter_worker_closes_reader_port_on_stop():
    session_id = database.create_session()
    reader = FakeReader(frames=[])

    class FakeSer:
        def __init__(self):
            self.is_open = True
            self.closed = False
        def close(self):
            self.closed = True
            self.is_open = False

    reader.ser = FakeSer()

    run_worker_briefly(reader, session_id, duration=0.05)

    assert reader.ser.closed is True


def test_meter_worker_respects_stop_event_promptly():
    """Worker powinien zakończyć się szybko po ustawieniu stop_event, nawet
    bez żadnych danych na porcie."""
    session_id = database.create_session()
    reader = FakeReader(frames=[])
    stop_event = threading.Event()
    heartbeat = HeartbeatMonitor()
    fatal_signal = FatalErrorSignal()

    t = threading.Thread(
        target=meter_worker.run,
        args=(session_id, stop_event, heartbeat, fatal_signal, reader),
    )
    t.start()
    time.sleep(0.02)
    start = time.monotonic()
    stop_event.set()
    t.join(timeout=2.0)
    elapsed = time.monotonic() - start

    assert not t.is_alive()
    assert elapsed < 1.0
