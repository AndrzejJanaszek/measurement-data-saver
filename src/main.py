# src/main.py
import logging
import sys
import threading
import time

import sdnotify

from src.database import init_db, create_session
from src.heartbeat import HeartbeatMonitor, FatalErrorSignal, UNHEALTHY_AFTER
from src.workers import meter_worker, arduino_worker

logging.basicConfig(level=logging.INFO, format='%(asctime)s - [%(levelname)s] - %(message)s')

WORKER_NAMES = ["miernik", "arduino"]
MAIN_LOOP_INTERVAL = 1.0       # jak często main sprawdza stan wątków
WATCHDOG_NOTIFY_INTERVAL = 5.0  # jak często main wysyła WATCHDOG=1 do systemd


def main():
    logging.info("Uruchamianie aplikacji RPi Serial Logger (v2 - miernik + arduino)...")

    notifier = sdnotify.SystemdNotifier()
    notifier.notify("READY=1")

    # 1. Inicjalizacja bazy danych i nowej sesji (grupowanie rekordów z tego uruchomienia)
    try:
        init_db()
        session_id = create_session()
    except Exception as e:
        logging.critical(f"Nie można zainicjalizować bazy danych. Zamykanie aplikacji. Błąd: {e}")
        sys.exit(1)

    # 2. Przygotowanie mechanizmów współdzielonych między wątkami
    stop_event = threading.Event()
    heartbeat = HeartbeatMonitor()
    fatal_signal = FatalErrorSignal()

    threads = {
        "miernik": threading.Thread(
            target=meter_worker.run,
            args=(session_id, stop_event, heartbeat, fatal_signal),
            name="meter-worker",
            daemon=True,
        ),
        "arduino": threading.Thread(
            target=arduino_worker.run,
            args=(session_id, stop_event, heartbeat, fatal_signal),
            name="arduino-worker",
            daemon=True,
        ),
    }

    for t in threads.values():
        t.start()

    logging.info("Wątki robocze uruchomione. Wejście w główną pętlę monitorującą.")

    last_watchdog_notify = 0.0

    try:
        while True:
            # KROK A: Fatalny błąd zgłoszony jawnie przez worker (np. DatabaseFatalError)
            # -> natychmiastowe zakończenie procesu z poziomu głównego wątku (patrz
            # komentarz w heartbeat.FatalErrorSignal / database.DatabaseFatalError,
            # dlaczego to musi się dziać tutaj, a nie wewnątrz wątku roboczego).
            if fatal_signal.is_set():
                logging.critical(
                    f"Wykryto fatalny błąd w wątku roboczym: {fatal_signal.reason()}. "
                    f"Zamykanie aplikacji, systemd zrestartuje usługę..."
                )
                sys.exit(1)

            # KROK B: Wątek zakończył się bez zgłoszenia fatal_signal (nieoczekiwane,
            # ale na wszelki wypadek - np. gdyby ktoś kiedyś dodał return bez wyjątku)
            for name, t in threads.items():
                if not t.is_alive():
                    logging.critical(
                        f"Wątek '{name}' zakończył działanie nieoczekiwanie "
                        f"(bez zgłoszenia fatal_signal). Zamykanie aplikacji..."
                    )
                    sys.exit(1)

            # KROK C: Watchdog systemd - wysyłamy WATCHDOG=1 tylko jeśli OBA wątki
            # zgłosiły heartbeat w ciągu ostatnich UNHEALTHY_AFTER sekund. Jeśli nie -
            # milczymy, a systemd (WatchdogSec w pliku .service) sam zabije i zrestartuje
            # proces po przekroczeniu limitu. To siatka bezpieczeństwa dla zawieszeń
            # bez wyjątku (np. blokujące I/O), których fatal_signal by nie złapał.
            now = time.monotonic()
            if now - last_watchdog_notify >= WATCHDOG_NOTIFY_INTERVAL:
                if heartbeat.all_healthy(WORKER_NAMES, max_age=UNHEALTHY_AFTER):
                    notifier.notify("WATCHDOG=1")
                else:
                    status = heartbeat.status_summary(WORKER_NAMES, max_age=UNHEALTHY_AFTER)
                    logging.warning(f"Pomijam watchdog - nie wszystkie wątki zdrowe. Wiek heartbeatów (s): {status}")
                last_watchdog_notify = now

            time.sleep(MAIN_LOOP_INTERVAL)

    except KeyboardInterrupt:
        logging.info("Wykryto przerwanie z klawiatury (Ctrl+C). Zamykanie aplikacji...")
    finally:
        # Wykonuje się zarówno przy Ctrl+C, jak i przy sys.exit(1) powyżej -
        # dajemy wątkom szansę na czyste zamknięcie portów szeregowych.
        stop_event.set()
        for t in threads.values():
            t.join(timeout=5.0)
        logging.info("Aplikacja została bezpiecznie zatrzymana.")


if __name__ == "__main__":
    main()