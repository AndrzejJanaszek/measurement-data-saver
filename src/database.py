# src/database.py
import sqlite3
import logging
import time
from src import config


class DatabaseFatalError(Exception):
    """
    Trwały, nieodzyskiwalny błąd dostępu do bazy danych.

    Uwaga: NIE wywołujemy tu sys.exit() - wywołane z wątku roboczego innego
    niż główny zakończyłoby tylko ten jeden wątek (SystemExit jest łapane
    per-wątek), a proces i pozostałe wątki działałyby dalej w ciszy, bez
    restartu przez systemd. Zamiast tego rzucamy wyjątek: worker go łapie,
    ustawia współdzielony fatal_event i kończy swoją pętlę, a main.py -
    działający w wątku głównym - dopiero tam bezpiecznie woła sys.exit(1).
    """
    pass


def init_db():
    """Tworzy katalog i tabele w bazie danych, jeśli jeszcze nie istnieją."""
    try:
        config.DB_DIR.mkdir(parents=True, exist_ok=True)

        with sqlite3.connect(config.DB_PATH) as conn:
            cursor = conn.cursor()

            # WAL pozwala zapisom (miernik/arduino-temps/arduino-pins z trzech
            # różnych wątków) nie blokować się nawzajem tak mocno jak domyślny
            # rollback journal, przy zachowaniu tej samej odporności na crash.
            cursor.execute("PRAGMA journal_mode=WAL")

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS sessions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    started_at REAL NOT NULL
                )
            """)

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS measurements (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp REAL NOT NULL,
                    value REAL NOT NULL,
                    session_id INTEGER NOT NULL REFERENCES sessions(id)
                )
            """)

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS temperature_measurements (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp REAL NOT NULL,
                    sensor_address TEXT NOT NULL,
                    value REAL NOT NULL,
                    session_id INTEGER NOT NULL REFERENCES sessions(id)
                )
            """)

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS pin_states (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp REAL NOT NULL,
                    pin INTEGER NOT NULL,
                    value INTEGER NOT NULL,
                    session_id INTEGER NOT NULL REFERENCES sessions(id)
                )
            """)

            conn.commit()

            # Migracja na wypadek istniejącej bazy sprzed wprowadzenia session_id
            # (np. stary plik measurements.db przeniesiony do db/).
            _add_column_if_missing(cursor, "measurements", "session_id", "INTEGER")
            conn.commit()

            logging.info(f"Baza danych zainicjalizowana pomyślnie w: {config.DB_PATH}")
    except sqlite3.Error as e:
        logging.error(f"Krytyczny błąd inicjalizacji bazy danych: {e}")
        raise e


def _add_column_if_missing(cursor, table: str, column: str, col_type: str):
    """Dodaje kolumnę do istniejącej tabeli, jeśli jeszcze jej nie ma."""
    cursor.execute(f"PRAGMA table_info({table})")
    existing_columns = {row[1] for row in cursor.fetchall()}
    if column not in existing_columns:
        cursor.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}")
        logging.info(f"Zmigrowano tabelę '{table}': dodano kolumnę '{column}'.")


def create_session() -> int:
    """
    Tworzy nowy wpis sesji (jedno uruchomienie usługi) i zwraca jej id.
    Ponieważ RPi nie ma RTC, started_at jest tylko przybliżeniem czasu startu -
    session_id służy głównie do grupowania rekordów z tego samego uruchomienia,
    niezależnie od tego, czy zegar systemowy jest wiarygodny.
    """
    with sqlite3.connect(config.DB_PATH, timeout=2.0) as conn:
        cursor = conn.cursor()
        cursor.execute("INSERT INTO sessions (started_at) VALUES (?)", (time.time(),))
        conn.commit()
        session_id = cursor.lastrowid
        logging.info(f"Utworzono nową sesję: id={session_id}")
        return session_id


def _run_with_retry(work, description: str) -> bool:
    """
    Wspólna logika retry/panic dla operacji zapisu do bazy.
    `work(cursor)` powinno wykonać INSERT(y) na przekazanym kursorze.
    Powiela zachowanie oryginalnego save_measurement: przy trwałym braku
    dostępu do pliku bazy wymusza restart całej usługi (systemd Restart=always).
    """
    for attempt in (1, 2):
        try:
            with sqlite3.connect(config.DB_PATH, timeout=2.0) as conn:
                cursor = conn.cursor()
                work(cursor)
                conn.commit()
                return True

        except sqlite3.OperationalError as e:
            if "unable to open database file" in str(e).lower():
                if attempt == 1:
                    logging.warning(f"[{description}] Wykryto problem z plikiem bazy danych. Odczekanie 1s i ponowna próba...")
                    time.sleep(1.0)
                    continue
                else:
                    _raise_fatal(f"Trwały błąd dostępu do bazy ({description}): {e}")
            else:
                logging.error(f"[{description}] Błąd operacyjny bazy danych (Próba {attempt}/2): {e}")
                if attempt == 2:
                    return False

        except sqlite3.Error as e:
            logging.error(f"[{description}] Ogólny błąd bazy danych (Próba {attempt}/2): {e}")
            if attempt == 2:
                return False

    return False


def save_measurement(timestamp: float, value: float, session_id: int) -> bool:
    """Zapisuje pojedynczy pomiar z miernika do bazy danych."""
    def work(cursor):
        cursor.execute(
            "INSERT INTO measurements (timestamp, value, session_id) VALUES (?, ?, ?)",
            (timestamp, value, session_id)
        )
    return _run_with_retry(work, "measurements")


def save_temperature_batch(timestamp: float, readings: list[tuple[str, float]], session_id: int) -> bool:
    """
    Zapisuje jedną porcję odczytów temperatury (może zawierać wiele czujników
    naraz, tak jak przychodzą w jednej ramce JSON od Arduino).
    readings: lista krotek (sensor_address, value).
    """
    if not readings:
        return True

    def work(cursor):
        cursor.executemany(
            "INSERT INTO temperature_measurements (timestamp, sensor_address, value, session_id) VALUES (?, ?, ?, ?)",
            [(timestamp, address, value, session_id) for address, value in readings]
        )
    return _run_with_retry(work, "temperature_measurements")


def save_pin_states_batch(timestamp: float, changes: dict[int, int], session_id: int) -> bool:
    """
    Zapisuje tylko te piny, których wartość faktycznie się zmieniła
    (filtrowanie odbywa się wcześniej, w PinStateTracker).
    changes: słownik {pin: value}.
    """
    if not changes:
        return True

    def work(cursor):
        cursor.executemany(
            "INSERT INTO pin_states (timestamp, pin, value, session_id) VALUES (?, ?, ?, ?)",
            [(timestamp, pin, value, session_id) for pin, value in changes.items()]
        )
    return _run_with_retry(work, "pin_states")


def _raise_fatal(message: str):
    """
    Loguje krytyczny błąd i rzuca DatabaseFatalError.
    Kończenie procesu (sys.exit) NIE odbywa się tutaj - patrz docstring
    DatabaseFatalError. To worker i main.py decydują, jak zareagować.
    """
    logging.critical(f"!!! KATASTROFALNY BŁĄD SYSTEMU PLIKÓW !!! {message}")
    raise DatabaseFatalError(message)