import time

from src.tools import system
from src.tools.report import INSTALL_TOOLS_HINT, Result, error, ok

RESTART_TIMEOUT = 10.0
POLL_INTERVAL = 0.5


def wait_until_active(timeout: float) -> system.ServiceState:
    deadline = time.monotonic() + timeout
    state = system.read_service()

    while not state.is_active and time.monotonic() < deadline:
        time.sleep(POLL_INTERVAL)
        state = system.read_service()

    return state


def restart(section: str) -> Result:
    result = system.restart_service()
    if result.returncode != 0 and result.stderr.startswith(system.SUDO_ERROR_PREFIXES):
        return error(section, "Brak uprawnień do ponownego uruchomienia usługi.", (INSTALL_TOOLS_HINT,))

    state = wait_until_active(RESTART_TIMEOUT)
    if not state.is_active:
        return error(section, f"Usługa nie uruchomiła się ponownie (stan: {state.active_state}).")

    return ok(section, "Usługa zapisu danych uruchomiona ponownie.")
