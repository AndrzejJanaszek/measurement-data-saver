# src/database.py
import contextlib
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

        # contextlib.closing() gwarantuje wywołanie conn.close() na wyjściu -
        # w przeciwieństwie do natywnego context managera sqlite3.Connection,
        # który przy __exit__ robi TYLKO commit/rollback, NIGDY close(). To
        # jest źródło wycieku deskryptorów plików, który naprawiamy w całym
        # tym module (patrz też open_worker_connection() niżej).
        with contextlib.closing(sqlite3.connect(config.DB_PATH)) as conn:
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
    with contextlib.closing(sqlite3.connect(config.DB_PATH, timeout=2.0)) as conn:
        cursor = conn.cursor()
        cursor.execute("INSERT INTO sessions (started_at) VALUES (?)", (time.time(),))
        conn.commit()
        session_id = cursor.lastrowid
        logging.info(f"Utworzono nową sesję: id={session_id}")
        return session_id


def open_worker_connection(name: str) -> sqlite3.Connection:
    """
    Otwiera JEDNO, długożyjące połączenie SQLite przeznaczone na cały czas
    życia wątku roboczego (miernik / arduino).

    WAŻNE - dlaczego to jest konieczne: poprzednia wersja tego modułu otwierała
    nowe połączenie sqlite3.connect() przy KAŻDYM pojedynczym zapisie (każda
    ramka pomiarowa, każda zmiana pinu). Przy częstotliwości kilku zapisów/s
    z trzech wątków naraz prowadziło to do stopniowego wyczerpywania puli
    deskryptorów plików procesu, aż po ok. 4-5 minutach działania proces nie
    mógł już otworzyć NOWEGO pliku (błąd "unable to open database file"),
    co wymuszało fatalny restart usługi w regularnych, w pełni deterministycz-
    nych odstępach czasu - potwierdzone na sprzęcie (/proc/<pid>/fd rosło
    w czasie aż do limitu). Każdy worker wywołuje tę funkcję RAZ na starcie
    swojej pętli i przekazuje zwrócone połączenie do wszystkich save_*(),
    a przy zamykaniu wątku (finally) wywołuje conn.close().

    Połączenie jest używane WYŁĄCZNIE w wątku, który je utworzył - sqlite3
    domyślnie pilnuje tego sam (check_same_thread=True), więc próba użycia
    go z innego wątku rzuci wyjątek zamiast po cichu psuć dane.
    """
    conn = sqlite3.connect(config.DB_PATH, timeout=5.0)
    logging.debug(f"[{name}] Otwarto długożyjące połączenie z bazą danych.")
    return conn


