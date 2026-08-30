import sqlite3
import threading
import time

import pytest

from src import config, database
from src.heartbeat import HeartbeatMonitor, FatalErrorSignal
from src.workers import arduino_worker


class FakeReader:
    """Zastępuje SerialReader w testach arduino_worker - patrz test_workers_meter.py."""
    def __init__(self, frames=None):
        self._frames = list(frames) if frames else []
        self.ser = None
        self.force_reconnect_calls = []

    def read_next_frame(self):
        if self._frames:
            return self._frames.pop(0)
        return None

    def force_reconnect(self, reason: str = ""):
        self.force_reconnect_calls.append(reason)


def run_worker_briefly(reader, session_id, duration: float, fatal_signal=None, heartbeat=None):
    stop_event = threading.Event()
    heartbeat = heartbeat or HeartbeatMonitor()
    fatal_signal = fatal_signal or FatalErrorSignal()

    t = threading.Thread(
        target=arduino_worker.run,
        args=(session_id, stop_event, heartbeat, fatal_signal, reader),
    )
    t.start()
    time.sleep(duration)
    stop_event.set()
    t.join(timeout=5.0)
    assert not t.is_alive(), "Wątek workera nie zakończył się w oczekiwanym czasie"
    return heartbeat, fatal_signal


def read_temperatures(session_id):
    with sqlite3.connect(config.DB_PATH) as conn:
        return conn.execute(
            "SELECT sensor_address, value FROM temperature_measurements WHERE session_id = ? ORDER BY id",
            (session_id,),
        ).fetchall()


def read_pin_states(session_id):
    with sqlite3.connect(config.DB_PATH) as conn:
        return conn.execute(
            "SELECT pin, value FROM pin_states WHERE session_id = ? ORDER BY id",
            (session_id,),
        ).fetchall()


# =============================================================================
# Temperatura - zapis na bieżąco
# =============================================================================

def test_arduino_worker_saves_temperature_batch_immediately():
    session_id = database.create_session()
    frame = b'{"temps":[{"a":"AAA","t":25.5},{"a":"BBB","t":26.1}]}'
    reader = FakeReader(frames=[frame])

    run_worker_briefly(reader, session_id, duration=0.1)

    rows = read_temperatures(session_id)
    assert rows == [("AAA", 25.5), ("BBB", 26.1)]


# =============================================================================
# Piny - zapis tylko zmian
# =============================================================================

def test_arduino_worker_saves_pin_changes_only():
    session_id = database.create_session()
    frames = [
        b'{"pins":{"4":1,"5":0}}',   # pierwszy batch - wszystko "nowe"
        b'{"pins":{"4":1,"5":1}}',   # tylko pin 5 się zmienia
        b'{"pins":{"4":1,"5":1}}',   # bez zmian - nic nowego w bazie
    ]
    reader = FakeReader(frames=frames)

    run_worker_briefly(reader, session_id, duration=0.15)

    rows = read_pin_states(session_id)
    assert rows == [(4, 1), (5, 0), (5, 1)]


def test_arduino_worker_no_pin_writes_when_nothing_changes():
    session_id = database.create_session()
    reader = FakeReader(frames=[b'{"pins":{"4":1}}', b'{"pins":{"4":1}}', b'{"pins":{"4":1}}'])

    run_worker_briefly(reader, session_id, duration=0.1)

    rows = read_pin_states(session_id)
    assert rows == [(4, 1)]  # tylko pierwszy zapis (świeży stan)


# =============================================================================
# Bannery diagnostyczne Arduino - nie powinny przeszkadzać w przetwarzaniu
# =============================================================================

def test_arduino_worker_ignores_startup_banners_and_still_processes_real_data():
    frames = [
        b'Locating devices...Found 4 devices.',
        b'Found device 0 with address: 28A8F126AB240B1C',
        b'io pin (INPUT_PULLUP) - on pin: 4',
        b'{"pins":{"4":1}}',
    ]
    session_id = database.create_session()
    reader = FakeReader(frames=frames)

    run_worker_briefly(reader, session_id, duration=0.1)

    rows = read_pin_states(session_id)
    assert rows == [(4, 1)]


# =============================================================================
# force_reconnect przy braku poprawnych danych
# =============================================================================

def test_arduino_worker_forces_reconnect_after_no_data_timeout():
    original_timeout = config.ARDUINO_NO_DATA_TIMEOUT
    config.ARDUINO_NO_DATA_TIMEOUT = 0.1
    try:
        session_id = database.create_session()
        reader = FakeReader(frames=[])

        run_worker_briefly(reader, session_id, duration=0.3)

        assert len(reader.force_reconnect_calls) >= 1
        assert "brak poprawnych danych przez" in reader.force_reconnect_calls[0]
    finally:
        config.ARDUINO_NO_DATA_TIMEOUT = original_timeout


def test_arduino_worker_banners_alone_still_count_as_no_data():
    """Same bannery diagnostyczne (bez realnych ramek temps/pins) NIE powinny
    resetować licznika braku danych - to nie są 'poprawne dane'."""
    original_timeout = config.ARDUINO_NO_DATA_TIMEOUT
    config.ARDUINO_NO_DATA_TIMEOUT = 0.1
    try:
        session_id = database.create_session()
        # Ciągły strumień samych bannerów, zero prawdziwych ramek danych
        reader = FakeReader(frames=[b'Locating devices...'] * 500)

        run_worker_briefly(reader, session_id, duration=0.3)

        assert len(reader.force_reconnect_calls) >= 1
    finally:
        config.ARDUINO_NO_DATA_TIMEOUT = original_timeout


# =============================================================================
# Ścieżka fatalna
# =============================================================================

def test_arduino_worker_triggers_fatal_signal_on_database_fatal_error(monkeypatch):
    session_id = database.create_session()
    reader = FakeReader(frames=[b'{"temps":[{"a":"AAA","t":21.0}]}'])

    def raising_save(*args, **kwargs):
        raise database.DatabaseFatalError("symulowana awaria dysku")

    monkeypatch.setattr(arduino_worker, "save_temperature_batch", raising_save)

    _, fatal_signal = run_worker_briefly(reader, session_id, duration=0.1)

    assert fatal_signal.is_set() is True
    assert "symulowana awaria dysku" in fatal_signal.reason()


# =============================================================================
# Heartbeat i czyste zamknięcie
# =============================================================================

def test_arduino_worker_beats_heartbeat():
    session_id = database.create_session()
    reader = FakeReader(frames=[])

    heartbeat, _ = run_worker_briefly(reader, session_id, duration=0.05)

    assert heartbeat.is_healthy("arduino", max_age=5.0) is True


def test_arduino_worker_closes_reader_port_on_stop():
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
