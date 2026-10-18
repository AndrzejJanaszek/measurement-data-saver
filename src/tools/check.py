import argparse
import ast
import contextlib
import sqlite3
import sys
import time
from dataclasses import dataclass, field, replace
from pathlib import Path

from src import config, worker_names
from src.heartbeat import UNHEALTHY_AFTER
from src.tools import system
from src.tools.report import (
    INSTALL_TOOLS_HINT,
    Level,
    Result,
    format_clock,
    format_duration,
    format_line,
    format_number,
    format_sections,
    format_time,
    format_verdict,
    plural,
)
from src.tools.sensors import TEMPERATURE_SENSORS
from src.tools.system import ServiceState

DEFAULT_WINDOW = 15
CLOCK_TOLERANCE = 60
LOW_DISK_BYTES = 1_000_000_000
CRITICAL_LOOKBACK = 300
SENSOR_ERROR_VALUES = (-127.0, 85.0)
LOG_LINE_LIMIT = 110
LOG_LINES_SHOWN = 3

METER_TAG = f"[{worker_names.METER}]"
ARDUINO_TAG = f"[{worker_names.ARDUINO}]"
WORKER_TAGS = {worker_names.METER: METER_TAG, worker_names.ARDUINO: ARDUINO_TAG}
PORT_OPEN_FAILED = "Nie udało się otworzyć portu"
UNPARSED_FRAME = "(niesparsowana) ramka: "
PORT_SILENT = "cisza na porcie"
EMPTY_TEMPS = "Ramka 'temps' nie zawierała żadnych poprawnych odczytów"
WATCHDOG_SKIPPED = "Pomijam watchdog"
WATCHDOG_AGES = "(s): "
CRITICAL_MARKER = "[CRITICAL]"
ERROR_MARKERS = ("[ERROR]", CRITICAL_MARKER, "Traceback")
METER_NO_RECORDS = "Miernik: brak nowych zapisów."

CLOCK = "Zegar"
SERVICE = "Usługa zapisu danych"
METER = "Miernik"
ARDUINO = "Arduino"
DISK = "Dysk"
SECTIONS = (CLOCK, SERVICE, METER, ARDUINO, DISK)

TEMPERATURE_HINTS = (
    "Przewód czujnika może nie stykać. Arduino szuka czujników ponownie co 60 s –",
    "po poprawieniu podłączenia odczekaj minutę i uruchom sprawdzanie jeszcze raz.",
)


@dataclass(frozen=True)
class Port:
    path: str
    exists: bool
    others: tuple[str, ...] = ()


@dataclass(frozen=True)
class Window:
    new_measurements: int = 0
    last_measurement: float | None = None
    temperatures: dict[str, float] = field(default_factory=dict)
    pin_changes: list[tuple[float, int, int]] = field(default_factory=list)
    session_pins: dict[int, int] = field(default_factory=dict)


@dataclass(frozen=True)
class Snapshot:
    service_before: ServiceState
    service_after: ServiceState
    meter_port: Port
    arduino_port: Port
    observed: bool = True
    journal_readable: bool = True
    journal: list[str] = field(default_factory=list)
    window: Window = field(default_factory=Window)
    database_error: str | None = None
    free_disk_bytes: int | None = None
    clock_offset: float | None = None


def ok(section: str, text: str) -> Result:
    return Result(section, Level.OK, text)


def info(section: str, text: str) -> Result:
    return Result(section, Level.INFO, text)


def warning(section: str, text: str, hints: tuple[str, ...] = (), label: str = "") -> Result:
    return Result(section, Level.WARNING, text, hints, label)


def error(section: str, text: str, hints: tuple[str, ...] = (), label: str = "") -> Result:
    return Result(section, Level.ERROR, text, hints, label)


def tagged(lines: list[str], tag: str) -> list[str]:
    return [line for line in lines if tag in line]


def contains(lines: list[str], text: str) -> bool:
    return any(text in line for line in lines)


def shorten(line: str) -> str:
    if len(line) <= LOG_LINE_LIMIT:
        return line
    return line[:LOG_LINE_LIMIT - 1] + "…"


def evaluate(snapshot: Snapshot) -> list[Result]:
    if snapshot.observed:
        meter, arduino = evaluate_meter(snapshot), evaluate_arduino(snapshot)
        devices = meter + arduino
        explained = explained_tags([meter[0], arduino[0]])
    else:
        devices = evaluate_ports(snapshot)
        explained = ()

    results = evaluate_clock(snapshot) + evaluate_service(snapshot, explained) + devices + evaluate_disk(snapshot)
    return sorted(results, key=lambda result: SECTIONS.index(result.section))


