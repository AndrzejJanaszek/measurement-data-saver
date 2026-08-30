import os
from pathlib import Path

# ---------------------------------------------------------------------------
# Miernik (istniejące urządzenie, port szeregowy nr 1)
# ---------------------------------------------------------------------------
SERIAL_PORT = "/dev/ttyUSB0"
BAUDRATE = 9600
TIMEOUT = 0.1  # non-blocking read timeout

# START_CHAR = b'\x02'
# END_CHAR_1 = b'\x03'
# END_CHAR_2 = None

START_CHAR = None
END_CHAR_1 = b'\r'  # To jest CR
END_CHAR_2 = b'\n'  # To jest LF

# ---------------------------------------------------------------------------
# Arduino (nowe urządzenie, port szeregowy nr 2) - temperatura + stany pinów
# ---------------------------------------------------------------------------
ARDUINO_SERIAL_PORT = "/dev/ttyACM0"
ARDUINO_BAUDRATE = 9600
ARDUINO_TIMEOUT = 0.1

ARDUINO_START_CHAR = None
# Konfigurowalny koniec ramki - Arduino wysyła linie zakończone \n.
# Zostawiamy to skonfigurowane analogicznie do miernika, na wypadek
# gdyby w przyszłości trzeba było to zmienić (np. na \r\n).
ARDUINO_END_CHAR_1 = b'\n'
ARDUINO_END_CHAR_2 = None

# ---------------------------------------------------------------------------
# Wspólne
# ---------------------------------------------------------------------------
RECONNECT_DELAY = 5.0

# Jeśli przez tyle sekund NIE napłynie żadna poprawnie sparsowana ramka danych
# (mimo że port jest otwarty i pyserial nie zgłasza błędu), worker wymusza
# zamknięcie i ponowne otwarcie portu - patrz SerialReader.force_reconnect().
# To nie jest to samo co timeout heartbeatu (heartbeat.UNHEALTHY_AFTER) -
# heartbeat mówi tylko "pętla workera żyje", a to sprawdza czy faktycznie
# płyną sensowne dane.
METER_NO_DATA_TIMEOUT = 15.0
ARDUINO_NO_DATA_TIMEOUT = 15.0

SAVE_DELAY = 1.0  # dotyczy tylko miernika - Arduino zapisuje na bieżąco

# Katalog z bazą danych ma być częścią repozytorium (nie .gitignore),
# żeby dane wędrowały razem z kodem.
DB_DIR = Path(os.environ.get("DB_DIR", "db"))
DB_PATH = str(DB_DIR / "measurement-data-saver.db")