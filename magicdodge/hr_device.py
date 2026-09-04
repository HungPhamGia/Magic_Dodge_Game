"""Bridge the real BLE heart-rate watch into the in-game monitor.

heart_rate_monitor.py (repo root) reads a wrist watch over Bluetooth and hands
each decoded reading to an on_bpm callback. This module runs that reader on a
background thread and forwards every beats-per-minute value to a sink, normally
game_heart.push(bpm), so a real trace replaces the simulation and flows into the
coach and the Firebase upload exactly like the simulated one.

A missing watch, a missing bleak install, or a dropped connection must never
stop the game, so every failure just prints a line and leaves the simulation
running, the same policy the camera and wand follow.
"""

from __future__ import annotations

import sys
import threading
from pathlib import Path
from typing import Callable, Optional


class HeartDeviceBridge:
    """Feed a real BLE heart-rate watch into an in-game HeartRateMonitor.

    target: a MAC address or UUID to connect straight to, or None to scan.
    name:   a substring to match a watch by name when target is None.
    sink:   called with each BPM float, normally lambda bpm: heart.push(bpm).
    """

    def __init__(self, sink: Callable[[float], None],
                 target: Optional[str] = None, name: Optional[str] = None):
        self.sink = sink
        self.target = target
        self.name = name
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self) -> None:
        # heart_rate_monitor.py sits at the repo root, one level above this
        # package; make sure it is importable however the game was launched.
        root = str(Path(__file__).resolve().parent.parent)
        if root not in sys.path:
            sys.path.insert(0, root)
        try:
            import asyncio

            import heart_rate_monitor as ble   # its import guard may sys.exit
        except BaseException as error:          # bleak missing -> SystemExit
            print(f"Heart rate watch off ({error}); simulated trace.")
            return
        try:
            asyncio.run(self._listen(ble))
        except BaseException as error:          # scan or connection failure
            print(f"Heart rate watch off ({error}); simulated trace.")

    async def _listen(self, ble) -> None:
        target = self.target
        if target is None and self.name:
            from bleak import BleakScanner

            print(f"Scanning for a heart rate watch named '{self.name}'...")
            device = await BleakScanner.find_device_by_filter(
                lambda d, ad: bool(d.name and self.name.lower() in d.name.lower()),
                timeout=8.0, scanning_mode="active",
            )
            if device is None:
                print(f"Heart rate watch '{self.name}' not found; simulated trace.")
                return
            target = device.address

        def on_bpm(parsed: dict) -> None:
            try:
                self.sink(float(parsed["bpm"]))
            except Exception:
                pass                            # a bad reading must not kill the loop

        print(f"Heart rate watch connecting: {target}")
        monitor = ble.HeartRateMonitor(target, on_bpm=on_bpm)
        await monitor.connect_and_listen()