def explained_tags(device_states: list[Result]) -> tuple[str, ...]:
    tags = {METER: METER_TAG, ARDUINO: ARDUINO_TAG}
    return tuple(
        tags[result.section] for result in device_states
        if result.level is Level.ERROR and result.text != METER_NO_RECORDS
    )


def stale_workers(line: str) -> set[str]:
    try:
        ages = ast.literal_eval(line.split(WATCHDOG_AGES, 1)[1])
    except (IndexError, ValueError, SyntaxError):
        return set()

    if not isinstance(ages, dict):
        return set()
    return {name for name, age in ages.items() if age is None or age > UNHEALTHY_AFTER}


def watchdog_explained(line: str, explained: tuple[str, ...]) -> bool:
    stale = stale_workers(line)
    return bool(stale) and all(WORKER_TAGS.get(name) in explained for name in stale)


def evaluate_clock(snapshot: Snapshot) -> list[Result]:
    offset = snapshot.clock_offset
    if offset is None:
        return []

    if abs(offset) <= CLOCK_TOLERANCE:
        return [ok(CLOCK, "Zegar zgodny z laptopem.")]

    return [warning(
        CLOCK,
        f"Zegar Raspberry Pi różni się od laptopa o {format_duration(offset)}.",
        ("Uruchom Ustaw_czas_Raspberry_Pi.bat.",),
        "zegar",
    )]


def evaluate_service(snapshot: Snapshot, explained: tuple[str, ...]) -> list[Result]:
    results = [service_status(snapshot)]
    after = snapshot.service_after

    if after.unit_file_state != "enabled":
        results.append(warning(
            SERVICE,
            "Usługa nie uruchomi się sama po ponownym włączeniu Raspberry Pi.",
            label="brak autostartu usługi",
        ))

    if not snapshot.journal_readable:
        results.append(warning(
            SERVICE,
            "Brak dostępu do logów usługi – przyczyny braków danych nie zostaną rozpoznane.",
            (INSTALL_TOOLS_HINT,),
            "brak dostępu do logów",
        ))

    if snapshot.database_error:
        results.append(error(
            SERVICE,
            f"Nie można odczytać bazy danych ({config.DB_PATH}): {snapshot.database_error}.",
            label="baza danych",
        ))

    if snapshot.observed:
        results += evaluate_service_logs(snapshot.journal, explained)

    return results


def service_status(snapshot: Snapshot) -> Result:
    before, after = snapshot.service_before, snapshot.service_after

    if not after.is_active:
        critical = tagged(snapshot.journal, CRITICAL_MARKER)
        hints = ("Ostatni błąd krytyczny:", shorten(critical[-1])) if critical else ()
        return error(SERVICE, f"Usługa nie działa (stan: {after.active_state}).", hints, "usługa nie działa")

    if before.main_pid != after.main_pid or before.restarts != after.restarts:
        return error(SERVICE, "Usługa uruchomiła się ponownie w trakcie sprawdzania.", label="restart usługi")

    since = format_time(after.active_since) if after.active_since else "nieznanego czasu"
    return ok(SERVICE, f"Usługa działa od {since}.")


def evaluate_service_logs(journal: list[str], explained: tuple[str, ...]) -> list[Result]:
    results = []

    skipped = len([line for line in tagged(journal, WATCHDOG_SKIPPED) if not watchdog_explained(line, explained)])
    if skipped:
        results.append(warning(
            SERVICE,
            f"Usługa zgłaszała zawieszenie wątku ({skipped} {plural(skipped, 'raz', 'razy', 'razy')}).",
            label="zawieszenie wątku",
        ))

    errors = [
        line for line in journal
        if any(marker in line for marker in ERROR_MARKERS)
        and not any(tag in line for tag in explained)
    ]
    if errors:
        results.append(warning(
            SERVICE,
            f"Błędy w logach usługi ({len(errors)}), ostatnie:",
            tuple(shorten(line) for line in errors[-LOG_LINES_SHOWN:]),
            "błędy w logach",
        ))

    return results


def port_missing(section: str, text: str, hint: str, port: Port, label: str) -> Result:
    hints = [hint]
    if port.others:
        hints.append(f"Wykryto inne porty: {', '.join(port.others)}.")
    return error(section, text, tuple(hints), label)


def meter_port_missing(port: Port) -> Result:
    return port_missing(
        METER,
        f"Miernik: nie wykryto przejściówki USB (brak {port.path}).",
        "Wtyczka USB przejściówki może nie stykać.",
        port,
        "miernik",
    )


def arduino_port_missing(port: Port) -> Result:
    return port_missing(
        ARDUINO,
        f"Arduino: nie wykryto (brak {port.path}).",
        "Kabel USB Arduino może nie stykać.",
        port,
        "Arduino",
    )


