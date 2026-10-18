import subprocess

import pytest

from src.tools import set_time, system

ACTIVE = "ActiveState=active\nUnitFileState=enabled\nMainPID=42\nNRestarts=0\nActiveEnterTimestamp=@1792319840\n"
FAILED = "ActiveState=failed\nUnitFileState=enabled\nMainPID=0\nNRestarts=0\nActiveEnterTimestamp=\n"


class FakeRun:
    def __init__(self):
        self.calls = []
        self.restart_status = 0
        self.restart_stderr = ""
        self.show_output = ACTIVE

    def __call__(self, args, **kwargs):
        self.calls.append(args)
        if args[:2] == ["systemctl", "show"]:
            return subprocess.CompletedProcess(args, 0, self.show_output, "")
        return subprocess.CompletedProcess(args, self.restart_status, "", self.restart_stderr)


@pytest.fixture(autouse=True)
def no_color_no_sleep(monkeypatch):
    monkeypatch.setenv("NO_COLOR", "1")
    monkeypatch.setattr(set_time.time, "sleep", lambda _: None)


@pytest.fixture
def fake_run(monkeypatch):
    fake = FakeRun()
    monkeypatch.setattr(system.subprocess, "run", fake)
    return fake


def run_main(capsys, date_status: int = 0, date_error: str = "") -> tuple[int, str]:
    code = set_time.main([
        "--requested", "1792319840",
        "--before", "1773320531",
        "--date-status", str(date_status),
        f"--date-error={date_error}",
    ])
    return code, capsys.readouterr().out


def test_date_permission_failure_skips_restart(fake_run, capsys):
    code, out = run_main(capsys, date_status=1, date_error="sudo: a password is required\n")

    assert code == 1
    assert "[BŁĄD]   Brak uprawnień do zmiany czasu." in out
    assert "deploy/install-tools.sh" in out
    assert fake_run.calls == []


def test_other_date_failure_shows_date_error(fake_run, capsys):
    code, out = run_main(capsys, date_status=1, date_error="date: cannot set date: Invalid argument\n")

    assert code == 1
    assert "Nie udało się ustawić czasu (date: cannot set date: Invalid argument)." in out
    assert "install-tools" not in out
    assert fake_run.calls == []


def test_date_failure_without_message_shows_exit_code(fake_run, capsys):
    code, out = run_main(capsys, date_status=3)

    assert code == 1
    assert "Nie udało się ustawić czasu (kod wyjścia 3)." in out


def test_success_restarts_service_with_sudoers_command(fake_run, capsys):
    code, out = run_main(capsys)

    assert code == 0
    assert ["sudo", "-n", "systemctl", "restart", "measurement-data-saver.service"] in fake_run.calls
    assert "Czas ustawiony: 2026-10-18" in out
    assert "różnica: 7 mies." in out
    assert "Usługa zapisu danych uruchomiona ponownie." in out
    assert "Teraz można uruchomić Sprawdz_Raspberry_Pi.bat." in out


def test_restart_permission_failure(fake_run, capsys):
    fake_run.restart_status = 1
    fake_run.restart_stderr = "sudo: a password is required\n"

    code, out = run_main(capsys)

    assert code == 1
    assert "Brak uprawnień do ponownego uruchomienia usługi." in out
    assert "Teraz można" not in out


def test_service_not_active_after_restart(fake_run, capsys):
    fake_run.show_output = FAILED

    code, out = run_main(capsys)

    assert code == 1
    assert "Usługa nie uruchomiła się ponownie (stan: failed)." in out


def test_failed_restart_without_sudo_error_reports_service_state(fake_run, capsys):
    fake_run.restart_status = 1
    fake_run.restart_stderr = "Job for measurement-data-saver.service failed because the control process exited with error code.\n"
    fake_run.show_output = FAILED

    code, out = run_main(capsys)

    assert code == 1
    assert "Usługa nie uruchomiła się ponownie (stan: failed)." in out
    assert "install-tools" not in out


def test_service_state_reads_unix_timestamp(fake_run):
    state = system.read_service()

    assert state.is_active
    assert state.active_since == 1792319840.0
    assert fake_run.calls[0][:4] == ["systemctl", "show", system.SERVICE, "--timestamp=unix"]
