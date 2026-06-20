import pytest
from src.parse import parse_raw_frame

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