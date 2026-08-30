from src import worker_names


def test_meter_and_arduino_names_are_distinct():
    assert worker_names.METER != worker_names.ARDUINO


def test_all_contains_both_names_exactly_once():
    assert sorted(worker_names.ALL) == sorted([worker_names.METER, worker_names.ARDUINO])
    assert len(worker_names.ALL) == len(set(worker_names.ALL))
