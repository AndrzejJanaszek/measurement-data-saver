# tests/test_reader.py
from unittest.mock import MagicMock, patch
import pytest
from src.serial_reader import SerialReader
from src import config

@pytest.fixture
def reader():
    """Tworzy czysty obiekt SerialReader przed każdym testem i ustawia domyślne znaki."""
    config.START_CHAR = b'\x02'
    config.END_CHAR_1 = b'\x03'
    config.END_CHAR_2 = None
    return SerialReader()

def setup_mock_serial(reader, read_bytes: bytes) -> MagicMock:
    """
    Funkcja pomocnicza konfigurująca sztuczny port szeregowy.
    Automatycznie oblicza in_waiting na podstawie długości przekazanych bajtów.
    """
    mock_ser = MagicMock()
    mock_ser.is_open = True
    mock_ser.in_waiting = len(read_bytes)  # <-- PYTHON SAM TO POLICZY!
    mock_ser.read.return_value = read_bytes
    
    reader.ser = mock_ser
    return mock_ser


@patch('serial.Serial')
def test_read_complete_frame(mock_serial, reader):
    # Używamy funkcji pomocniczej: 5 bajtów w kolejce, zwraca pełną ramkę
    setup_mock_serial(reader, read_bytes=b'\x0242.5\x03')

    frame = reader.read_next_frame()
    
    assert frame == b'42.5'
    assert reader.buffer == b''  # Bufor wyczyszczony po pełnej ramce


@patch('serial.Serial')
def test_read_fragmented_frame(mock_serial, reader):
    # KROK 1: Port zwraca tylko początek ramki (brak znaku końca)
    mock_ser = setup_mock_serial(reader, read_bytes=b'\x0242')
    
    frame1 = reader.read_next_frame()
    assert frame1 is None  # Ramka niekompletna
    assert reader.buffer == b'\x0242'

    # KROK 2: Symulujemy nadejście reszty danych w kolejnym cyklu.
    # Podmieniamy zachowanie istniejącego mocka
    mock_ser.in_waiting = 3
    mock_ser.read.return_value = b'.5\x03'
    
    frame2 = reader.read_next_frame()
    assert frame2 == b'42.5'  # Teraz ramka jest kompletna
    assert reader.buffer == b''


@patch('serial.Serial')
def test_ignore_garbage_before_start(mock_serial, reader):
    # Śmieci na początku transmisji, potem poprawna ramka
    setup_mock_serial(reader, read_bytes=b'XYZ\x02100\x03')

    frame = reader.read_next_frame()
    assert frame == b'100'