import contextlib
import sqlite3
from dataclasses import replace

import pytest

from src import config, database
from src.tools import check, report
from src.tools.check import Port, Snapshot, Window, evaluate
from src.tools.report import Level
from src.tools.system import ServiceState

SENSORS = {
    "28A8F126AB240B1C": 21.44,
    "28989298AA240B81": 22.0,
    "287C36E9AB240BD4": 20.5,
    "2833D8EDAB240BF3": 19.75,
}
PINS = {4: 1, 5: 1, 6: 0, 7: 1, 8: 1, 9: 1}

RUNNING = ServiceState("active", "enabled", "1234", "0", 1792319840.0)

METER_PORT_SILENT = "2026-10-18 09:15:02,123 - [WARNING] - [miernik] Minęła sekunda, ale port nie wysłał żadnej ramki (cisza na porcie)."
METER_OPEN_FAILED = "2026-10-18 09:15:02,123 - [ERROR] - [miernik] Nie udało się otworzyć portu: [Errno 2] could not open port"
METER_UNPARSED = "2026-10-18 09:15:02,123 - [WARNING] - [miernik] Minęła sekunda, ale nie odebrano poprawnej ramki danych. Ostatnia otrzymana (niesparsowana) ramka: b'ST,GS'"
ARDUINO_EMPTY = "2026-10-18 09:15:02,123 - [WARNING] - [arduino] Ramka 'temps' nie zawierała żadnych poprawnych odczytów: '{\"temps\":[]}'"
ARDUINO_OPEN_FAILED = "2026-10-18 09:15:02,123 - [ERROR] - [arduino] Nie udało się otworzyć portu: [Errno 2] could not open port"
WATCHDOG = "2026-10-18 09:15:02,123 - [WARNING] - Pomijam watchdog - nie wszystkie wątki zdrowe. Wiek heartbeatów (s): {}"
WATCHDOG_STALE_METER = WATCHDOG.replace("{}", "{'miernik': 9.5, 'arduino': 0.3}")
WATCHDOG_STALE_ARDUINO = WATCHDOG.replace("{}", "{'miernik': 0.2, 'arduino': None}")
METER_PORT_ERROR = "2026-10-18 09:15:02,123 - [ERROR] - [miernik] Błąd portu szeregowego: device reports readiness to read but returned no data."
ARDUINO_PORT_ERROR = "2026-10-18 09:15:02,123 - [ERROR] - [arduino] Błąd portu szeregowego: device reports readiness to read but returned no data."
DATABASE_ERROR = "2026-10-18 09:15:02,123 - [ERROR] - [measurements] Błąd operacyjny bazy danych (Próba 1/2): disk I/O error"
CRITICAL = "2026-10-18 09:15:02,123 - [CRITICAL] - Nie można zainicjalizować bazy danych. Zamykanie aplikacji."


@pytest.fixture(autouse=True)
def no_color(monkeypatch):
    monkeypatch.setenv("NO_COLOR", "1")


def healthy(**changes) -> Snapshot:
    snapshot = Snapshot(
        service_before=RUNNING,
        service_after=RUNNING,
        meter_port=Port("/dev/ttyUSB0", True),
        arduino_port=Port("/dev/ttyACM0", True),
        window=Window(
            new_measurements=15,
            last_measurement=1234.5,
            temperatures=dict(SENSORS),
            session_pins=dict(PINS),
        ),
        free_disk_bytes=20_000_000_000,
        clock_offset=2.0,
    )
    return replace(snapshot, **changes)


def with_window(**changes) -> Snapshot:
    return healthy(window=replace(healthy().window, **changes))


def problems(results) -> list[report.Result]:
    return [result for result in results if result.is_problem]


def texts(results, section: str | None = None) -> list[str]:
    return [result.text for result in results if section is None or result.section == section]


def test_healthy_snapshot_has_no_problems():
    results = evaluate(healthy())

    assert problems(results) == []
    assert report.format_verdict(results) == "WYNIK: WSZYSTKO DZIAŁA"


def test_healthy_snapshot_reports_every_section_in_order():
    sections = list(dict.fromkeys(result.section for result in evaluate(healthy())))

    assert sections == [check.CLOCK, check.SERVICE, check.METER, check.ARDUINO]


def test_healthy_meter_shows_last_value_and_count():
    assert "Miernik: wskazanie 1234,5 (+15 zapisów)." in texts(evaluate(healthy()))


def test_clock_is_skipped_without_laptop_time():
    assert texts(evaluate(healthy(clock_offset=None)), check.CLOCK) == []


