import sqlite3
import os
import pytest
from src import config
from unittest.mock import patch, MagicMock
from src import database

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
    database.init_db()
    
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
    
    result = database.save_measurement(test_timestamp, test_value)
    
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

def test_save_measurement_heals_on_second_attempt():
    """
    Testuje sytuację, gdzie pierwsza próba zapisu zwraca błąd dysku,
    ale druga próba kończy się sukcesem. Funkcja powinna zwrócić True.
    """
    # Tworzymy mocka dla połączenia, który za drugim razem zadziała (będzie Context Managerem)
    mock_successful_conn = MagicMock()
    
    # Podmieniamy sqlite3.connect tak, by za 1. razem rzucił błąd, a za 2. razem zwrócił sukces
    with patch("sqlite3.connect") as mock_connect:
        mock_connect.side_effect = [
            sqlite3.OperationalError("unable to open database file"),  # Próba 1
            mock_successful_conn                                       # Próba 2
        ]
        
        # Odpalamy funkcję. Dzięki błędowki w pierwszej próbie, funkcja powinna
        # odczekać sekundę, spróbować ponownie i zwrócić True.
        # Skracamy czas sleep w module database, żeby test nie trwał całej sekundy
        with patch("time.sleep") as mock_sleep:
            result = database.save_measurement(timestamp=123456.7, value=55.5)
            
            assert result is True
            assert mock_connect.call_count == 2
            mock_sleep.assert_called_once_with(1.0)  # Sprawdzamy czy odczekał przed 2. próbą


def test_save_measurement_panics_on_persistent_error():
    """
    Testuje sytuację, gdzie obie próby zapisu zwracają błąd dysku.
    Aplikacja powinna wywołać procedurę awaryjną i zamknąć się przez sys.exit(1).
    """
    with patch("sqlite3.connect") as mock_connect:
        # Obie próby zwrócą trwały błąd dysku
        mock_connect.side_effect = sqlite3.OperationalError("unable to open database file")
        
        # Chcemy sprawdzić, czy program spróbuje wyjść awaryjnie za pomocą sys.exit(1)
        # W pytest przechwytujemy SystemExit za pomocą pytest.raises
        with patch("time.sleep"):  # Ignorujemy sztuczne czekanie, żeby test był szybki
            with pytest.raises(SystemExit) as exit_exception:
                database.save_measurement(timestamp=123456.7, value=55.5)
            
            # Sprawdzamy, czy kod wyjścia to dokładnie 1 (wymóg dla systemd pod Restart)
            assert exit_exception.value.code == 1
            # Sprawdzamy, czy zgodnie z logiką wykonał dokładnie 2 próby zanim poległ
            assert mock_connect.call_count == 2