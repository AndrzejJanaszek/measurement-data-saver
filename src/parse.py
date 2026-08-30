import json
import logging
import re


def parse_raw_frame(frame_bytes: bytes) -> float | None:
    """
    Przyjmuje surowe bajty ramki (bez znaków START i END) z MIERNIKA.
    Dekoduje do stringa i wyciąga z niego pierwszą znalezioną liczbę float.
    Zwraca float lub None w przypadku błędu/braku danych.
    """
    if not frame_bytes:
        return None

    try:
        text_data = frame_bytes.decode('utf-8', errors='ignore').strip()

        match = re.search(r"[-+]?\d*\.\d+|\d+", text_data)

        if match:
            return float(match.group())

        return None

    except Exception:
        return None


def parse_arduino_frame(frame_bytes: bytes):
    """
    Przyjmuje surowe bajty pojedynczej linii JSON z ARDUINO i rozpoznaje jej typ
    na podstawie obecności klucza "temps" albo "pins".

    Arduino wysyła asynchronicznie dwa różne rodzaje ramek, np.:
      {"temps":[{"a":"28A8F126AB240B1C","t":25.44}, ...]}
      {"pins":{"4":1,"5":1}}

    Zwraca:
      ("temps", [(sensor_address: str, value: float), ...])
      ("pins", {pin: int -> value: int, ...})
      None - jeśli ramka jest pusta, niepoprawnym/niepełnym JSON-em
             (np. urwana przy odpięciu kabla), albo nie pasuje do
             żadnego znanego formatu. Błąd jest logowany, ramka odrzucana,
             nigdy nie wywala wyjątkiem wywołującego wątku.
    """
    if not frame_bytes:
        return None

    try:
        text_data = frame_bytes.decode('utf-8', errors='ignore').strip()
    except Exception as e:
        logging.warning(f"[arduino] Nie udało się zdekodować ramki jako UTF-8: {e}")
        return None

    if not text_data:
        return None

    # Heurystyka: jeśli linia nie zaczyna się od '{', to prawie na pewno nie
    # miała być JSON-em - to log diagnostyczny/banner startowy z Arduino
    # (np. "Locating devices...", "Found device 0 with address: ..."),
    # a nie uszkodzona ramka danych. Takie linie są normalnym szumem
    # (zwłaszcza zaraz po starcie/resecie Arduino) i logujemy je cicho,
    # na poziomie DEBUG, żeby nie zaśmiecać logów usługi fałszywymi
    # ostrzeżeniami. Jeśli linia NACZYNA się od '{', ale mimo to nie da się
    # sparsować - to już realny sygnał problemu (np. transmisja urwana
    # w połowie ramki) i zostaje na poziomie WARNING.
    if not text_data.startswith("{"):
        logging.debug(f"[arduino] Zignorowano linię niebędącą JSON-em (log/diagnostyka Arduino): {text_data!r}")
        return None

    try:
        data = json.loads(text_data)
    except json.JSONDecodeError as e:
        logging.warning(f"[arduino] Odrzucono niepoprawny/niepełny JSON: {e} | raw={text_data!r}")
        return None

    if not isinstance(data, dict):
        logging.warning(f"[arduino] Oczekiwano obiektu JSON, otrzymano {type(data).__name__}: {text_data!r}")
        return None

    if "temps" in data:
        return _parse_temps(data["temps"], text_data)

    if "pins" in data:
        return _parse_pins(data["pins"], text_data)

    logging.warning(f"[arduino] Nierozpoznany format ramki (brak klucza 'temps'/'pins'): {text_data!r}")
    return None


def _parse_temps(temps_field, raw_text: str):
    if not isinstance(temps_field, list):
        logging.warning(f"[arduino] Pole 'temps' nie jest listą: {raw_text!r}")
        return None

    readings = []
    for entry in temps_field:
        try:
            address = str(entry["a"])
            value = float(entry["t"])
            readings.append((address, value))
        except (KeyError, TypeError, ValueError) as e:
            logging.warning(f"[arduino] Pominięto błędny wpis temperatury {entry!r}: {e}")
            continue

    if not readings:
        logging.warning(f"[arduino] Ramka 'temps' nie zawierała żadnych poprawnych odczytów: {raw_text!r}")
        return None

    return ("temps", readings)


def _parse_pins(pins_field, raw_text: str):
    if not isinstance(pins_field, dict):
        logging.warning(f"[arduino] Pole 'pins' nie jest obiektem: {raw_text!r}")
        return None

    states = {}
    for pin_key, value in pins_field.items():
        try:
            pin = int(pin_key)
            states[pin] = int(value)
        except (TypeError, ValueError) as e:
            logging.warning(f"[arduino] Pominięto błędny wpis pinu {pin_key!r}={value!r}: {e}")
            continue

    if not states:
        logging.warning(f"[arduino] Ramka 'pins' nie zawierała żadnych poprawnych wpisów: {raw_text!r}")
        return None

    return ("pins", states)