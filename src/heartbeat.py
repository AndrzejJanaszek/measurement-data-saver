# src/heartbeat.py
import threading
import time

HEARTBEAT_INTERVAL = 3.0
UNHEALTHY_AFTER = 7.0


class HeartbeatMonitor:
    """
    Współdzielony obiekt: workery zgłaszają "żyję" (beat), main.py sprawdza
    świeżość zgłoszeń przed wysłaniem WATCHDOG=1 do systemd.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._last_beat: dict[str, float] = {}

    def beat(self, worker_name: str):
        with self._lock:
            self._last_beat[worker_name] = time.monotonic()

    def is_healthy(self, worker_name: str, max_age: float = UNHEALTHY_AFTER) -> bool:
        """
        Zdrowy = worker zgłosił się w ciągu ostatnich max_age sekund.
        Worker, który jeszcze nigdy się nie zgłosił, jest traktowany jako
        niezdrowy (jeszcze się nie rozkręcił albo padł przed pierwszym beat).
        """
        with self._lock:
            last = self._last_beat.get(worker_name)
        if last is None:
            return False
        return (time.monotonic() - last) <= max_age

    def all_healthy(self, worker_names, max_age: float = UNHEALTHY_AFTER) -> bool:
        return all(self.is_healthy(name, max_age) for name in worker_names)

    def status_summary(self, worker_names, max_age: float = UNHEALTHY_AFTER) -> dict:
        """Wiek (w sekundach) ostatniego heartbeatu każdego workera - do logów diagnostycznych."""
        now = time.monotonic()
        with self._lock:
            return {
                name: round(now - self._last_beat[name], 2) if name in self._last_beat else None
                for name in worker_names
            }


class FatalErrorSignal:
    """
    Współdzielony sygnał: worker ustawia go, napotkawszy błąd po którym nie ma
    sensu kontynuować (np. DatabaseFatalError albo dowolny nieoczekiwany wyjątek).
    main.py widzi to i bezpiecznie kończy cały proces (sys.exit) z poziomu
    głównego wątku - patrz komentarz w database.DatabaseFatalError, dlaczego
    sys.exit() musi być wywołany stąd, a nie z wnętrza wątku roboczego.
    """

    def __init__(self):
        self._event = threading.Event()
        self._lock = threading.Lock()
        self._reason: str | None = None

    def trigger(self, worker_name: str, reason: str):
        with self._lock:
            if not self._event.is_set():
                self._reason = f"[{worker_name}] {reason}"
        self._event.set()

    def is_set(self) -> bool:
        return self._event.is_set()

    def reason(self) -> str | None:
        with self._lock:
            return self._reason