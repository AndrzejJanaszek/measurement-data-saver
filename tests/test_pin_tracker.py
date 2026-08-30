from src.pin_tracker import PinStateTracker


def test_first_batch_is_entirely_reported_as_changed():
    tracker = PinStateTracker()
    changes = tracker.get_changes({4: 1, 5: 0})
    assert changes == {4: 1, 5: 0}


def test_identical_batch_reports_no_changes():
    tracker = PinStateTracker()
    tracker.get_changes({4: 1, 5: 0})
    changes = tracker.get_changes({4: 1, 5: 0})
    assert changes == {}


def test_single_pin_change_detected():
    tracker = PinStateTracker()
    tracker.get_changes({4: 1, 5: 0})
    changes = tracker.get_changes({4: 0, 5: 0})
    assert changes == {4: 0}


def test_new_pin_appearing_later_counts_as_change():
    tracker = PinStateTracker()
    tracker.get_changes({4: 1})
    changes = tracker.get_changes({4: 1, 6: 1})
    assert changes == {6: 1}


def test_multiple_simultaneous_changes():
    tracker = PinStateTracker()
    tracker.get_changes({4: 0, 5: 0, 6: 0})
    changes = tracker.get_changes({4: 1, 5: 1, 6: 0})
    assert changes == {4: 1, 5: 1}


def test_known_pins_reflects_full_internal_state():
    tracker = PinStateTracker()
    tracker.get_changes({4: 1, 5: 0})
    tracker.get_changes({4: 1, 5: 1, 6: 1})
    assert tracker.known_pins() == {4: 1, 5: 1, 6: 1}


def test_known_pins_returns_a_copy_not_internal_reference():
    tracker = PinStateTracker()
    tracker.get_changes({4: 1})
    snapshot = tracker.known_pins()
    snapshot[4] = 999  # mutacja kopii nie powinna wpłynąć na tracker
    assert tracker.known_pins() == {4: 1}


def test_empty_batch_produces_no_changes():
    tracker = PinStateTracker()
    tracker.get_changes({4: 1})
    changes = tracker.get_changes({})
    assert changes == {}