def evaluate_ports(snapshot: Snapshot) -> list[Result]:
    meter, arduino = snapshot.meter_port, snapshot.arduino_port
    results = []

    if meter.exists:
        results.append(info(METER, "Miernik: przejściówka wykryta, dane niesprawdzone."))
    else:
        results.append(meter_port_missing(meter))

    if arduino.exists:
        results.append(info(ARDUINO, "Arduino: wykryte, dane niesprawdzone."))
    else:
        results.append(arduino_port_missing(arduino))

    return results


def evaluate_meter(snapshot: Snapshot) -> list[Result]:
    window, port = snapshot.window, snapshot.meter_port
    lines = tagged(snapshot.journal, METER_TAG)

    if window.new_measurements:
        count = window.new_measurements
        value = format_number(window.last_measurement)
        return [ok(METER, f"Miernik: wskazanie {value} (+{count} {plural(count, 'zapis', 'zapisy', 'zapisów')}).")]

    if not port.exists:
        return [meter_port_missing(port)]

    if contains(lines, PORT_OPEN_FAILED):
        return [error(METER, "Miernik: przejściówka wykryta, ale nie da się otworzyć portu.", label="miernik")]

    frames = [line.split(UNPARSED_FRAME, 1)[1] for line in lines if UNPARSED_FRAME in line]
    if frames:
        return [error(
            METER,
            f"Miernik: przychodzą dane w nieoczekiwanym formacie, np. {shorten(frames[-1])}.",
            ("Ustawienia transmisji w mierniku mogą być inne niż oczekiwane.",),
            "miernik",
        )]

    if contains(lines, PORT_SILENT):
        return [error(
            METER,
            "Miernik: przejściówka wykryta, brak danych z miernika.",
            (
                "Miernik może być wyłączony, kabel RS232 może nie stykać",
                "albo wysyłanie ciągłe w mierniku może być wyłączone.",
            ),
            "miernik",
        )]

    return [error(METER, METER_NO_RECORDS, label="miernik")]


def evaluate_arduino(snapshot: Snapshot) -> list[Result]:
    window, port = snapshot.window, snapshot.arduino_port
    lines = tagged(snapshot.journal, ARDUINO_TAG)

    if not port.exists:
        return [arduino_port_missing(port)]

    empty_temps = contains(lines, EMPTY_TEMPS)
    if window.temperatures or window.pin_changes or empty_temps:
        results = [ok(ARDUINO, "Arduino: podłączone, wysyła dane.")]
        return results + evaluate_temperatures(window, empty_temps) + evaluate_signals(window)

    if contains(lines, PORT_OPEN_FAILED):
        return [error(ARDUINO, "Arduino: wykryte, ale nie da się otworzyć portu.", label="Arduino")]

    return [error(ARDUINO, "Arduino: wykryte, brak danych z Arduino.", label="Arduino")]


def evaluate_temperatures(window: Window, empty_temps: bool) -> list[Result]:
    if not window.temperatures and empty_temps:
        return [error(
            ARDUINO,
            "Temperatury: Arduino nie wykrywa żadnego czujnika.",
            TEMPERATURE_HINTS,
            "czujniki temperatury",
        )]

    by_number = sorted(TEMPERATURE_SENSORS.items(), key=lambda item: item[1])
    results = [sensor_result(number, window.temperatures.get(address)) for address, number in by_number]

    for address, value in window.temperatures.items():
        if address not in TEMPERATURE_SENSORS:
            results.append(warning(
                ARDUINO,
                f"Nieznany czujnik {address}: {format_number(value, 1)} °C.",
                label="nieznany czujnik temperatury",
            ))

    return with_temperature_hint(results)


def sensor_result(number: int, value: float | None) -> Result:
    label = f"czujnik temperatury {number}"

    if value is None:
        return error(ARDUINO, f"Czujnik {number}: brak odczytu.", label=label)
    if value in SENSOR_ERROR_VALUES:
        return error(ARDUINO, f"Czujnik {number}: błąd odczytu.", label=label)

    return ok(ARDUINO, f"Czujnik {number}: {format_number(value, 1)} °C")


def with_temperature_hint(results: list[Result]) -> list[Result]:
    errors = [index for index, result in enumerate(results) if result.level is Level.ERROR]
    if not errors:
        return results

    last = errors[-1]
    return results[:last] + [replace(results[last], hints=TEMPERATURE_HINTS)] + results[last + 1:]


def evaluate_signals(window: Window) -> list[Result]:
    if not window.session_pins:
        return [error(ARDUINO, "Sygnały: brak stanu sygnałów z Arduino.", label="sygnały")]

    states = " ".join(f"{pin}={value}" for pin, value in sorted(window.session_pins.items()))
    results = [ok(ARDUINO, f"Sygnały odebrane: {states}")]

    for timestamp, pin, value in window.pin_changes:
        results.append(info(ARDUINO, f"Zmiana sygnału w trakcie sprawdzania: pin {pin} → {value} ({format_clock(timestamp)})"))

    return results


