"""Heart rate for MagicDodge — the wrist PPG input the report describes.

A real run wears a wrist photoplethysmography watch. This class accepts real
samples through push() when a device feeds them, and otherwise simulates a
plausible trace driven by how intense the game currently is, so the whole
pipeline (record, upload, coach) is complete and demonstrable without hardware.

Every sample is tagged with the wave and the game state, so the summary can line
the effort up with the game timeline the way the report's time synchronisation
intends. The summary is an EFFORT signal for the coach, never a medical reading:
coach.py is instructed to describe effort and progress only, never health.

No pygame here, so it is testable without a window.
"""

from __future__ import annotations

import math
import random

RESTING_BPM = 72.0                 # seated baseline for the simulation
MAX_BPM = 178.0                    # a young-adult ceiling for the simulation
SAMPLE_EVERY_S = 1.0               # a wrist monitor reports about once a second
EASE_TAU_S = 6.0                   # heart rate follows effort with a lag, not instantly


class HeartRateMonitor:
    """Produces a beats-per-minute stream during a run.

    Call update(dt, game) every frame. Read current() for a live display and
    summarize() at the end of the run. If a real device is present, feed it with
    push(bpm) and the simulation steps aside.
    """

    def __init__(self, resting: float = RESTING_BPM, rng=None):
        self.resting = resting
        self.bpm = resting
        self.rng = rng or random.Random()
        self.samples: list[dict] = []      # {t_s, bpm, wave, state}
        self._since_sample = 0.0
        self._elapsed = 0.0
        self._external: float | None = None
        self.simulated = True

    # --- real device seam ----------------------------------------------------

    def push(self, bpm: float) -> None:
        """Feed a reading from a real PPG device; the next update uses it."""
        self._external = float(bpm)
        self.simulated = False

    # --- per-frame update ----------------------------------------------------

    def update(self, dt: float, game) -> None:
        self._elapsed += dt
        target = self._external if self._external is not None else self._target(game)
        # Ease toward the target so the trace rises and recovers gradually.
        self.bpm += (target - self.bpm) * (1.0 - math.exp(-dt / EASE_TAU_S))
        self._external = None

        self._since_sample += dt
        if self._since_sample >= SAMPLE_EVERY_S:
            self._since_sample = 0.0
            noisy = self.bpm + self.rng.uniform(-1.5, 1.5)
            reading = max(45.0, min(MAX_BPM + 4, noisy))
            self.samples.append({
                "t_s": round(self._elapsed, 1),
                "bpm": round(reading),
                "wave": int(getattr(game, "wave", 0)),
                "state": getattr(game, "state", ""),
            })

    def _target(self, game) -> float:
        """Where the heart rate is heading, from how hard the game is right now."""
        if getattr(game, "state", "") == "PLAY":
            wave = getattr(game, "wave", 1)
            load = 0.35 + 0.45 * min(1.0, wave / 6.0)          # harder waves, higher load
            target = self.resting + (MAX_BPM - self.resting) * load
            if getattr(game, "shake_ms", 0) > 0 or getattr(game, "bolts", None):
                target += 8.0                                   # a burst of action
            return target
        return self.resting + 6.0                               # rest / recovery

    def current(self) -> int:
        return int(round(self.bpm))

    # --- end of run ----------------------------------------------------------

    def summarize(self) -> dict:
        """Compact effort summary for the coach and the cloud record.

        Resting is estimated from the calmest readings, the rise is how far the
        mean sat above that, and per-wave means show effort tracking difficulty.
        """
        if not self.samples:
            return {}
        bpms = [s["bpm"] for s in self.samples]
        calm = sorted(bpms)[:max(1, len(bpms) // 5)]            # lowest fifth ~ rest
        resting = round(sum(calm) / len(calm))
        mean = round(sum(bpms) / len(bpms))
        peak = max(bpms)

        per_wave: dict[int, list[int]] = {}
        for s in self.samples:
            if s["state"] == "PLAY" and s["wave"]:
                per_wave.setdefault(s["wave"], []).append(s["bpm"])
        per_wave_mean = {str(w): round(sum(v) / len(v)) for w, v in sorted(per_wave.items())}

        return {
            "resting_bpm": resting,
            "mean_bpm": mean,
            "peak_bpm": peak,
            "rise_bpm": max(0, mean - resting),
            "per_wave_mean_bpm": per_wave_mean,
            "samples": len(bpms),
            "simulated": self.simulated,
        }
