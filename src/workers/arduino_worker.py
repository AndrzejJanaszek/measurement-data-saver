# src/workers/arduino_worker.py
import logging
import time

from src import config
from src.database import save_temperature_batch, save_pin_states_batch, DatabaseFatalError
from src.parse import parse_arduino_frame
from src.pin_tracker import PinStateTracker
from src.serial_reader import SerialReader


def run(session_id: int, stop_event, heartbeat, fatal_signal, reader=None):
    """
    Pętla robocza dla Arduino - działa w osobnym wątku.

    reader: opcjonalny, wstrzykiwany SerialReader (do testów bez fizycznego
    portu). Domyślnie tworzony na podstawie config.ARDUINO_*.

    Temperatura: zapisywana od razu przy każdej odebranej ramce "temps"
    (może zawierać kilka czujników naraz - jeden batch INSERT).
    Piny: przepuszczane przez PinStateTracker, zapisywane do bazy TYLKO gdy
    faktycznie się zmieniły względem poprzedniego znanego stanu.
    """
    if reader is None:
        reader = SerialReader(
            port=config.ARDUINO_SERIAL_PORT,
            baudrate=config.ARDUINO_BAUDRATE,
            timeout=config.ARDUINO_TIMEOUT,
            start_char=config.ARDUINO_START_CHAR,
            end_char_1=config.ARDUINO_END_CHAR_1,
            end_char_2=config.ARDUINO_END_CHAR_2,
            name="arduino",
        )

    pin_tracker = PinStateTracker()
    last_heartbeat_time = 0.0

    logging.info("[arduino] Wątek uruchomiony.")

    try:
        while not stop_event.is_set():
            raw_frame = reader.read_next_frame()

            if raw_frame is not None:
                parsed = parse_arduino_frame(raw_frame)

                if parsed is not None:
                    kind, payload = parsed
                    now = time.time()

                    if kind == "temps":
                        save_temperature_batch(now, payload, session_id)
                        logging.debug(f"[arduino] Zapisano {len(payload)} odczytów temperatury.")

                    elif kind == "pins":
                        changes = pin_tracker.get_changes(payload)
                        if changes:
                            save_pin_states_batch(now, changes, session_id)
                            logging.debug(f"[arduino] Zapisano zmiany pinów: {changes}")

            now_mono = time.monotonic()
            if now_mono - last_heartbeat_time >= 5.0:
                heartbeat.beat("arduino")
                last_heartbeat_time = now_mono

            time.sleep(0.001)

    except DatabaseFatalError as e:
        logging.critical(f"[arduino] Fatalny błąd bazy danych, kończę wątek: {e}")
        fatal_signal.trigger("arduino", str(e))
    except Exception as e:
        logging.critical(f"[arduino] Nieoczekiwany błąd w pętli workera: {e}", exc_info=True)
        fatal_signal.trigger("arduino", str(e))
    finally:
        try:
            if reader.ser and reader.ser.is_open:
                reader.ser.close()
        except Exception:
            pass
        logging.info("[arduino] Wątek zakończony.")