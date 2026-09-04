"""Bridge the real BLE heart-rate watch into the in-game monitor.

heart_rate_monitor.py (repo root) reads a wrist watch over Bluetooth and hands
each decoded reading to an on_bpm callback. This module runs that reader on a
background thread and forwards every beats-per-minute value to a sink, normally
game_heart.push(bpm), so a real trace replaces the simulation and flows into the
coach and the Firebase upload exactly like the simulated one.

A missing watch, a missing bleak install, or a dropped connection must never
stop the game, so every failure just prints a line and leaves the simulation
running, the same policy the camera and wand follow. Unlike the camera and the
wand, though, this one keeps trying: a watch is worn mid session, not plugged
in before launch, so it is allowed to show up late and to come back.
"""

from __future__ import annotations

import sys
import threading
from pathlib import Path
from typing import Callable, Optional

SCAN_S = 8.0        # one scan window. A watch advertises in bursts, so missing
                    # one is normal and only means try again
RETRY_S = 5.0       # pause between attempts, so a watch that is simply not
                    # there does not scan in a hot loop for the whole session
REPORT_EVERY = 30   # after the first reading, confirm roughly every 30s. A watch
                    # reports about once a second, so this is a heartbeat on the
                    # terminal without burying the wand and camera lines


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
        self._readings = 0        # proof the watch is actually sending, not just paired

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
        """Find the watch, listen, and keep trying if either part fails.

        One 8s scan used to be the whole attempt: miss that window -- and you
        do, because a watch advertises in bursts and you are usually still
        strapping it on while the game boots -- and the run was simulated with
        no way back but a restart. So this keeps scanning, the same as
        heart_rate_monitor.py does on its own.

        Reconnect works: forcing a disconnect against a real HUAWEI Band,
        connect_and_listen returns and this loop rescans and picks it back up.
        The HUD does not lie in the gap either -- heart_rate.py falls back to
        the simulation after DEVICE_GRACE_S, so a watch that walked away goes
        grey rather than freezing on its last reading.
        """
        import asyncio

        def on_bpm(parsed: dict) -> None:
            # heart_rate_monitor.py prints each reading only when nobody passed
            # an on_bpm, so supplying this callback silenced the watch entirely
            # and a live watch looked identical to the simulation. Say so here.
            try:
                bpm = float(parsed["bpm"])
            except Exception as error:          # a bad reading must not kill the loop
                print(f"Heart rate reading unreadable ({error}): {parsed}")
                return
            self._readings += 1
            if self._readings == 1:
                print(f"Heart rate watch LIVE: {bpm:.0f} bpm. The HUD is now the watch.")
            elif self._readings % REPORT_EVERY == 0:
                print(f"Heart rate watch: {bpm:.0f} bpm ({self._readings} readings).")
            self.sink(bpm)

        while True:
            target = self.target or await self._find()
            if target is not None:
                print(f"Heart rate watch connecting: {target}")
                try:
                    await ble.HeartRateMonitor(target, on_bpm=on_bpm).connect_and_listen()
                except Exception as error:      # out of range, watch busy, dropped
                    print(f"Heart rate watch lost ({error}); retrying.")
                if self._readings == 0:
                    # Paired but never notified: the usual cause is the watch
                    # streaming to its phone app, which holds the HR channel.
                    print("Heart rate watch connected but sent no readings. "
                          "Close its phone app so it can stream here.")
                print("Heart rate watch disconnected; simulated trace until it returns.")
            await asyncio.sleep(RETRY_S)

    async def _find(self):
        """One scan for a heart rate watch. None if none showed up.

        With no name, match the Heart Rate service (0x180D). That is what makes
        a watch a heart rate watch, so it needs no flag and no guess at what the
        thing calls itself. A name, when given, narrows it instead -- useful only
        when more than one strap is in the room.
        """
        from bleak import BleakScanner

        name = self.name                    # bound out for the closure below
        if name:
            wanted = f"named '{name}'"
            def match(device, ad) -> bool:
                # local_name as well as name: a watch often puts its name only
                # in the scan response, so the two disagree early in a scan.
                seen = f"{device.name or ''} {ad.local_name or ''}".lower()
                return name.lower() in seen
        else:
            wanted = "advertising the Heart Rate service"
            def match(device, ad) -> bool:
                return any("180d" in str(u).lower() for u in ad.service_uuids)

        print(f"Scanning for a heart rate watch {wanted}...")
        device = await BleakScanner.find_device_by_filter(
            match, timeout=SCAN_S, scanning_mode="active",
        )
        if device is None:
            print("No heart rate watch yet; simulated trace, still looking."
                  " Launch with --no-hr to play without one.")
            return None
        print(f"Found heart rate watch: {device.address} ({device.name or 'unnamed'})")
        return device.address
