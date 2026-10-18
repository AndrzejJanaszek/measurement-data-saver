import glob
import shutil
import socket
import subprocess
from dataclasses import dataclass
from pathlib import Path

SERVICE = "measurement-data-saver.service"
SERVICE_PROPERTIES = ("ActiveState", "UnitFileState", "MainPID", "NRestarts", "ActiveEnterTimestamp")


@dataclass(frozen=True)
class ServiceState:
    active_state: str
    unit_file_state: str
    main_pid: str
    restarts: str
    active_since: float | None

    @property
    def is_active(self) -> bool:
        return self.active_state == "active"


def run(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(args, capture_output=True, text=True)


def read_service() -> ServiceState:
    result = run(["systemctl", "show", SERVICE, "--timestamp=unix", "-p", ",".join(SERVICE_PROPERTIES)])
    values = dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)

    return ServiceState(
        active_state=values.get("ActiveState", "unknown"),
        unit_file_state=values.get("UnitFileState", "unknown"),
        main_pid=values.get("MainPID", ""),
        restarts=values.get("NRestarts", ""),
        active_since=parse_unix_timestamp(values.get("ActiveEnterTimestamp", "")),
    )


def parse_unix_timestamp(value: str) -> float | None:
    if not value.startswith("@"):
        return None
    try:
        return float(value[1:])
    except ValueError:
        return None


def restart_service() -> subprocess.CompletedProcess:
    return run(["sudo", "-n", "systemctl", "restart", SERVICE])


def read_journal(since: float) -> list[str]:
    result = run(["journalctl", "-u", SERVICE, "--since", f"@{int(since)}", "-o", "cat", "-q", "--no-pager"])
    return result.stdout.splitlines()


def journal_readable() -> bool:
    result = run(["journalctl", "-u", SERVICE, "-n", "1", "-o", "cat", "-q", "--no-pager"])
    return bool(result.stdout.strip())


def port_exists(port: str) -> bool:
    return Path(port).exists()


def other_ports(port: str) -> list[str]:
    pattern = port.rstrip("0123456789") + "*"
    return sorted(path for path in glob.glob(pattern) if path != port)


def free_disk_bytes(path: str) -> int | None:
    try:
        return shutil.disk_usage(Path(path).resolve().parent).free
    except OSError:
        return None


def hostname() -> str:
    return socket.gethostname()
