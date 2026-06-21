# src/database.py
import sqlite3
import logging
import time
import sys
from src import config

def init_db():
    """Tworzy tabelę w bazie danych, jeśli jeszcze nie istnieje."""
    try:
        with sqlite3.connect(config.DB_PATH) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS measurements (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp REAL NOT NULL,
                    value REAL NOT NULL
                )
            """)
            conn.commit()
            logging.info(f"Baza danych zainicjalizowana pomyślnie w: {config.DB_PATH}")
    except sqlite3.Error as e:
        logging.error(f"Krytyczny błąd inicjalizacji bazy danych: {e}")
        raise e

def save_measurement(timestamp: float, value: float) -> bool:
    """
    Zapisuje pojedynczy pomiar do bazy danych.
    W przypadku błędu dyskowego wykonuje jedną próbę naprawy.
    Przy trwałej utracie dostępu do bazy wymusza restart aplikacji.
    """
    for attempt in (1, 2):
        try:
            with sqlite3.connect(config.DB_PATH, timeout=2.0) as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "INSERT INTO measurements (timestamp, value) VALUES (?, ?)",
                    (timestamp, value)
                )
                conn.commit()
                return True
                
        except sqlite3.OperationalError as e:
            # Sprawdzamy, czy to specyficzny błąd braku dostępu do pliku
            if "unable to open database file" in str(e).lower():
                if attempt == 1:
                    logging.warning("Wykryto problem z plikiem bazy danych. Odczekanie 1s i ponowna próba...")
                    time.sleep(1.0)
                    continue  # Przechodzi do drugiego obrotu pętli (próba nr 2)
                else:
                    # Jeśli druga próba też padła, nie ma sensu srać logami w nieskończoność
                    _panic_and_exit(f"Trwały błąd dostępu do bazy: {e}")
            else:
                logging.error(f"Błąd operacyjny bazy danych (Próba {attempt}/2): {e}")
                if attempt == 2:
                    return False
                    
        except sqlite3.Error as e:
            logging.error(f"Ogólny błąd bazy danych (Próba {attempt}/2): {e}")
            if attempt == 2:
                return False

    return False

def _panic_and_exit(message: str):
    """Kończy działanie aplikacji z kodem błędu, wymuszając restart przez systemd."""
    logging.critical(f"!!! KATASTROFALNY BŁĄD SYSTEMU PLIKÓW !!! {message}")
    logging.critical("Zamykanie aplikacji. Systemd zrestartuje usługę za 5 sekund...")
    time.sleep(2.0)  # Czas na upewnienie się, że logi zostały zapisane w journald
    sys.exit(1)      # Kod wyjścia inny niż 0 zmusza systemd do wykonania Restart=always