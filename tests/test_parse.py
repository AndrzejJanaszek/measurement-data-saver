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

def test_parse_empty_or_corrupted():
    # Puste dane lub same śmieci
    assert parse_raw_frame(b"") is None
    assert parse_raw_frame(b"ERROR_NO_DATA") is None

def test_two_numbers():
    assert parse_raw_frame(b"1 42000") == 1