def test_clock_offset_over_tolerance_is_a_warning():
    results = evaluate(healthy(clock_offset=-(3 * 3600 + 12 * 60)))

    assert [result.level for result in problems(results)] == [Level.WARNING]
    assert "Zegar Raspberry Pi różni się od laptopa o 3 h 12 min." in texts(results)


def test_inactive_service_reports_last_critical_line_and_only_ports():
    stopped = ServiceState("failed", "enabled", "0", "3", None)
    results = evaluate(healthy(
        service_before=stopped,
        service_after=stopped,
        observed=False,
        journal=["other line", CRITICAL],
    ))

    service = [result for result in results if result.section == check.SERVICE][0]
    assert service.level is Level.ERROR
    assert service.text == "Usługa nie działa (stan: failed)."
    assert service.hints[-1] == check.shorten(CRITICAL)
    assert texts(results, check.METER) == ["Miernik: przejściówka wykryta, dane niesprawdzone."]
    assert len(problems(results)) == 1


def test_restart_during_window_is_an_error():
    results = evaluate(healthy(service_after=replace(RUNNING, main_pid="5678", restarts="1")))

    assert "Usługa uruchomiła się ponownie w trakcie sprawdzania." in texts(problems(results))


def test_disabled_unit_is_a_warning():
    results = evaluate(healthy(service_after=replace(RUNNING, unit_file_state="disabled")))

    assert [result.label for result in problems(results)] == ["brak autostartu usługi"]


def test_watchdog_skips_are_counted():
    results = evaluate(healthy(journal=[WATCHDOG, WATCHDOG]))

    assert "Usługa zgłaszała zawieszenie wątku (2 razy)." in texts(results)


def test_unexplained_error_lines_are_a_warning_with_last_three():
    journal = [DATABASE_ERROR] * 4
    results = evaluate(healthy(journal=journal))

    result = problems(results)[0]
    assert result.text == "Błędy w logach usługi (4), ostatnie:"
    assert len(result.hints) == 3


def test_unreadable_journal_is_a_warning():
    results = evaluate(healthy(journal_readable=False))

    assert [result.label for result in problems(results)] == ["brak dostępu do logów"]


def test_unplugged_meter_is_exactly_one_problem():
    journal = [METER_OPEN_FAILED, METER_PORT_SILENT, METER_OPEN_FAILED]
    snapshot = with_window(new_measurements=0, last_measurement=None)
    results = evaluate(replace(snapshot, meter_port=Port("/dev/ttyUSB0", False), journal=journal))

    assert len(problems(results)) == 1
    assert problems(results)[0].text == "Miernik: nie wykryto przejściówki USB (brak /dev/ttyUSB0)."
    assert report.format_verdict(results) == "WYNIK: 1 problem – miernik"


def test_watchdog_skip_caused_by_unplugged_meter_is_explained():
    journal = [METER_OPEN_FAILED, WATCHDOG_STALE_METER, METER_PORT_SILENT]
    snapshot = with_window(new_measurements=0, last_measurement=None)
    results = evaluate(replace(snapshot, meter_port=Port("/dev/ttyUSB0", False), journal=journal))

    assert [result.label for result in problems(results)] == ["miernik"]


def test_watchdog_skip_of_working_arduino_is_a_warning():
    snapshot = with_window(new_measurements=0, last_measurement=None)
    results = evaluate(replace(snapshot, meter_port=Port("/dev/ttyUSB0", False), journal=[WATCHDOG_STALE_ARDUINO]))

    assert [result.label for result in problems(results)] == ["zawieszenie wątku", "miernik"]


def test_sensor_error_does_not_hide_arduino_port_errors():
    snapshot = with_window(temperatures={**SENSORS, "287C36E9AB240BD4": -127.0})
    results = evaluate(replace(snapshot, journal=[ARDUINO_PORT_ERROR]))

    assert [result.label for result in problems(results)] == ["błędy w logach", "czujnik temperatury 3"]


def test_meter_without_known_cause_does_not_hide_its_logs():
    snapshot = with_window(new_measurements=0, last_measurement=None)
    results = evaluate(replace(snapshot, journal=[METER_PORT_ERROR, WATCHDOG_STALE_METER]))

    assert [result.label for result in problems(results)] == ["zawieszenie wątku", "błędy w logach", "miernik"]
    assert texts(problems(results), check.METER) == ["Miernik: brak nowych zapisów."]


