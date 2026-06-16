# src/database.py
import sqlite3
import logging
from src import config

def init_db():
    """Tworzy tabelę w bazie danych, jeśli jeszcze nie istnieje."""
    try:
        # Połączenie tworzy plik bazy, jeśli go nie ma
        with sqlite3.connect(config.DB_PATH) as conn:
            cursor = conn.cursor()
            # Tworzymy tabelę z indeksem na timestamp, co jest dobre dla szeregów czasowych
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
    Zwraca True jeśli zapis się udał, False w przypadku błędu.
    """
    try:
        with sqlite3.connect(config.DB_PATH) as conn:
            cursor = conn.cursor()
            cursor.execute(
                "INSERT INTO measurements (timestamp, value) VALUES (?, ?)",
                (timestamp, value)
            )
            conn.commit()
            return True
    except sqlite3.Error as e:
        logging.error(f"Błąd podczas zapisu do bazy danych: {e}")
        return False