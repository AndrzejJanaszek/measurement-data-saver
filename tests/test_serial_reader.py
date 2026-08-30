from unittest.mock import MagicMock, patch

import pytest

from src.serial_reader import SerialReader


def make_reader(start_char=b'\x02', end_char_1=b'\x03', end_char_2=None,
                 reconnect_delay=0.0, name='test') -> SerialReader:
    """
    Tworzy SerialReader z zamockowanym pyserial.Serial, żeby konstruktor
    (który sam próbuje otworzyć port) nie wymagał prawdziwego sprzętu.
    reconnect_delay=0.0 domyślnie, żeby testy błędów/reconnectu nie czekały
    naprawdę na czas.sleep().
    """
    with patch('serial.Serial') as mock_serial_cls:
        mock_serial_cls.return_value = MagicMock(is_open=True)
        reader = SerialReader(
            port='/dev/fake',
            baudrate=9600,
            timeout=0.1,
            start_char=start_char,
            end_char_1=end_char_1,
            end_char_2=end_char_2,
            reconnect_delay=reconnect_delay,
            name=name,
        )
    return reader


def setup_mock_serial(reader, read_bytes: bytes) -> MagicMock:
    """Funkcja pomocnicza konfigurująca sztuczny port szeregowy."""
    mock_ser = MagicMock()
    mock_ser.is_open = True
    mock_ser.in_waiting = len(read_bytes)
    mock_ser.read.return_value = read_bytes

    reader.ser = mock_ser
    return mock_ser


# =============================================================================
# Konstruktor - parametryzacja
# =============================================================================

def test_constructor_stores_all_parameters():
    reader = make_reader(start_char=b'\x01', end_char_1=b'\r', end_char_2=b'\n',
                          reconnect_delay=2.5, name='miernik')
    assert reader.port == '/dev/fake'
    assert reader.baudrate == 9600
    assert reader.start_char == b'\x01'
    assert reader.end_char_1 == b'\r'
    assert reader.end_char_2 == b'\n'
    assert reader.reconnect_delay == 2.5
    assert reader.name == 'miernik'


def test_two_independent_readers_can_have_different_config():
    # To jest cały sens parametryzacji: dwie niezależne instancje, każda
    # z własną konfiguracją, żeby jedna klasa obsługiwała miernik i Arduino.
    meter_reader = make_reader(start_char=None, end_char_1=b'\r', end_char_2=b'\n', name='miernik')
    arduino_reader = make_reader(start_char=None, end_char_1=b'\n', end_char_2=None, name='arduino')

    assert meter_reader.end_char_1 == b'\r'
    assert meter_reader.end_char_2 == b'\n'
    assert arduino_reader.end_char_1 == b'\n'
    assert arduino_reader.end_char_2 is None
    assert meter_reader.name != arduino_reader.name


# =============================================================================
# Ramkowanie - podstawowe przypadki (przeniesione z oryginalnej wersji testów)
# =============================================================================

def test_read_complete_frame():
    reader = make_reader(start_char=b'\x02', end_char_1=b'\x03', end_char_2=None)
    setup_mock_serial(reader, read_bytes=b'\x0242.5\x03')
    frame = reader.read_next_frame()
    assert frame == b'42.5'
    assert reader.buffer == b''


def test_read_fragmented_frame():
    reader = make_reader(start_char=b'\x02', end_char_1=b'\x03', end_char_2=None)
    mock_ser = setup_mock_serial(reader, read_bytes=b'\x0242')
    frame1 = reader.read_next_frame()
    assert frame1 is None
    assert reader.buffer == b'\x0242'

    mock_ser.in_waiting = 3
    mock_ser.read.return_value = b'.5\x03'
    frame2 = reader.read_next_frame()
    assert frame2 == b'42.5'
    assert reader.buffer == b''


def test_config_x01_and_cr_lf():
    """Przypadek: START = \\x01, END = CR LF (\\r\\n)"""
    reader = make_reader(start_char=b'\x01', end_char_1=b'\r', end_char_2=b'\n')
    setup_mock_serial(reader, read_bytes=b'\x0199.9\r\n')
    frame = reader.read_next_frame()
    assert frame == b'99.9'
    assert reader.buffer == b''


def test_config_only_cr_lf_no_start():
    """Przypadek: START = None, END = CR LF (\\r\\n) - miernik"""
    reader = make_reader(start_char=None, end_char_1=b'\r', end_char_2=b'\n')
    setup_mock_serial(reader, read_bytes=b'ST,      66,kg\r\n')
    frame = reader.read_next_frame()
    assert frame == b'ST,      66,kg'
    assert reader.buffer == b''