def test_missing_meter_port_lists_other_ports():
    snapshot = with_window(new_measurements=0, last_measurement=None)
    results = evaluate(replace(snapshot, meter_port=Port("/dev/ttyUSB0", False, ("/dev/ttyUSB1",))))

    assert "Wykryto inne porty: /dev/ttyUSB1." in problems(results)[0].hints


@pytest.mark.parametrize(
    ("journal", "expected"),
    [
        ([METER_OPEN_FAILED, METER_PORT_SILENT], "Miernik: przejściówka wykryta, ale nie da się otworzyć portu."),
        ([METER_UNPARSED, METER_PORT_SILENT], "Miernik: przychodzą dane w nieoczekiwanym formacie, np. b'ST,GS'."),
        ([METER_PORT_SILENT], "Miernik: przejściówka wykryta, brak danych z miernika."),
        ([], "Miernik: brak nowych zapisów."),
    ],
)
def test_meter_rules_in_order(journal, expected):
    snapshot = with_window(new_measurements=0, last_measurement=None)
    results = evaluate(replace(snapshot, journal=journal))

    assert texts(problems(results)) == [expected]


def test_unplugged_arduino_skips_temperatures_and_signals():
    snapshot = with_window(temperatures={}, session_pins={})
    results = evaluate(replace(snapshot, arduino_port=Port("/dev/ttyACM0", False), journal=[ARDUINO_OPEN_FAILED]))

    assert texts(results, check.ARDUINO) == ["Arduino: nie wykryto (brak /dev/ttyACM0)."]
    assert len(problems(results)) == 1


def test_arduino_port_open_failure():
    snapshot = with_window(temperatures={})
    results = evaluate(replace(snapshot, journal=[ARDUINO_OPEN_FAILED]))

    assert texts(results, check.ARDUINO) == ["Arduino: wykryte, ale nie da się otworzyć portu."]
    assert len(problems(results)) == 1


def test_silent_arduino():
    results = evaluate(with_window(temperatures={}))

    assert texts(results, check.ARDUINO) == ["Arduino: wykryte, brak danych z Arduino."]


def test_empty_temps_means_arduino_works_but_sees_no_sensor():
    results = evaluate(replace(with_window(temperatures={}), journal=[ARDUINO_EMPTY] * 10))

    assert texts(results, check.ARDUINO)[:2] == [
        "Arduino: podłączone, wysyła dane.",
        "Temperatury: Arduino nie wykrywa żadnego czujnika.",
    ]
    assert [result.label for result in problems(results)] == ["czujniki temperatury"]


@pytest.mark.parametrize("value", [-127.0, 85.0])
def test_sensor_error_value(value):
    results = evaluate(with_window(temperatures={**SENSORS, "287C36E9AB240BD4": value}))

    assert texts(problems(results)) == ["Czujnik 3: błąd odczytu."]
    assert problems(results)[0].hints == check.TEMPERATURE_HINTS
    assert report.format_verdict(results) == "WYNIK: 1 problem – czujnik temperatury 3"


def test_missing_sensors_share_one_hint():
    temperatures = {"28A8F126AB240B1C": 21.0, "28989298AA240B81": 22.0}
    results = evaluate(with_window(temperatures=temperatures))

    sensor_errors = problems(results)
    assert texts(sensor_errors) == ["Czujnik 3: brak odczytu.", "Czujnik 4: brak odczytu."]
    assert [bool(result.hints) for result in sensor_errors] == [False, True]


def test_healthy_sensor_text():
    assert "Czujnik 1: 21,4 °C" in texts(evaluate(healthy()))


def test_unknown_sensor_is_a_warning():
    results = evaluate(with_window(temperatures={**SENSORS, "28FFFFFFFFFFFFFF": 23.5}))

    assert texts(problems(results)) == ["Nieznany czujnik 28FFFFFFFFFFFFFF: 23,5 °C."]
    assert problems(results)[0].level is Level.WARNING


def test_missing_signals_is_an_error():
    results = evaluate(with_window(session_pins={}))

    assert texts(problems(results)) == ["Sygnały: brak stanu sygnałów z Arduino."]


def test_signal_state_is_listed():
    assert "Sygnały odebrane: 4=1 5=1 6=0 7=1 8=1 9=1" in texts(evaluate(healthy()))


def test_signal_change_is_info_only():
    results = evaluate(with_window(pin_changes=[(1792319840.0, 6, 0)]))

    changes = [result for result in results if result.level is Level.INFO]
    assert len(changes) == 1
    assert changes[0].text.startswith("Zmiana sygnału w trakcie sprawdzania: pin 6 → 0 (")
    assert problems(results) == []


