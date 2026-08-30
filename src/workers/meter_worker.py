# src/workers/meter_worker.py
import logging
import time

from src import config, worker_names
from src.database import save_measurement, DatabaseFatalError, open_worker_connection
from src.heartbeat import HEARTBEAT_INTERVAL
from src.parse import parse_raw_frame
from src.serial_reader import SerialReader


def run(session_id: int, stop_event, heartbeat, fatal_signal, reader=None):
    """
    Pętla robocza dla miernika - działa w osobnym wątku.

    reader: opcjonalny, wstrzykiwany SerialReader (do testów bez fizycznego
    portu). Domyślnie tworzony na podstawie config.SERIAL_PORT / BAUDRATE / itd.

    Błędy portu szeregowego (wyjątki pyserial) są obsługiwane wewnątrz
    SerialReader (reconnect, bez przerywania pętli). Dodatkowo - jeśli port
    jest otwarty i nie zgłasza żadnego błędu, ale przez dłuższy czas nie
    napłynie żadna POPRAWNA ramka danych - wymuszamy reconnect ręcznie
    (SerialReader.force_reconnect()), bo to sygnał że coś może być nie tak
    mimo braku wyjątku. Błąd zapisu do bazy (DatabaseFatalError) albo
    jakikolwiek inny nieoczekiwany wyjątek kończy TYLKO ten wątek i sygnalizuje
    fatal_signal - main.py decyduje wtedy o restarcie całej usługi.
    """
    name = worker_names.METER

    if reader is None:
        reader = SerialReader(
            port=config.SERIAL_PORT,
            baudrate=config.BAUDRATE,
            timeout=config.TIMEOUT,
            start_char=config.START_CHAR,
            end_char_1=config.END_CHAR_1,
            end_char_2=config.END_CHAR_2,
            name=name,
        )

    # JEDNO długożyjące połączenie na cały czas życia wątku - patrz docstring
    # open_worker_connection() w database.py (naprawa wycieku deskryptorów
    # plików, które wcześniej otwierało nowe połączenie przy każdym zapisie).
    db_conn = open_worker_connection(name)

    last_saved_time = time.time()
    last_heartbeat_time = 0.0
    last_valid_frame_time = time.monotonic()
    latest_value = None
    # Ostatnia odebrana ramka w bieżącym oknie SAVE_DELAY, NIEZALEŻNIE od tego
    # czy udało się ją sparsować - pozwala odróżnić w warningu "port faktycznie
    # milczy" od "port coś wysyła, ale parser tego nie łapie" (błąd logiki
    # parsowania albo nieoczekiwany format ramki z miernika).
    last_seen_raw_frame = None

    logging.info(f"[{name}] Wątek uruchomiony.")

    try:
        while not stop_event.is_set():
            raw_frame = reader.read_next_frame()

            if raw_frame is not None:
                last_seen_raw_frame = raw_frame
                parsed_value = parse_raw_frame(raw_frame)
                if parsed_value is not None:
                    latest_value = parsed_value
                    last_valid_frame_time = time.monotonic()
                    logging.debug(f"[{name}] Odebrano nową wartość: {latest_value}")

            current_time = time.time()
            if current_time - last_saved_time >= config.SAVE_DELAY:
                if latest_value is not None:
                    save_measurement(db_conn, current_time, latest_value, session_id)
                    latest_value = None
                elif last_seen_raw_frame is not None:
                    logging.warning(
                        f"[{name}] Minęła sekunda, ale nie odebrano poprawnej ramki danych. "
                        f"Ostatnia otrzymana (niesparsowana) ramka: {last_seen_raw_frame!r}"
                    )
                else:
                    logging.warning(f"[{name}] Minęła sekunda, ale port nie wysłał żadnej ramki (cisza na porcie).")
                last_saved_time = current_time
                last_seen_raw_frame = None  # zaczynamy nowe okno od zera

            now_mono = time.monotonic()

            if now_mono - last_valid_frame_time >= config.METER_NO_DATA_TIMEOUT:
                reader.force_reconnect(f"brak poprawnych danych przez {config.METER_NO_DATA_TIMEOUT:.0f}s")
                # Resetujemy licznik, żeby dać połączeniu pełne okno czasowe
                # na "otrząśnięcie się" zanim ponownie uznamy brak danych za problem.
                last_valid_frame_time = time.monotonic()

            if now_mono - last_heartbeat_time >= HEARTBEAT_INTERVAL:
                heartbeat.beat(name)
                last_heartbeat_time = now_mono

            time.sleep(0.001)

    except DatabaseFatalError as e:
        logging.critical(f"[{name}] Fatalny błąd bazy danych, kończę wątek: {e}")
        fatal_signal.trigger(name, str(e))
    except Exception as e:
        logging.critical(f"[{name}] Nieoczekiwany błąd w pętli workera: {e}", exc_info=True)
        fatal_signal.trigger(name, str(e))
    finally:
        try:
            if reader.ser and reader.ser.is_open:
                reader.ser.close()
        except Exception:
            pass
        try:
            db_conn.close()
        except Exception:
            pass
        logging.info(f"[{name}] Wątek zakończony.")