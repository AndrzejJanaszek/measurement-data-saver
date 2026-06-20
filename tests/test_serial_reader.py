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
    """Funkcja pomocnicza konfigurująca sztuczny port szeregowy."""
    mock_ser = MagicMock()
    mock_ser.is_open = True
    mock_ser.in_waiting = len(read_bytes)
    mock_ser.read.return_value = read_bytes
    
    reader.ser = mock_ser
    return mock_ser


@patch('serial.Serial')
def test_read_complete_frame(mock_serial, reader):
    setup_mock_serial(reader, read_bytes=b'\x0242.5\x03')
    frame = reader.read_next_frame()
    assert frame == b'42.5'
    assert reader.buffer == b''


@patch('serial.Serial')
def test_read_fragmented_frame(mock_serial, reader):
    mock_ser = setup_mock_serial(reader, read_bytes=b'\x0242')
    frame1 = reader.read_next_frame()
    assert frame1 is None
    assert reader.buffer == b'\x0242'

    mock_ser.in_waiting = 3
    mock_ser.read.return_value = b'.5\x03'
    frame2 = reader.read_next_frame()
    assert frame2 == b'42.5'
    assert reader.buffer == b''


# =========================================================================
# NOWE TESTY DLA RÓŻNYCH KONFIGURACJI PROTOKOŁÓW (IaC / UNIWERSALNOŚĆ)
# =========================================================================

@patch('serial.Serial')
def test_config_x01_and_cr_lf(mock_serial, reader):
    """Przypadek: START = \x01, END = CR LF (\r\n)"""
    config.START_CHAR = b'\x01'
    config.END_CHAR_1 = b'\r'
    config.END_CHAR_2 = b'\n'
    
    setup_mock_serial(reader, read_bytes=b'\x0199.9\r\n')
    
    frame = reader.read_next_frame()
    assert frame == b'99.9'
    assert reader.buffer == b''


@patch('serial.Serial')
def test_config_only_cr_lf_no_start(mock_serial, reader):
    """Przypadek: START = None, END = CR LF (\r\n) -> Twoja obecna waga"""
    config.START_CHAR = None
    config.END_CHAR_1 = b'\r'
    config.END_CHAR_2 = b'\n'
    
    # Symulujemy dokładnie to, co przysłała waga ze sniffera
    setup_mock_serial(reader, read_bytes=b'ST,      66,kg\r\n')
    
    frame = reader.read_next_frame()
    assert frame == b'ST,      66,kg'
    assert reader.buffer == b''


@patch('serial.Serial')
def test_config_only_lf_no_start(mock_serial, reader):
    """Przypadek: START = None, END = samo \n"""
    config.START_CHAR = None
    config.END_CHAR_1 = b'\n'
    config.END_CHAR_2 = None
    
    setup_mock_serial(reader, read_bytes=b'123.4\n')
    
    frame = reader.read_next_frame()
    assert frame == b'123.4'
    assert reader.buffer == b''


@patch('serial.Serial')
def test_config_x02_and_lf(mock_serial, reader):
    """Przypadek: START = \x02, END = samo \n"""
    config.START_CHAR = b'\x02'
    config.END_CHAR_1 = b'\n'
    config.END_CHAR_2 = None
    
    setup_mock_serial(reader, read_bytes=b'smieci\x0255.5\n')
    
    frame = reader.read_next_frame()
    assert frame == b'55.5'
    assert reader.buffer == b''


@patch('serial.Serial')
def test_garbage_handling_with_start_char(mock_serial, reader):
    """Test odporności: Jeśli jest znak startu, smieci przed nim są ucinane."""
    config.START_CHAR = b'\x02'
    config.END_CHAR_1 = b'\x03'
    config.END_CHAR_2 = None
    
    # 'XYZ' przed \x02 powinno zostać zignorowane
    setup_mock_serial(reader, read_bytes=b'XYZ\x02100\x03')

    frame = reader.read_next_frame()
    assert frame == b'100'
    assert reader.buffer == b''