def evaluate_disk(snapshot: Snapshot) -> list[Result]:
    free = snapshot.free_disk_bytes
    if free is None or free >= LOW_DISK_BYTES:
        return []

    return [warning(DISK, f"Mało miejsca na dysku: {format_number(free / 1e9, 1)} GB.", label="mało miejsca na dysku")]


def open_database() -> sqlite3.Connection:
    uri = Path(config.DB_PATH).resolve().as_uri() + "?mode=ro"
    return sqlite3.connect(uri, uri=True, timeout=3)


def read_max_ids(conn: sqlite3.Connection) -> dict[str, int]:
    tables = ("measurements", "temperature_measurements", "pin_states")
    return {table: conn.execute(f"SELECT COALESCE(MAX(id), 0) FROM {table}").fetchone()[0] for table in tables}


def read_window(conn: sqlite3.Connection, max_ids: dict[str, int]) -> Window:
    measurements = conn.execute(
        "SELECT value FROM measurements WHERE id > ? ORDER BY id",
        (max_ids["measurements"],),
    ).fetchall()

    temperatures = conn.execute(
        "SELECT sensor_address, value FROM temperature_measurements WHERE id > ? ORDER BY id",
        (max_ids["temperature_measurements"],),
    ).fetchall()

    pin_changes = conn.execute(
        "SELECT timestamp, pin, value FROM pin_states WHERE id > ? ORDER BY id",
        (max_ids["pin_states"],),
    ).fetchall()

    session_pins = conn.execute(
        "SELECT pin, value FROM pin_states WHERE session_id = (SELECT MAX(id) FROM sessions) ORDER BY id"
    ).fetchall()

    return Window(
        new_measurements=len(measurements),
        last_measurement=measurements[-1][0] if measurements else None,
        temperatures=dict(temperatures),
        pin_changes=pin_changes,
        session_pins=dict(session_pins),
    )


def query_database(query, *args):
    with contextlib.closing(open_database()) as conn:
        return query(conn, *args)


def read_port(path: str) -> Port:
    return Port(path, system.port_exists(path), tuple(system.other_ports(path)))


def countdown(seconds: int):
    for remaining in range(seconds, 0, -1):
        print(f"\rSprawdzam... wynik za {remaining:2d} s ", end="", flush=True)
        time.sleep(1)
    print("\r" + " " * 40 + "\r", end="", flush=True)


def unobserved(before: ServiceState, after: ServiceState, journal_since: float, database_error: str | None, common: dict) -> Snapshot:
    return Snapshot(
        service_before=before,
        service_after=after,
        meter_port=read_port(config.SERIAL_PORT),
        arduino_port=read_port(config.ARDUINO_SERIAL_PORT),
        observed=False,
        journal=system.read_journal(journal_since),
        database_error=database_error,
        free_disk_bytes=system.free_disk_bytes(config.DB_PATH),
        **common,
    )


def collect(window_seconds: int, clock_offset: float | None) -> Snapshot:
    before = system.read_service()
    started = time.time()
    common = {"clock_offset": clock_offset, "journal_readable": system.journal_readable()}

    if not before.is_active:
        return unobserved(before, before, started - CRITICAL_LOOKBACK, None, common)

    try:
        max_ids = query_database(read_max_ids)
    except sqlite3.Error as exception:
        return unobserved(before, before, started - CRITICAL_LOOKBACK, str(exception), common)

    countdown(window_seconds)
    after = system.read_service()

    try:
        window = query_database(read_window, max_ids)
    except sqlite3.Error as exception:
        return unobserved(before, after, started, str(exception), common)

    return Snapshot(
        service_before=before,
        service_after=after,
        meter_port=read_port(config.SERIAL_PORT),
        arduino_port=read_port(config.ARDUINO_SERIAL_PORT),
        journal=system.read_journal(started),
        window=window,
        free_disk_bytes=system.free_disk_bytes(config.DB_PATH),
        **common,
    )


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="python -m src.tools.check")
    parser.add_argument("--window", type=int, default=DEFAULT_WINDOW)
    parser.add_argument("--laptop-time", type=float)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    now = time.time()
    clock_offset = None if args.laptop_time is None else now - args.laptop_time

    print(format_line(ok("", f"Połączono z Raspberry Pi ({system.hostname()}), {format_time(now)}")))
    print()

    results = evaluate(collect(args.window, clock_offset))

    print(format_sections(results))
    print()
    print(format_verdict(results))

    return 1 if any(result.is_problem for result in results) else 0


if __name__ == "__main__":
    sys.exit(main())
