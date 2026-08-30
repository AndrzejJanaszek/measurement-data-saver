# src/sniffer.py
"""
Narzędzie do debugowania portów szeregowych - niezależnie od siebie
(miernik, Arduino, albo oba naraz - każdy w osobnym wątku).

Domyślnie (tryb "framed") korzysta z tej samej klasy SerialReader i tych
samych funkcji parsujących co produkcyjny main.py, więc pozwala sprawdzić
nie tylko czy dane w ogóle płyną, ale czy logika ramkowania (START/END char)
i parsowania (liczba z miernika / JSON temps-pins z Arduino) faktycznie
działa poprawnie - zanim odpali się cała usługa.

Użycie:
    python -m src.sniffer                  # oba porty, tryb framed
    python -m src.sniffer --meter           # tylko miernik
    python -m src.sniffer --arduino         # tylko Arduino
    python -m src.sniffer --meter --arduino # jawnie oba (to samo co bez flag)
    python -m src.sniffer --raw             # surowy podgląd bajtów, bez ramkowania
    python -m src.sniffer --arduino --raw   # surowy podgląd tylko Arduino
"""
import argparse
import sys
import threading
import time
from pathlib import Path

# Upewniamy się, że Python widzi folder główny (PYTHONPATH), gdy uruchamiane
# bezpośrednio jako skrypt (python src/sniffer.py), a nie jako moduł.
sys.path.append(str(Path(__file__).resolve().parent.parent))

import serial

from src import config
from src.serial_reader import SerialReader
from src.parse import parse_raw_frame, parse_arduino_frame

_print_lock = threading.Lock()


def _log(name: str, message: str):
    """Wypisuje linię z prefiksem urządzenia, chronione lockiem żeby wątki się nie mieszały."""
    with _print_lock:
        print(f"[{name}] {message}")


def sniff_framed(name: str, port: str, baudrate: int, timeout: float,
                  start_char, end_char_1, end_char_2, parser_fn, stop_event: threading.Event):
    """
    Tryb "framed" - korzysta z SerialReader (ta sama logika co w main.py),
    więc pokazuje kompletne ramki po zdjęciu START/END char, a następnie
    wynik parsowania (parse_raw_frame dla miernika / parse_arduino_frame
    dla Arduino). To najlepszy sposób, żeby sprawdzić, czy cała logika
    odczytu faktycznie zadziała tak samo jak w usłudze produkcyjnej.
    """
    _log(name, f"Otwieranie portu {port} ({baudrate} baud, timeout={timeout}s, tryb: framed)...")
    reader = SerialReader(
        port=port,
        baudrate=baudrate,
        timeout=timeout,
        start_char=start_char,
        end_char_1=end_char_1,
        end_char_2=end_char_2,
        name=name,
    )

    while not stop_event.is_set():
        frame = reader.read_next_frame()
        if frame is not None:
            _log(name, f"RAMKA (raw)  : {frame!r}")
            parsed = parser_fn(frame)
            _log(name, f"RAMKA (parsed): {parsed!r}")
            _log(name, "-" * 50)
        else:
            time.sleep(0.01)

    try:
        if reader.ser and reader.ser.is_open:
            reader.ser.close()
    except Exception:
        pass
    _log(name, "Port zamknięty.")


def sniff_raw(name: str, port: str, baudrate: int, timeout: float, stop_event: threading.Event):
    """
    Tryb "raw" - bez żadnego ramkowania/parsowania, surowy podgląd tego co
    faktycznie przychodzi z portu. Przydatne, gdy nie masz jeszcze pewności
    co do znaków START/END i chcesz zobaczyć "gołe" bajty, żeby je ustalić.
    """
    _log(name, f"Otwieranie portu {port} ({baudrate} baud, timeout={timeout}s, tryb: raw)...")
    try:
        ser = serial.Serial(port=port, baudrate=baudrate, timeout=timeout)
        _log(name, "Połączono pomyślnie. Oczekiwanie na dane...")
    except Exception as e:
        _log(name, f"BŁĄD: Nie można otworzyć portu: {e}")
        return

    while not stop_event.is_set():
        if ser.in_waiting > 0:
            raw_bytes = ser.read(ser.in_waiting)
            _log(name, f"RAW BYTES : {raw_bytes!r}")
            try:
                text = raw_bytes.decode('utf-8', errors='replace')
                _log(name, f"TEXT REPR : {repr(text)}")
            except Exception:
                pass
            _log(name, "-" * 50)
        else:
            time.sleep(0.1)

    ser.close()
    _log(name, "Port zamknięty.")


def run_sniffer(sniff_meter: bool, sniff_arduino: bool, raw_mode: bool):
    if not sniff_meter and not sniff_arduino:
        # Domyślnie: brak flag = oba porty (zgodnie z "niezależne, ale mogę
        # debugować jedno albo oba naraz").
        sniff_meter = True
        sniff_arduino = True

    print("=== Sniffer portów szeregowych ===")
    print(f"Miernik : {'TAK' if sniff_meter else 'nie'}  ({config.SERIAL_PORT})")
    print(f"Arduino : {'TAK' if sniff_arduino else 'nie'}  ({config.ARDUINO_SERIAL_PORT})")
    print(f"Tryb    : {'raw (surowe bajty)' if raw_mode else 'framed (ramkowanie + parsowanie jak w main.py)'}")
    print("Wciśnij Ctrl+C, aby zatrzymać.\n")

    stop_event = threading.Event()
    threads = []

    if sniff_meter:
        if raw_mode:
            args = ("miernik", config.SERIAL_PORT, config.BAUDRATE, config.TIMEOUT, stop_event)
            target = sniff_raw
        else:
            args = (
                "miernik", config.SERIAL_PORT, config.BAUDRATE, config.TIMEOUT,
                config.START_CHAR, config.END_CHAR_1, config.END_CHAR_2,
                parse_raw_frame, stop_event,
            )
            target = sniff_framed
        threads.append(threading.Thread(target=target, args=args, name="sniffer-miernik", daemon=True))

    if sniff_arduino:
        if raw_mode:
            args = ("arduino", config.ARDUINO_SERIAL_PORT, config.ARDUINO_BAUDRATE, config.ARDUINO_TIMEOUT, stop_event)
            target = sniff_raw
        else:
            args = (
                "arduino", config.ARDUINO_SERIAL_PORT, config.ARDUINO_BAUDRATE, config.ARDUINO_TIMEOUT,
                config.ARDUINO_START_CHAR, config.ARDUINO_END_CHAR_1, config.ARDUINO_END_CHAR_2,
                parse_arduino_frame, stop_event,
            )
            target = sniff_framed
        threads.append(threading.Thread(target=target, args=args, name="sniffer-arduino", daemon=True))

    for t in threads:
        t.start()

    try:
        while any(t.is_alive() for t in threads):
            time.sleep(0.2)
    except KeyboardInterrupt:
        print("\nZatrzymywanie sniffera...")
        stop_event.set()
        for t in threads:
            t.join(timeout=3.0)
        print("Zatrzymano.")


def main():
    parser = argparse.ArgumentParser(description="Sniffer portów szeregowych (miernik / Arduino / oba)")
    parser.add_argument("--meter", action="store_true", help="Debuguj tylko port miernika")
    parser.add_argument("--arduino", action="store_true", help="Debuguj tylko port Arduino")
    parser.add_argument("--raw", action="store_true", help="Surowy podgląd bajtów, bez ramkowania i parsowania")
    args = parser.parse_args()

    run_sniffer(sniff_meter=args.meter, sniff_arduino=args.arduino, raw_mode=args.raw)


if __name__ == "__main__":
    main()