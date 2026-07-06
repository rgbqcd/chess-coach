"""Squeeze detection from a kGoal Boost pressure stream.

Pressure samples (0-2000, normalized) arrive from BLE notifications. The
detector classifies squeezes with hysteresis thresholds derived from a
calibrated (baseline, peak) span, debounces sub-threshold blips, and labels
each squeeze short or long by hold duration. Events are emitted on release.
"""

from __future__ import annotations

from dataclasses import dataclass

DEFAULT_SPAN = 400.0  # assumed squeeze span before calibration


@dataclass(frozen=True)
class SqueezeEvent:
    seq: int
    kind: str  # "short" | "long"
    t_start: float
    t_end: float
    peak: float

    def to_dict(self) -> dict:
        return {
            "seq": self.seq,
            "kind": self.kind,
            "t_start": self.t_start,
            "t_end": self.t_end,
            "duration_ms": round((self.t_end - self.t_start) * 1000),
            "peak": self.peak,
        }


class SqueezeDetector:
    def __init__(
        self,
        long_press_ms: int = 1000,
        min_press_ms: int = 80,
        on_fraction: float = 0.35,
        off_fraction: float = 0.20,
        ema_alpha: float = 0.02,
    ):
        self.long_press_ms = long_press_ms
        self.min_press_ms = min_press_ms
        self.on_fraction = on_fraction
        self.off_fraction = off_fraction
        self.ema_alpha = ema_alpha

        self.baseline: float | None = None  # EMA-tracked resting pressure
        self.span = DEFAULT_SPAN
        self.calibrated = False

        self.pressed = False
        self.seq = 0
        self._t_press = 0.0
        self._peak = 0.0

    def set_calibration(self, baseline: float, peak: float) -> None:
        self.baseline = float(baseline)
        self.span = max(1.0, float(peak) - float(baseline))
        self.calibrated = True

    @property
    def on_threshold(self) -> float:
        return (self.baseline or 0.0) + self.on_fraction * self.span

    @property
    def off_threshold(self) -> float:
        return (self.baseline or 0.0) + self.off_fraction * self.span

    def process(self, pressure: float, t: float) -> SqueezeEvent | None:
        """Feed one sample; returns a SqueezeEvent when a squeeze completes."""
        if self.baseline is None:
            self.baseline = pressure
            return None

        if not self.pressed:
            if pressure > self.on_threshold:
                self.pressed = True
                self._t_press = t
                self._peak = pressure
            else:
                # track slow baseline drift only while released
                self.baseline += self.ema_alpha * (pressure - self.baseline)
            return None

        self._peak = max(self._peak, pressure)
        if pressure < self.off_threshold:
            self.pressed = False
            duration_ms = (t - self._t_press) * 1000
            if duration_ms < self.min_press_ms:
                return None  # bounce
            self.seq += 1
            return SqueezeEvent(
                seq=self.seq,
                kind="long" if duration_ms >= self.long_press_ms else "short",
                t_start=self._t_press,
                t_end=t,
                peak=self._peak,
            )
        return None