def test_config_only_lf_no_start():
    """Przypadek: START = None, END = samo \\n - Arduino"""
    reader = make_reader(start_char=None, end_char_1=b'\n', end_char_2=None)
    setup_mock_serial(reader, read_bytes=b'{"pins":{"4":1}}\n')
    frame = reader.read_next_frame()
    assert frame == b'{"pins":{"4":1}}'
    assert reader.buffer == b''


def test_config_x02_and_lf():
    """Przypadek: START = \\x02, END = samo \\n"""
    reader = make_reader(start_char=b'\x02', end_char_1=b'\n', end_char_2=None)
    setup_mock_serial(reader, read_bytes=b'smieci\x0255.5\n')
    frame = reader.read_next_frame()
    assert frame == b'55.5'
    assert reader.buffer == b''


def test_garbage_handling_with_start_char():
    """Test odporności: Jeśli jest znak startu, śmieci przed nim są ucinane."""
    reader = make_reader(start_char=b'\x02', end_char_1=b'\x03', end_char_2=None)
    setup_mock_serial(reader, read_bytes=b'XYZ\x02100\x03')
    frame = reader.read_next_frame()
    assert frame == b'100'
    assert reader.buffer == b''


# =============================================================================
# Reconnect po wyjątku pyserial
# =============================================================================

def test_serial_exception_triggers_reconnect():
    import serial as pyserial

    reader = make_reader(reconnect_delay=0.0)
    mock_ser = MagicMock()
    mock_ser.is_open = True
    mock_ser.in_waiting = 5
    mock_ser.read.side_effect = pyserial.SerialException("port zniknął")
    reader.ser = mock_ser

    with patch('time.sleep'):  # nie czekamy naprawdę
        frame = reader.read_next_frame()

    assert frame is None
    # _handle_disconnect powinno zresetować stan, żeby kolejne wywołanie
    # spróbowało otworzyć port od nowa
    assert reader.ser is None
    assert reader.buffer == b''


def test_read_next_frame_reconnects_when_port_closed():
    reader = make_reader(reconnect_delay=0.0)
    reader.ser = None  # symulujemy stan "port nieotwarty"

    with patch('serial.Serial') as mock_serial_cls, patch('time.sleep'):
        mock_serial_cls.return_value = MagicMock(is_open=True, in_waiting=0)
        result = reader.read_next_frame()

    # Powinno spróbować ponownie połączyć (serial.Serial wywołane)
    assert result is None
    assert reader.ser is not None


# =============================================================================
# force_reconnect() - wymuszony reset połączenia bez wyjątku pyserial
# =============================================================================

def test_force_reconnect_closes_and_resets_state():
    reader = make_reader(reconnect_delay=0.0)
    mock_ser = MagicMock()
    mock_ser.is_open = True
    reader.ser = mock_ser
    reader.buffer = b'jakies-niedokonczone-smieci'

    with patch('time.sleep'):
        reader.force_reconnect(reason="test")

    mock_ser.close.assert_called_once()
    assert reader.ser is None
    assert reader.buffer == b''


def test_force_reconnect_logs_warning_with_reason(caplog):
    import logging

    reader = make_reader(reconnect_delay=0.0)
    reader.ser = MagicMock(is_open=True)

    with patch('time.sleep'), caplog.at_level(logging.WARNING):
        reader.force_reconnect(reason="brak danych przez 30s")

    assert any("Wymuszam ponowne połączenie" in msg and "brak danych przez 30s" in msg
               for msg in caplog.messages)


def test_force_reconnect_allows_subsequent_read():
    """Po force_reconnect kolejne wywołanie read_next_frame powinno spróbować
    otworzyć port ponownie i normalnie czytać dane."""
    reader = make_reader(start_char=None, end_char_1=b'\n', end_char_2=None, reconnect_delay=0.0)
    reader.ser = MagicMock(is_open=True)

    with patch('time.sleep'):
        reader.force_reconnect()

    with patch('serial.Serial') as mock_serial_cls:
        mock_new_ser = MagicMock(is_open=True, in_waiting=len(b'123.4\n'))
        mock_new_ser.read.return_value = b'123.4\n'
        mock_serial_cls.return_value = mock_new_ser

        frame = reader.read_next_frame()

    assert frame == b'123.4'
