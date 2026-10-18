import argparse
import sys
import time

from src.tools import system
from src.tools.report import INSTALL_TOOLS_HINT, Level, Result, format_duration, format_line, format_time

RESTART_TIMEOUT = 10.0
POLL_INTERVAL = 0.5
SECTION = "Czas"


def wait_until_active(timeout: float) -> system.ServiceState:
    deadline = time.monotonic() + timeout
    state = system.read_service()

    while not state.is_active and time.monotonic() < deadline:
        time.sleep(POLL_INTERVAL)
        state = system.read_service()

    return state


def time_set(requested: float, before: float) -> Result:
    text = (
        f"Czas ustawiony: {format_time(requested)} "
        f"(był: {format_time(before)}, różnica: {format_duration(requested - before)})."
    )
    return Result(SECTION, Level.OK, text)


def restart() -> Result:
    result = system.restart_service()
    if result.returncode != 0 and result.stderr.startswith("sudo:"):
        return Result(SECTION, Level.ERROR, "Brak uprawnień do ponownego uruchomienia usługi.", (INSTALL_TOOLS_HINT,))

    state = wait_until_active(RESTART_TIMEOUT)
    if not state.is_active:
        return Result(SECTION, Level.ERROR, f"Usługa nie uruchomiła się ponownie (stan: {state.active_state}).")

    return Result(SECTION, Level.OK, "Usługa zapisu danych uruchomiona ponownie.")


def date_failed(date_status: int, date_error: str) -> Result:
    if date_error.startswith("sudo:"):
        return Result(SECTION, Level.ERROR, "Brak uprawnień do zmiany czasu.", (INSTALL_TOOLS_HINT,))

    reason = " ".join(date_error.split()) or f"kod wyjścia {date_status}"
    return Result(SECTION, Level.ERROR, f"Nie udało się ustawić czasu ({reason}).")


def run(requested: float, before: float, date_status: int, date_error: str = "") -> list[Result]:
    if date_status != 0:
        return [date_failed(date_status, date_error)]

    return [time_set(requested, before), restart()]


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="python -m src.tools.set_time")
    parser.add_argument("--requested", type=float, required=True)
    parser.add_argument("--before", type=float, required=True)
    parser.add_argument("--date-status", type=int, required=True)
    parser.add_argument("--date-error", default="")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    results = run(args.requested, args.before, args.date_status, args.date_error)

    for result in results:
        print(format_line(result))

    if any(result.is_problem for result in results):
        return 1

    print()
    print("Teraz można uruchomić Sprawdz_Raspberry_Pi.bat.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
