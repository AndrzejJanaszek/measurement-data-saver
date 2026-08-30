# src/worker_names.py
"""
Jedno źródło prawdy dla nazw workerów. Wcześniej "miernik"/"arduino"
występowały jako osobne literały stringowe w main.py, meter_worker.py
i arduino_worker.py - literówka albo zmiana nazwy w jednym miejscu
NIE powodowała żadnego błędu, tylko cichy rozjazd: heartbeat nigdy nie
widziałby zgłoszeń dla tej nazwy, main.py uznawałby workera za wiecznie
"niezdrowego", nigdy nie wysyłałby WATCHDOG=1, a systemd zabijałby
i restartował usługę co WatchdogSec bez żadnego komunikatu wskazującego
przyczynę. Importowanie tych stałych wszędzie eliminuje to ryzyko.
"""

METER = "miernik"
ARDUINO = "arduino"

ALL = [METER, ARDUINO]