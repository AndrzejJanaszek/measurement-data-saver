import argparse
import contextlib
import hashlib
import sqlite3
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from src import config
from src.tools import service_restart, system
from src.tools.report import (
    Level,
    Result,
    error,
    format_duration,
    format_line,
    format_number,
    format_time,
    info,
    ok,
    plural,
    warning,
)

SECTION = "Zgrywanie bazy"
DUMP_DIR = Path.home() / "measurement-data-saver-dumps"
DUMP_NAME = "dump.db"
PART_NAME = "dump.db.part"
PART_JOURNAL_NAME = "dump.db.part-journal"
CHECKSUM_NAME = "dump.sha256"
DUMP_FILES = (DUMP_NAME, PART_NAME, PART_JOURNAL_NAME, CHECKSUM_NAME)

SPACE_MARGIN_BYTES = 200_000_000
NEW_SESSION_TIMEOUT = 20.0
POLL_INTERVAL = 0.5
HASH_CHUNK_BYTES = 1 << 20

EXIT_OK = 0
EXIT_PROBLEMS = 3
EXIT_NO_DUMP = 2

CHECK_HINT = "Po zgraniu uruchom Sprawdz_Raspberry_Pi.bat."


@dataclass(frozen=True)
class SessionSummary:
    id: int
    started_at: float
    first: float | None
    last: float | None
    measurements: int
    temperatures: int
    pin_changes: int


def format_megabytes(size: int) -> str:
    return f"{format_number(size / 1e6, 1)} MB"


def describe_os_error(exception: OSError) -> str:
    return exception.strerror or str(exception)


def progress(text: str):
    print(f"\r{text}", end="", flush=True)


def clear_progress():
    print("\r" + " " * 60 + "\r", end="", flush=True)


def remove_dump_files():
    for name in DUMP_FILES:
        (DUMP_DIR / name).unlink(missing_ok=True)


def prepare_dump_dir() -> Result | None:
    try:
        DUMP_DIR.mkdir(parents=True, exist_ok=True)
        remove_dump_files()
    except OSError as exception:
        return error(SECTION, f"Nie można przygotować katalogu kopii {DUMP_DIR} ({describe_os_error(exception)}).")
    return None


def file_size(path: Path) -> int:
    try:
        return path.stat().st_size
    except FileNotFoundError:
        return 0


def database_size() -> int:
    database = Path(config.DB_PATH)
    return file_size(database) + file_size(database.with_name(database.name + "-wal"))


def check_space() -> Result:
    needed = database_size() + SPACE_MARGIN_BYTES
    free = system.free_disk_bytes(str(DUMP_DIR / DUMP_NAME))

    if free is None:
        return warning(SECTION, "Nie udało się sprawdzić wolnego miejsca na karcie.")
    if free < needed:
        return error(
            SECTION,
            f"Za mało miejsca na karcie: wolne {format_megabytes(free)}, potrzebne {format_megabytes(needed)}.",
            ("Usługa nie została uruchomiona ponownie, kopia nie powstała.",),
        )

    return ok(SECTION, f"Miejsce na karcie: wolne {format_megabytes(free)}, potrzebne {format_megabytes(needed)}.")


def read_last_session(conn: sqlite3.Connection) -> int:
    return conn.execute("SELECT COALESCE(MAX(id), 0) FROM sessions").fetchone()[0]


def read_writing_session(conn: sqlite3.Connection) -> int | None:
    row = conn.execute("SELECT session_id FROM measurements ORDER BY id DESC LIMIT 1").fetchone()
    return row[0] if row else None


def find_new_session(previous: int) -> int | None:
    try:
        session = system.query_database(read_writing_session)
    except sqlite3.Error:
        return None

    if session is None or session <= previous:
        return None
    return session


def wait_for_new_session(previous: int) -> Result:
    progress("Czekam na wskazania w nowej sesji...")
    deadline = time.monotonic() + NEW_SESSION_TIMEOUT
    session = find_new_session(previous)

    while session is None and time.monotonic() < deadline:
        time.sleep(POLL_INTERVAL)
        session = find_new_session(previous)

    clear_progress()

    if session is None:
        return warning(
            SECTION,
            f"Nowa sesja nie zapisała wskazań w ciągu {format_duration(NEW_SESSION_TIMEOUT)}.",
            (CHECK_HINT,),
        )
    return ok(SECTION, f"Nowa sesja {session} zapisuje wskazania.")


def copy_database(target: Path) -> str:
    source = system.open_database_readonly(config.DB_PATH)
    with contextlib.closing(source), contextlib.closing(sqlite3.connect(target)) as copy:
        source.backup(copy)
        copy.execute("PRAGMA journal_mode=DELETE")
        return copy.execute("PRAGMA integrity_check").fetchone()[0]


def make_copy() -> Result:
    part = DUMP_DIR / PART_NAME
    started = time.monotonic()
    progress("Kopiuję bazę...")

    try:
        integrity = copy_database(part)
        if integrity == "ok":
            dump = part.rename(DUMP_DIR / DUMP_NAME)
    except sqlite3.Error as exception:
        integrity, failure = None, str(exception)
    except OSError as exception:
        integrity, failure = None, describe_os_error(exception)
    finally:
        clear_progress()

    if integrity is None:
        remove_dump_files()
        return error(SECTION, f"Kopia bazy nie powiodła się ({failure}).")

    if integrity != "ok":
        remove_dump_files()
        return error(SECTION, f"Kopia bazy jest uszkodzona (integrity_check: {integrity}).")

    elapsed = format_duration(time.monotonic() - started)
    return ok(SECTION, f"Kopia bazy gotowa: {format_megabytes(dump.stat().st_size)}, w {elapsed}.")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        while chunk := file.read(HASH_CHUNK_BYTES):
            digest.update(chunk)
    return digest.hexdigest()


