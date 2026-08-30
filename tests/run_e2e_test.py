"""
Test end-to-end: uruchamia PRAWDZIWE meter_worker i arduino_worker (z prawdziwą
klasą SerialReader, nie mockiem) na symulowanych portach szeregowych (pty),
tak jak działałyby razem pod main.py, i weryfikuje że dane z obu urządzeń
trafiają poprawnie do wspólnej bazy w ramach jednej sesji.

Wcześniejsza wersja tego pliku:
  - w ogóle nie była odpalana przez pytest (brak funkcji zaczynającej się
    od "test_" - plik był "cicho" zbierany, ale zero testów z niego startowało),
  - zależała od zewnętrznego binarki `socat` (niedostępnej domyślnie na wielu
    systemach, w tym w środowisku CI/sandbox używanym do tej rewizji testów),
  - testowała stary, jednowątkowy main() sprzed refaktoru na miernik+Arduino.

Moduł `pty` z biblioteki standardowej daje dokładnie to samo (parę
master/slave symulującą prawdziwy port szeregowy) bez żadnych zewnętrznych
zależności - i to właśnie na nim opierały się manualne testy w trakcie
tworzenia tej funkcjonalności.
"""
import json
import os
import pty
import sqlite3
import threading
import time

import pytest

from src import config, database
from src.heartbeat import HeartbeatMonitor, FatalErrorSignal
from src.workers import meter_worker, arduino_worker


def build_meter_frame(value: float) -> bytes:
    """Buduje ramkę zgodnie z AKTUALNĄ konfiguracją znaków START/END miernika
    z config.py - test od razu wykryje regresję, gdyby ktoś zmienił protokół."""
    start = config.START_CHAR or b""
    end = (config.END_CHAR_1 or b"") + (config.END_CHAR_2 or b"")
    return start + f"{value:.2f}".encode("utf-8") + end


def build_arduino_frame(payload: dict) -> bytes:
    start = config.ARDUINO_START_CHAR or b""
    end = (config.ARDUINO_END_CHAR_1 or b"") + (config.ARDUINO_END_CHAR_2 or b"")
    return start + json.dumps(payload).encode("utf-8") + end


@pytest.fixture
def fast_save_delay():
    original = config.SAVE_DELAY
    config.SAVE_DELAY = 0.05
    yield
    config.SAVE_DELAY = original


@pytest.fixture
def simulated_ports():
    """Tworzy dwa niezależne pseudo-terminale (miernik + Arduino) i podmienia
    config.SERIAL_PORT / config.ARDUINO_SERIAL_PORT na czas testu."""
    meter_master, meter_slave = pty.openpty()
    arduino_master, arduino_slave = pty.openpty()

    original_meter_port = config.SERIAL_PORT
    original_arduino_port = config.ARDUINO_SERIAL_PORT
    config.SERIAL_PORT = os.ttyname(meter_slave)
    config.ARDUINO_SERIAL_PORT = os.ttyname(arduino_slave)

    yield meter_master, arduino_master

    config.SERIAL_PORT = original_meter_port
    config.ARDUINO_SERIAL_PORT = original_arduino_port


def test_e2e_meter_and_arduino_write_to_shared_session(simulated_ports, fast_save_delay):
    meter_master, arduino_master = simulated_ports

    session_id = database.create_session()
    stop_event = threading.Event()
    heartbeat = HeartbeatMonitor()
    fatal_signal = FatalErrorSignal()

    t_meter = threading.Thread(
        target=meter_worker.run,
        args=(session_id, stop_event, heartbeat, fatal_signal),
        name="e2e-meter-worker",
    )
    t_arduino = threading.Thread(
        target=arduino_worker.run,
        args=(session_id, stop_event, heartbeat, fatal_signal),
        name="e2e-arduino-worker",
    )
    t_meter.start()
    t_arduino.start()

    try:
        time.sleep(0.2)  # czas na otwarcie prawdziwych portów przez SerialReader

        os.write(meter_master, build_meter_frame(23.45))
        os.write(arduino_master, build_arduino_frame({"temps": [{"a": "AAA", "t": 25.5}]}))
        os.write(arduino_master, build_arduino_frame({"pins": {"4": 1, "5": 0}}))

        time.sleep(0.3)  # czas na przetworzenie i zapisanie

        # Oba wątki powinny być "zdrowe" (heartbeat) w trakcie normalnej pracy
        assert heartbeat.all_healthy(["miernik", "arduino"], max_age=5.0) is True
        assert fatal_signal.is_set() is False

    finally:
        stop_event.set()
        t_meter.join(timeout=5.0)
        t_arduino.join(timeout=5.0)

    assert not t_meter.is_alive(), "Wątek miernika nie zakończył się po stop_event"
    assert not t_arduino.is_alive(), "Wątek Arduino nie zakończył się po stop_event"

    with sqlite3.connect(config.DB_PATH) as conn:
        measurements = conn.execute(
            "SELECT value FROM measurements WHERE session_id = ?", (session_id,)
        ).fetchall()
        temps = conn.execute(
            "SELECT sensor_address, value FROM temperature_measurements WHERE session_id = ?",
            (session_id,),
        ).fetchall()
        pins = conn.execute(
            "SELECT pin, value FROM pin_states WHERE session_id = ? ORDER BY pin",
            (session_id,),
        ).fetchall()

    assert measurements == [(23.45,)]
    assert temps == [("AAA", 25.5)]
    assert pins == [(4, 1), (5, 0)]


def test_e2e_arduino_startup_banner_does_not_break_subsequent_real_data(simulated_ports, fast_save_delay):
    """
    Symuluje realny scenariusz zaobserwowany na sprzęcie: Arduino po
    reset/starcie wysyła banner diagnostyczny PRZED pierwszą prawdziwą
    ramką danych. Worker powinien go zignorować i mimo to poprawnie
    przetworzyć dane, które przyjdą zaraz potem.
    """
    meter_master, arduino_master = simulated_ports

    session_id = database.create_session()
    stop_event = threading.Event()
    heartbeat = HeartbeatMonitor()
    fatal_signal = FatalErrorSignal()

    t_arduino = threading.Thread(
        target=arduino_worker.run,
        args=(session_id, stop_event, heartbeat, fatal_signal),
        name="e2e-arduino-worker-banner",
    )
    t_arduino.start()

    try:
        time.sleep(0.2)

        os.write(arduino_master, b"Locating devices...Found 4 devices.\n")
        os.write(arduino_master, b"Found device 0 with address: 28A8F126AB240B1C\n")
        os.write(arduino_master, build_arduino_frame({"pins": {"4": 1}}))

        time.sleep(0.2)

    finally:
        stop_event.set()
        t_arduino.join(timeout=5.0)

    assert not t_arduino.is_alive()

    with sqlite3.connect(config.DB_PATH) as conn:
        pins = conn.execute(
            "SELECT pin, value FROM pin_states WHERE session_id = ?", (session_id,)
        ).fetchall()

    assert pins == [(4, 1)]


if __name__ == "__main__":
    # Wygodne uruchomienie samego pliku e2e bez pamiętania pełnej komendy pytest.
    import sys
    raise SystemExit(pytest.main([__file__, "-v"] + sys.argv[1:]))
