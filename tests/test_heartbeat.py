import time
from unittest.mock import patch

from src.heartbeat import HeartbeatMonitor, FatalErrorSignal


# =============================================================================
# HeartbeatMonitor
# =============================================================================

def test_worker_that_never_beat_is_unhealthy():
    monitor = HeartbeatMonitor()
    assert monitor.is_healthy("miernik", max_age=5.0) is False


def test_worker_is_healthy_immediately_after_beat():
    monitor = HeartbeatMonitor()
    monitor.beat("miernik")
    assert monitor.is_healthy("miernik", max_age=5.0) is True


def test_worker_becomes_unhealthy_after_max_age_exceeded():
    monitor = HeartbeatMonitor()
    with patch("time.monotonic", return_value=100.0):
        monitor.beat("miernik")

    with patch("time.monotonic", return_value=100.0 + 5.01):
        assert monitor.is_healthy("miernik", max_age=5.0) is False


def test_worker_still_healthy_just_under_max_age():
    monitor = HeartbeatMonitor()
    with patch("time.monotonic", return_value=100.0):
        monitor.beat("miernik")

    with patch("time.monotonic", return_value=100.0 + 4.99):
        assert monitor.is_healthy("miernik", max_age=5.0) is True


def test_all_healthy_requires_every_worker_healthy():
    monitor = HeartbeatMonitor()
    monitor.beat("miernik")
    monitor.beat("arduino")
    assert monitor.all_healthy(["miernik", "arduino"], max_age=5.0) is True


def test_all_healthy_false_if_one_worker_never_beat():
    monitor = HeartbeatMonitor()
    monitor.beat("miernik")
    # "arduino" nigdy się nie zgłosił
    assert monitor.all_healthy(["miernik", "arduino"], max_age=5.0) is False


def test_status_summary_reports_age_and_none_for_missing():
    monitor = HeartbeatMonitor()
    with patch("time.monotonic", return_value=100.0):
        monitor.beat("miernik")

    with patch("time.monotonic", return_value=103.0):
        summary = monitor.status_summary(["miernik", "arduino"], max_age=5.0)

    assert summary["miernik"] == 3.0
    assert summary["arduino"] is None


def test_beat_from_multiple_threads_does_not_corrupt_state():
    import threading

    monitor = HeartbeatMonitor()
    names = [f"worker-{i}" for i in range(20)]

    def hammer(name):
        for _ in range(100):
            monitor.beat(name)

    threads = [threading.Thread(target=hammer, args=(n,)) for n in names]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert monitor.all_healthy(names, max_age=5.0) is True


# =============================================================================
# FatalErrorSignal
# =============================================================================

def test_fatal_signal_not_set_initially():
    signal = FatalErrorSignal()
    assert signal.is_set() is False
    assert signal.reason() is None


def test_fatal_signal_trigger_sets_and_records_reason():
    signal = FatalErrorSignal()
    signal.trigger("miernik", "błąd bazy danych")
    assert signal.is_set() is True
    assert signal.reason() == "[miernik] błąd bazy danych"


def test_fatal_signal_first_reason_wins_on_multiple_triggers():
    """Jeśli oba workery padną niemal jednocześnie, chcemy zachować powód
    PIERWSZEGO zdarzenia, a nie nadpisywać go kolejnym."""
    signal = FatalErrorSignal()
    signal.trigger("miernik", "pierwszy błąd")
    signal.trigger("arduino", "drugi błąd")

    assert signal.reason() == "[miernik] pierwszy błąd"


def test_fatal_signal_is_thread_safe_under_concurrent_trigger():
    import threading

    signal = FatalErrorSignal()

    def trigger_from(name):
        signal.trigger(name, "błąd")

    threads = [threading.Thread(target=trigger_from, args=(f"worker-{i}",)) for i in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert signal.is_set() is True
    assert signal.reason() is not None