def _run_with_retry(conn: sqlite3.Connection, work, description: str) -> bool:
    """
    Wspólna logika retry NA ISTNIEJĄCYM, długożyjącym połączeniu (nie otwiera
    już nowych połączeń per zapis). `work(cursor)` powinno wykonać INSERT(y)
    na przekazanym kursorze.

    Zachowanie zależy od typu błędu:
    - "unable to open database file": trwały problem z systemem plików (to
      dokładnie nasz potwierdzony historyczny incydent - wyciek deskryptorów
      plików przy otwieraniu nowego połączenia na każdy zapis). Eskaluje do
      DatabaseFatalError po MAX_OPEN_FILE_ATTEMPTS próbach, co wymusza
      restart całej usługi - uzasadnione, bo to sygnał poważnego problemu
      na poziomie OS/dysku.
    - "database is locked": zwykle CHWILOWA kontencja między dwoma wątkami
      (miernik/arduino) piszącymi do tego samego pliku WAL. Połączenie ma
      już własny wewnętrzny busy_timeout (patrz open_worker_connection),
      więc ten wyjątek widzimy DOPIERO PO tym, jak SQLite sam już czekał.
      Restart całej usługi z tego powodu byłby przesadą (i mógłby
      spowodować WIĘCEJ przestojów niż rozwiązać) - próbujemy jeszcze
      kilka razy z rosnącym odstępem, a jeśli nadal się nie uda, tracimy
      TEN JEDEN zapis (log błędu) zamiast zabijać oba wątki robocze.
    - inne błędy: 2 szybkie próby, potem False (bez eskalacji do fatal).
    """
    MAX_OPEN_FILE_ATTEMPTS = 2
    LOCK_RETRY_DELAYS = (0.2, 0.5, 1.0)  # rosnący backoff, nie-fatalny

    open_file_attempt = 0
    lock_attempt = 0
    generic_attempt = 0

    while True:
        try:
            cursor = conn.cursor()
            work(cursor)
            conn.commit()
            return True

        except sqlite3.OperationalError as e:
            error_text = str(e).lower()

            if "unable to open database file" in error_text:
                open_file_attempt += 1
                if open_file_attempt < MAX_OPEN_FILE_ATTEMPTS:
                    logging.warning(f"[{description}] Wykryto problem z plikiem bazy danych. Odczekanie 1s i ponowna próba...")
                    time.sleep(1.0)
                    continue
                _raise_fatal(f"Trwały błąd dostępu do bazy ({description}): {e}")

            elif "database is locked" in error_text:
                if lock_attempt < len(LOCK_RETRY_DELAYS):
                    delay = LOCK_RETRY_DELAYS[lock_attempt]
                    lock_attempt += 1
                    logging.warning(
                        f"[{description}] Chwilowa kontencja bazy danych (database is locked), "
                        f"próba {lock_attempt}/{len(LOCK_RETRY_DELAYS)}, odczekanie {delay}s..."
                    )
                    time.sleep(delay)
                    continue
                logging.error(
                    f"[{description}] Trwała kontencja bazy danych (database is locked) po "
                    f"{lock_attempt} dodatkowych próbach - pomijam TEN zapis. Usługa NIE jest restartowana."
                )
                return False

            else:
                generic_attempt += 1
                logging.error(f"[{description}] Błąd operacyjny bazy danych (Próba {generic_attempt}/2): {e}")
                if generic_attempt >= 2:
                    return False

        except sqlite3.Error as e:
            generic_attempt += 1
            logging.error(f"[{description}] Ogólny błąd bazy danych (Próba {generic_attempt}/2): {e}")
            if generic_attempt >= 2:
                return False


def save_measurement(conn: sqlite3.Connection, timestamp: float, value: float, session_id: int) -> bool:
    """Zapisuje pojedynczy pomiar z miernika do bazy danych, na przekazanym połączeniu."""
    def work(cursor):
        cursor.execute(
            "INSERT INTO measurements (timestamp, value, session_id) VALUES (?, ?, ?)",
            (timestamp, value, session_id)
        )
    return _run_with_retry(conn, work, "measurements")


def save_temperature_batch(conn: sqlite3.Connection, timestamp: float, readings: list[tuple[str, float]], session_id: int) -> bool:
    """
    Zapisuje jedną porcję odczytów temperatury (może zawierać wiele czujników
    naraz, tak jak przychodzą w jednej ramce JSON od Arduino), na przekazanym
    połączeniu.
    readings: lista krotek (sensor_address, value).
    """
    if not readings:
        return True

    def work(cursor):
        cursor.executemany(
            "INSERT INTO temperature_measurements (timestamp, sensor_address, value, session_id) VALUES (?, ?, ?, ?)",
            [(timestamp, address, value, session_id) for address, value in readings]
        )
    return _run_with_retry(conn, work, "temperature_measurements")


def save_pin_states_batch(conn: sqlite3.Connection, timestamp: float, changes: dict[int, int], session_id: int) -> bool:
    """
    Zapisuje tylko te piny, których wartość faktycznie się zmieniła
    (filtrowanie odbywa się wcześniej, w PinStateTracker), na przekazanym
    połączeniu.
    changes: słownik {pin: value}.
    """
    if not changes:
        return True

    def work(cursor):
        cursor.executemany(
            "INSERT INTO pin_states (timestamp, pin, value, session_id) VALUES (?, ?, ?, ?)",
            [(timestamp, pin, value, session_id) for pin, value in changes.items()]
        )
    return _run_with_retry(conn, work, "pin_states")


def _raise_fatal(message: str):
    """
    Loguje krytyczny błąd i rzuca DatabaseFatalError.
    Kończenie procesu (sys.exit) NIE odbywa się tutaj - patrz docstring
    DatabaseFatalError. To worker i main.py decydują, jak zareagować.
    """
    logging.critical(f"!!! KATASTROFALNY BŁĄD SYSTEMU PLIKÓW !!! {message}")
    raise DatabaseFatalError(message)