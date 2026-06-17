import sqlite3
import os
import pytest
from src import config
from src.database import init_db, save_measurement

@pytest.fixture(autouse=True)
def setup_test_db(tmp_path):
    """
    Ta funkcja uruchamia się automatycznie PRZED KAŻDYM testem bazy danych.
    Tworzy tymczasowy plik bazy danych na dysku i sprząta po teście.
    """
    original_path = config.DB_PATH
    
    # tmp_path to unikalny dla każdego testu folder tymczasowy tworzony przez pytest
    temp_db = tmp_path / "test_measurements.db"
    config.DB_PATH = str(temp_db)
    
    # Inicjalizujemy strukturę tabeli
    init_db()
    
    yield  # Tutaj wykonuje się test
    
    # Po teście przywracamy oryginalną ścieżkę z konfiguracji
    config.DB_PATH = original_path


def test_init_db_creates_table():
    # Sprawdzamy, czy tabela measurements faktycznie istnieje w pliku tymczasowym
    with sqlite3.connect(config.DB_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='measurements'")
        table_exists = cursor.fetchone()
        
    assert table_exists is not None


def test_save_measurement_success():
    test_timestamp = 1718580000.0
    test_value = 23.45
    
    result = save_measurement(test_timestamp, test_value)
    
    # 1. Funkcja powinna potwierdzić sukces (True)
    assert result is True
    
    # 2. Sprawdzamy zawartość bazy danych
    with sqlite3.connect(config.DB_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT timestamp, value FROM measurements")
        row = cursor.fetchone()
        
    assert row is not None
    assert row[0] == test_timestamp
    assert row[1] == test_value