def write_checksum() -> Result:
    try:
        checksum = sha256(DUMP_DIR / DUMP_NAME)
        (DUMP_DIR / CHECKSUM_NAME).write_text(checksum + "\n")
    except OSError as exception:
        remove_dump_files()
        return error(SECTION, f"Nie udało się zapisać sumy SHA-256 kopii ({describe_os_error(exception)}).")

    return ok(SECTION, f"SHA-256: {checksum}")


def read_session_summary(conn: sqlite3.Connection, session_id: int) -> SessionSummary:
    started_at = conn.execute("SELECT started_at FROM sessions WHERE id = ?", (session_id,)).fetchone()[0]
    first, last, measurements = conn.execute(
        "SELECT MIN(timestamp), MAX(timestamp), COUNT(*) FROM measurements WHERE session_id = ?",
        (session_id,),
    ).fetchone()
    temperatures = conn.execute(
        "SELECT COUNT(*) FROM temperature_measurements WHERE session_id = ?",
        (session_id,),
    ).fetchone()[0]
    pin_changes = conn.execute("SELECT COUNT(*) FROM pin_states WHERE session_id = ?", (session_id,)).fetchone()[0]

    return SessionSummary(session_id, started_at, first, last, measurements, temperatures, pin_changes)


def read_sessions_after(conn: sqlite3.Connection, previous: int) -> list[int]:
    return [row[0] for row in conn.execute("SELECT id FROM sessions WHERE id > ? ORDER BY id", (previous,))]


def describe_counts(summary: SessionSummary) -> str:
    measurements = summary.measurements
    temperatures = summary.temperatures
    pin_changes = summary.pin_changes
    return (
        f"{measurements} {plural(measurements, 'wskazanie', 'wskazania', 'wskazań')}, "
        f"{temperatures} {plural(temperatures, 'odczyt', 'odczyty', 'odczytów')} temperatury, "
        f"{pin_changes} {plural(pin_changes, 'zmiana', 'zmiany', 'zmian')} sygnałów"
    )


def describe_span(summary: SessionSummary) -> str:
    if summary.first is None:
        return f"od {format_time(summary.started_at)}, bez wskazań"
    return f"wskazania {format_time(summary.first)} – {format_time(summary.last)}"


def summarize_sessions(previous: int) -> list[Result]:
    try:
        with contextlib.closing(system.open_database_readonly(DUMP_DIR / DUMP_NAME)) as conn:
            newer = read_sessions_after(conn, previous)
            old = read_session_summary(conn, previous) if previous else None
            new = read_session_summary(conn, newer[0]) if newer else None
    except sqlite3.Error as exception:
        return [warning(SECTION, f"Nie udało się odczytać sesji z kopii ({exception}).")]

    results = []

    if old is not None:
        state = "zakończona zgraniem" if new is not None else "trwa dalej (bez restartu)"
        results.append(info(SECTION, f"Sesja {old.id} {state}: {describe_span(old)}, {describe_counts(old)}."))

    if new is not None:
        results.append(info(SECTION, f"Nowa sesja {new.id}: od {format_time(new.started_at)}."))

    return results


def emit(results: list[Result], result: Result) -> Result:
    print(format_line(result))
    results.append(result)
    return result


def dump_ready() -> bool:
    return (DUMP_DIR / DUMP_NAME).exists() and (DUMP_DIR / CHECKSUM_NAME).exists()


def finish(results: list[Result]) -> int:
    print()

    if not dump_ready():
        print("Baza nie została zgrana.")
        return EXIT_NO_DUMP

    print(f"Kopia do pobrania: {DUMP_DIR / DUMP_NAME}")
    if any(result.is_problem for result in results):
        print(CHECK_HINT)
        return EXIT_PROBLEMS
    return EXIT_OK


def dump() -> int:
    results: list[Result] = []
    print(SECTION)

    failure = prepare_dump_dir()
    if failure is not None:
        emit(results, failure)
        return finish(results)

    if emit(results, check_space()).level is Level.ERROR:
        return finish(results)

    try:
        previous = system.query_database(read_last_session)
    except sqlite3.Error as exception:
        emit(results, error(SECTION, f"Nie można odczytać bazy danych ({config.DB_PATH}): {exception}."))
        return finish(results)

    if emit(results, service_restart.restart(SECTION)).level is Level.OK:
        emit(results, wait_for_new_session(previous))

    if emit(results, make_copy()).level is Level.ERROR:
        return finish(results)

    if emit(results, write_checksum()).level is Level.ERROR:
        return finish(results)

    for result in summarize_sessions(previous):
        emit(results, result)

    return finish(results)


def cleanup() -> int:
    try:
        remove_dump_files()
    except OSError as exception:
        print(format_line(error(SECTION, f"Nie udało się usunąć kopii bazy ({describe_os_error(exception)}).")))
        return EXIT_NO_DUMP

    print(format_line(ok(SECTION, "Kopia bazy usunięta z Raspberry Pi.")))
    return EXIT_OK


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="python -m src.tools.dump")
    parser.add_argument("--cleanup", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    return cleanup() if args.cleanup else dump()


if __name__ == "__main__":
    sys.exit(main())
