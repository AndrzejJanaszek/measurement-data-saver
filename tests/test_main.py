import subprocess
import sys
import time

import pytest

from src import main as main_module
from src import worker_names


def test_sigterm_handler_raises_keyboard_interrupt():
    """
    Jednostkowa weryfikacja samego handlera: SIGTERM musi zostać przełożone
    na KeyboardInterrupt, żeby main() wpadło do tej samej, już przetestowanej
    ścieżki czystego zamknięcia co Ctrl+C.
    """
    with pytest.raises(KeyboardInterrupt):
        main_module._handle_sigterm(signum=15, frame=None)


def test_worker_names_used_consistently_in_main():
    """
    main.py buduje słownik wątków kluczowany nazwami z worker_names, a
    WORKER_NAMES (używane do sprawdzania heartbeatów) powinno zawierać
    dokładnie te same nazwy - inaczej heartbeat.beat("miernik") wewnątrz
    workera nigdy nie dopasowałby się do tego, czego szuka main.py.
    """
    assert set(main_module.WORKER_NAMES) == {worker_names.METER, worker_names.ARDUINO}


def test_main_shuts_down_gracefully_on_sigterm():
    """
    Test end-to-end na prawdziwym procesie: wysyła SIGTERM (dokładnie to,
    czego używa `systemctl stop`/`restart` - w tym install.sh przy każdym
    wdrożeniu) i sprawdza, że main.py loguje czyste zamknięcie zamiast
    ginąć w ciszy. Bez portów fizycznych workery będą w pętli prób
    reconnectu - to nie przeszkadza w weryfikacji samej obsługi sygnału.
    """
    proc = subprocess.Popen(
        [sys.executable, "-m", "src.main"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    try:
        time.sleep(1.0)  # dać procesowi czas na start i instalację handlera SIGTERM
        proc.terminate()  # subprocess.terminate() == SIGTERM na Linuksie
        try:
            stdout, _ = proc.communicate(timeout=5.0)
        except subprocess.TimeoutExpired:
            proc.kill()
            stdout, _ = proc.communicate()
            pytest.fail(
                "Proces NIE zakończył się w 5s po SIGTERM - blok finally "
                f"prawdopodobnie nie zostal wykonany. Wyjście:\n{stdout}"
            )
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()

    assert proc.returncode == 0, f"Oczekiwano kodu 0 po czystym zamknięciu, dostano {proc.returncode}"
    assert "bezpiecznie zatrzymana" in stdout, f"Brak logu czystego zamknięcia w wyjściu:\n{stdout}"