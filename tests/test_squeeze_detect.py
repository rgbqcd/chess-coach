from src.lib.squeeze_detect import SqueezeDetector

HZ = 50
DT = 1.0 / HZ


def feed(detector, segments, t0=0.0):
    """segments: list of (pressure, duration_s). Returns emitted events."""
    events = []
    t = t0
    for pressure, duration in segments:
        for _ in range(int(duration * HZ)):
            ev = detector.process(pressure, t)
            if ev:
                events.append(ev)
            t += DT
    return events


def calibrated_detector(**kwargs):
    d = SqueezeDetector(**kwargs)
    d.set_calibration(baseline=100, peak=700)  # on at 310, off at 220
    d.process(100, -1.0)  # seed baseline sample
    return d


def test_short_squeeze():
    d = calibrated_detector()
    events = feed(d, [(100, 1.0), (600, 0.2), (100, 1.0)])
    assert len(events) == 1
    assert events[0].kind == "short"
    assert events[0].seq == 1


def test_long_squeeze():
    d = calibrated_detector()
    events = feed(d, [(100, 1.0), (600, 0.7), (100, 1.0)])
    assert [e.kind for e in events] == ["long"]


def test_bounce_ignored():
    d = calibrated_detector()
    events = feed(d, [(100, 1.0), (600, 0.04), (100, 1.0)])
    assert events == []


def test_boundary_is_long():
    d = calibrated_detector(long_press_ms=500)
    # hold ~520ms: comfortably past the boundary
    events = feed(d, [(100, 1.0), (600, 0.52), (100, 1.0)])
    assert [e.kind for e in events] == ["long"]


def test_multiple_squeezes_sequence_numbers():
    d = calibrated_detector()
    segments = []
    for _ in range(3):
        segments += [(100, 0.5), (600, 0.2)]
    segments.append((100, 0.5))
    events = feed(d, segments)
    assert [e.seq for e in events] == [1, 2, 3]
    assert all(e.kind == "short" for e in events)


def test_hysteresis_no_double_trigger():
    d = calibrated_detector()
    # dips to 250 stay above the off threshold (220): still one squeeze
    events = feed(d, [(100, 1.0), (600, 0.1), (250, 0.1), (600, 0.1), (100, 1.0)])
    assert len(events) == 1


def test_baseline_drift_tracked():
    d = calibrated_detector()
    feed(d, [(180, 60.0)])  # a minute of slightly elevated resting pressure
    assert d.baseline > 150
    # a squeeze from the new baseline still detects
    events = feed(d, [(600, 0.2), (180, 1.0)], t0=100.0)
    assert len(events) == 1


def test_peak_recorded():
    d = calibrated_detector()
    events = feed(d, [(100, 1.0), (500, 0.1), (650, 0.1), (100, 1.0)])
    assert events[0].peak == 650
