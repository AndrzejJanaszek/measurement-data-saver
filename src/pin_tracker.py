class PinStateTracker:
    """
    Śledzi ostatnio znany stan pinów, żeby dało się zapisywać do bazy
    tylko te wartości, które faktycznie się zmieniły.

    Rozwiązanie celowo nie zależy od konkretnej liczby ani numeracji pinów -
    Arduino zawsze wysyła pełny snapshot wszystkich monitorowanych pinów,
    a tracker sam wykrywa, które z nich są nowe.

    Po restarcie usługi stan wewnętrzny jest pusty, więc pierwszy odebrany
    batch zawsze zostanie w całości potraktowany jako "zmiana" i zapisany -
    to zamierzone zachowanie (świeży snapshot stanu na start sesji).
    """

    def __init__(self):
        self._last_known: dict[int, int] = {}

    def get_changes(self, new_states: dict[int, int]) -> dict[int, int]:
        """
        Przyjmuje pełny snapshot stanów pinów {pin: value}.
        Zwraca tylko te pary, których wartość jest nowa albo różni się
        od poprzednio zapamiętanej. Aktualizuje stan wewnętrzny o WSZYSTKIE
        przekazane piny (nie tylko zmienione), żeby kolejne porównania
        były poprawne.
        """
        changes: dict[int, int] = {}
        for pin, value in new_states.items():
            if self._last_known.get(pin) != value:
                changes[pin] = value

        self._last_known.update(new_states)
        return changes

    def known_pins(self) -> dict[int, int]:
        """Zwraca kopię aktualnie znanego stanu (przydatne do logów/testów)."""
        return dict(self._last_known)