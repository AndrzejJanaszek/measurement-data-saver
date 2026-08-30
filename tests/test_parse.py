import logging

import pytest

from src.parse import parse_raw_frame, parse_arduino_frame


# =============================================================================
# MIERNIK (parse_raw_frame) - bez zmian względem oryginalnej wersji
# =============================================================================

def test_parse_clean_float():
    # Przypadek idealny: tylko liczba
    assert parse_raw_frame(b"23.45") == 23.45

def test_parse_integer():
    # Liczba całkowita powinna być skonwertowana na float
    assert parse_raw_frame(b"42") == 42.0

def test_parse_with_text_prefix():
    assert parse_raw_frame(b"N     100.5") == 100.5
    assert parse_raw_frame(b"SD      -12.3") == -12.3

def test_two_numbers():
    assert parse_raw_frame(b"1 42000") == 1

def test_parse_valid_weight_frame():
    # Testujemy dokładnie taki format, jaki przysłała Twoja waga
    assert parse_raw_frame(b'ST,      66,kg') == 66.0
    assert parse_raw_frame(b'ST,     123.45,kg') == 123.45

def test_parse_empty_or_corrupted():
    assert parse_raw_frame(b'') is None
    assert parse_raw_frame(b'ST, brak_danych,kg') is None


# =============================================================================
# ARDUINO (parse_arduino_frame) - nowe testy
# =============================================================================

def test_parse_arduino_temps_single_sensor():
    frame = b'{"temps":[{"a":"28A8F126AB240B1C","t":25.44}]}'
    result = parse_arduino_frame(frame)
    assert result == ("temps", [("28A8F126AB240B1C", 25.44)])


def test_parse_arduino_temps_multiple_sensors():
    frame = (
        b'{"temps":[{"a":"28A8F126AB240B1C","t":25.44},'
        b'{"a":"28989298AA240B81","t":25.44},'
        b'{"a":"287C36E9AB240BD4","t":25.06}]}'
    )
    kind, readings = parse_arduino_frame(frame)
    assert kind == "temps"
    assert readings == [
        ("28A8F126AB240B1C", 25.44),
        ("28989298AA240B81", 25.44),
        ("287C36E9AB240BD4", 25.06),
    ]


def test_parse_arduino_pins_basic():
    frame = b'{"pins":{"4":1,"5":1}}'
    result = parse_arduino_frame(frame)
    assert result == ("pins", {4: 1, 5: 1})


def test_parse_arduino_pins_string_values():
    # Arduino czasem wysyła wartość pinu jako string liczbowy zamiast liczby -
    # powinno zostać poprawnie skonwertowane.
    frame = b'{"pins":{"4":"0","5":"1"}}'
    result = parse_arduino_frame(frame)
    assert result == ("pins", {4: 0, 5: 1})


def test_parse_arduino_empty_frame():
    assert parse_arduino_frame(b'') is None
    assert parse_arduino_frame(b'   ') is None


def test_parse_arduino_malformed_json_logs_warning(caplog):
    # Realnie urwana ramka (np. odpięcie kabla w trakcie transmisji) -
    # zaczyna się od '{', więc to NIE jest banner diagnostyczny, tylko
    # prawdziwy błąd protokołu -> powinien zostać zalogowany jako WARNING.
    frame = b'{"temps":[{"a":"28A8F126AB240B1C","t":25.4'
    with caplog.at_level(logging.WARNING):
        result = parse_arduino_frame(frame)
    assert result is None
    assert any("niepoprawny/niepełny JSON" in msg for msg in caplog.messages)


def test_parse_arduino_non_dict_json():
    # Poprawny JSON, ale nie jest obiektem (np. sama liczba albo lista)
    assert parse_arduino_frame(b'42') is None
    assert parse_arduino_frame(b'[1, 2, 3]') is None


def test_parse_arduino_unknown_key():
    frame = b'{"foo": "bar"}'
    assert parse_arduino_frame(frame) is None


def test_parse_arduino_temps_skips_bad_entries_but_keeps_good_ones():
    frame = b'{"temps":[{"a":"AAA","t":21.0},{"a":"BBB","t":"not_a_number"}]}'
    result = parse_arduino_frame(frame)
    assert result == ("temps", [("AAA", 21.0)])


def test_parse_arduino_temps_all_entries_bad_returns_none():
    frame = b'{"temps":[{"a":"AAA","t":"not_a_number"}]}'
    assert parse_arduino_frame(frame) is None


def test_parse_arduino_temps_not_a_list():
    frame = b'{"temps": "oops"}'
    assert parse_arduino_frame(frame) is None


def test_parse_arduino_pins_not_a_dict():
    frame = b'{"pins": [1, 2, 3]}'
    assert parse_arduino_frame(frame) is None


def test_parse_arduino_pins_skips_bad_entries():
    frame = b'{"pins":{"4":1,"oops":"not_a_number_at_all_xyz"}}'
    result = parse_arduino_frame(frame)
    assert result == ("pins", {4: 1})


# --- Heurystyka rozróżniająca banner diagnostyczny Arduino od zepsutej ramki ---
# Arduino po starcie/resecie wysyła linie logów (np. "Locating devices...",
# "Found device 0 with address: ...", "io pin (INPUT_PULLUP) - on pin: 4").
# Te linie NIE zaczynają się od '{', więc powinny być cicho zignorowane
# (poziom DEBUG), a NIE zgłaszane jako WARNING - inaczej logi usługi byłyby
# zaśmiecone fałszywymi alarmami przy każdym starcie/resecie Arduino.

@pytest.mark.parametrize("banner_line", [
    b'Locating devices...Found 4 devices.',
    b'Found device 0 with address: 28A8F126AB240B1C',
    b'io pin (INPUT_PULLUP) - on pin: 4',
    b'[DEBUG] Found device 0 with address: 28A8F126AB240B1C',
    b'[INFO] Locating devices...',
])
def test_parse_arduino_ignores_diagnostic_banners_quietly(banner_line, caplog):
    with caplog.at_level(logging.DEBUG):
        result = parse_arduino_frame(banner_line)
    assert result is None
    # Kluczowe: żaden z tych zapisów NIE powinien wylądować jako WARNING.
    warning_records = [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert warning_records == []


def test_parse_arduino_garbage_not_json_at_all():
    result = parse_arduino_frame(b'garbage not json at all')
    assert result is None