def test_low_disk_is_a_warning():
    results = evaluate(healthy(free_disk_bytes=600_000_000))

    assert texts(results, check.DISK) == ["Mało miejsca na dysku: 0,6 GB."]


def test_database_error_reports_only_ports():
    results = evaluate(healthy(observed=False, database_error="unable to open database file"))

    assert [result.label for result in problems(results)] == ["baza danych"]
    assert texts(results, check.METER) == ["Miernik: przejściówka wykryta, dane niesprawdzone."]


def test_verdict_counts_and_colors_problems(monkeypatch):
    results = evaluate(healthy(clock_offset=600, free_disk_bytes=1))

    assert report.format_verdict(results) == "WYNIK: 2 problemy – zegar, mało miejsca na dysku"

    monkeypatch.delenv("NO_COLOR")
    assert report.format_verdict(results).startswith("\033[33m")


def write_rows(session_id: int, measurements: int, temperatures: dict[str, float], pins: dict[int, int]):
    with contextlib.closing(database.open_worker_connection("test")) as conn:
        for _ in range(measurements):
            database.save_measurement(conn, 1000.0, 10.5, session_id)
        database.save_temperature_batch(conn, 1000.0, list(temperatures.items()), session_id)
        database.save_pin_states_batch(conn, 1000.0, pins, session_id)


def test_database_window_reads_only_new_rows():
    old_session = database.create_session()
    write_rows(old_session, 3, {"28A8F126AB240B1C": 18.0}, {4: 0})

    max_ids = check.query_database(check.read_max_ids)

    session = database.create_session()
    write_rows(session, 2, {"28A8F126AB240B1C": 21.0, "28989298AA240B81": 22.0}, {4: 1, 5: 1})
    write_rows(session, 0, {"28A8F126AB240B1C": 21.5}, {5: 0})

    window = check.query_database(check.read_window, max_ids)

    assert window.new_measurements == 2
    assert window.last_measurement == 10.5
    assert window.temperatures == {"28A8F126AB240B1C": 21.5, "28989298AA240B81": 22.0}
    assert [(pin, value) for _, pin, value in window.pin_changes] == [(4, 1), (5, 1), (5, 0)]
    assert window.session_pins == {4: 1, 5: 0}


def test_database_is_opened_read_only():
    with contextlib.closing(check.open_database()) as conn:
        with pytest.raises(sqlite3.OperationalError):
            conn.execute("INSERT INTO sessions (started_at) VALUES (1.0)")


def fake_collect_environment(monkeypatch, query_database):
    monkeypatch.setattr(check, "query_database", query_database)
    monkeypatch.setattr(check, "countdown", lambda _: None)
    monkeypatch.setattr(check.system, "read_service", lambda: RUNNING)
    monkeypatch.setattr(check.system, "journal_readable", lambda: True)
    monkeypatch.setattr(check.system, "read_journal", lambda since: [])


def test_database_error_after_window_is_reported_not_raised(monkeypatch):
    def query_database(query, *args):
        if query is check.read_max_ids:
            return {"measurements": 0, "temperature_measurements": 0, "pin_states": 0}
        raise sqlite3.OperationalError("database is locked")

    fake_collect_environment(monkeypatch, query_database)

    snapshot = check.collect(15, None)

    assert not snapshot.observed
    assert snapshot.database_error == "database is locked"
    assert [result.label for result in problems(evaluate(snapshot))] == ["baza danych", "miernik", "Arduino"]


def test_missing_database_raises_sqlite_error(tmp_path):
    config.DB_PATH = str(tmp_path / "missing.db")

    with pytest.raises(sqlite3.Error):
        check.query_database(check.read_max_ids)


@pytest.mark.parametrize(
    ("seconds", "expected"),
    [
        (42, "42 s"),
        (5 * 60, "5 min"),
        (3 * 3600 + 12 * 60, "3 h 12 min"),
        (86400, "1 dzień"),
        (3 * 86400, "3 dni"),
        (215 * 86400, "7 mies."),
        (2 * 365 * 86400, "2 lata"),
    ],
)
def test_format_duration(seconds, expected):
    assert report.format_duration(seconds) == expected


@pytest.mark.parametrize(("count", "expected"), [(1, "problem"), (3, "problemy"), (5, "problemów"), (12, "problemów"), (22, "problemy")])
def test_plural(count, expected):
    assert report.plural(count, "problem", "problemy", "problemów") == expected
