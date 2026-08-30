# src/workers/meter_worker.py
import logging
import time

from src import config
from src.database import save_measurement, DatabaseFatalError
from src.parse import parse_raw_frame
from src.serial_reader import SerialReader


def run(session_id: int, stop_event, heartbeat, fatal_signal, reader=None):
    """
    Pętla robocza dla miernika - działa w osobnym wątku.

    reader: opcjonalny, wstrzykiwany SerialReader (do testów bez fizycznego
    portu). Domyślnie tworzony na podstawie config.SERIAL_PORT / BAUDRATE / itd.

    Błędy portu szeregowego są obsługiwane wewnątrz SerialReader (reconnect,
    bez przerywania pętli). Błąd zapisu do bazy (DatabaseFatalError) albo
    jakikolwiek inny nieoczekiwany wyjątek kończy TYLKO ten wątek i sygnalizuje
    fatal_signal - main.py decyduje wtedy o restarcie całej usługi.
    """
    if reader is None:
        reader = SerialReader(
            port=config.SERIAL_PORT,
            baudrate=config.BAUDRATE,
            timeout=config.TIMEOUT,
            start_char=config.START_CHAR,
            end_char_1=config.END_CHAR_1,
            end_char_2=config.END_CHAR_2,
            name="miernik",
        )

    last_saved_time = time.time()
    last_heartbeat_time = 0.0
    latest_value = None

    logging.info("[miernik] Wątek uruchomiony.")

    try:
        while not stop_event.is_set():
            raw_frame = reader.read_next_frame()

            if raw_frame is not None:
                parsed_value = parse_raw_frame(raw_frame)
                if parsed_value is not None:
                    latest_value = parsed_value
                    logging.debug(f"[miernik] Odebrano nową wartość: {latest_value}")

            current_time = time.time()
            if current_time - last_saved_time >= config.SAVE_DELAY:
                if latest_value is not None:
                    save_measurement(current_time, latest_value, session_id)
                    latest_value = None
                else:
                    logging.warning("[miernik] Minęła sekunda, ale nie odebrano jeszcze żadnej poprawnej ramki danych z portu.")
                last_saved_time = current_time

            now_mono = time.monotonic()
            if now_mono - last_heartbeat_time >= 5.0:
                heartbeat.beat("miernik")
                last_heartbeat_time = now_mono

            time.sleep(0.001)

    except DatabaseFatalError as e:
        logging.critical(f"[miernik] Fatalny błąd bazy danych, kończę wątek: {e}")
        fatal_signal.trigger("miernik", str(e))
    except Exception as e:
        logging.critical(f"[miernik] Nieoczekiwany błąd w pętli workera: {e}", exc_info=True)
        fatal_signal.trigger("miernik", str(e))
    finally:
        try:
            if reader.ser and reader.ser.is_open:
                reader.ser.close()
        except Exception:
            pass
        logging.info("[miernik] Wątek zakończony.")