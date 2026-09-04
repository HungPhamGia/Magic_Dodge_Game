"""BLE Heart Rate Monitor using Bleak.

Supports:
- Auto-scanning (Active Mode) and interactive device selection
- Passing device address / name via CLI arguments
- Full GATT 0x2A37 decoding (BPM, Contact Status, RR-Intervals, Energy Expended)
- Modular HeartRateMonitor class for easy import into other modules
"""

import argparse
import asyncio
import sys
from datetime import datetime
from typing import Callable, Optional

# Every message below is emoji + Vietnamese, neither of which exists in cp1252.
# Windows only gives stdout UTF-8 when it is a real console: redirect it to a
# pipe, a log file, or an IDE run window and it falls back to the ANSI codepage,
# where the first print in connect_and_listen raises UnicodeEncodeError and takes
# the connection down with it. That is invisible when you run this script in a
# terminal and fatal when the game imports it, so pin the encoding here.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, ValueError, OSError):
    pass                    # pythonw, or a stream that cannot be reconfigured

try:
    from bleak import BleakClient, BleakScanner
    from bleak.backends.device import BLEDevice
    from bleak.backends.scanner import AdvertisementData
except ImportError:
    print(
        "❌ Thư viện 'bleak' chưa được cài đặt!\n"
        "👉 Hãy cài đặt bằng lệnh: pip install bleak",
        file=sys.stderr,
    )
    sys.exit(1)

# Standard Bluetooth SIG UUIDs
HEART_RATE_SERVICE_UUID = "0000180d-0000-1000-8000-00805f9b34fb"
HEART_RATE_MEASUREMENT_UUID = "00002a37-0000-1000-8000-00805f9b34fb"
BATTERY_LEVEL_UUID = "00002a19-0000-1000-8000-00805f9b34fb"
DEFAULT_HEART_RATE_DEVICE = "C8:8F:68:A9:5E:AD"


def parse_heart_rate_data(data: bytearray) -> dict:
    """Decode BLE Heart Rate Measurement characteristic payload (0x2A37)."""
    flags = data[0]
    is_16_bit = bool(flags & 0x01)
    sensor_contact_supported = bool(flags & 0x04)
    sensor_contact_detected = bool(flags & 0x02)
    energy_expended_present = bool(flags & 0x08)
    rr_interval_present = bool(flags & 0x10)

    offset = 1

    # 1. Heart Rate Value
    if is_16_bit:
        hr_bpm = int.from_bytes(data[offset : offset + 2], byteorder="little")
        offset += 2
    else:
        hr_bpm = data[offset]
        offset += 1

    # 2. Energy Expended (kilo Joules)
    energy_expended = None
    if energy_expended_present:
        energy_expended = int.from_bytes(data[offset : offset + 2], byteorder="little")
        offset += 2

    # 3. RR-Intervals (resolution: 1/1024 seconds -> ms)
    rr_intervals = []
    if rr_interval_present:
        while offset + 1 < len(data):
            raw_rr = int.from_bytes(data[offset : offset + 2], byteorder="little")
            rr_ms = round((raw_rr / 1024.0) * 1000.0, 1)
            rr_intervals.append(rr_ms)
            offset += 2

    return {
        "bpm": hr_bpm,
        "contact_supported": sensor_contact_supported,
        "contact_detected": sensor_contact_detected,
        "energy_expended_kj": energy_expended,
        "rr_intervals_ms": rr_intervals,
    }


class HeartRateMonitor:
    """Class to manage BLE connection and read Heart Rate data."""

    def __init__(
        self,
        address_or_device: str | BLEDevice,
        on_bpm: Optional[Callable[[dict], None]] = None,
    ):
        self.target = address_or_device
        self.on_bpm = on_bpm
        self.client: Optional[BleakClient] = None
        self._is_running = False

    def _notification_handler(self, sender, data: bytearray):
        parsed = parse_heart_rate_data(data)
        if self.on_bpm:
            self.on_bpm(parsed)
        else:
            time_str = datetime.now().strftime("%H:%M:%S")
            rr_info = f" | RR: {parsed['rr_intervals_ms']} ms" if parsed["rr_intervals_ms"] else ""
            print(f"[{time_str}] ❤️ Heart Rate: {parsed['bpm']} BPM{rr_info}")

    async def connect_and_listen(self):
        """Connect to device and start listening for heart rate notifications."""
        print(f"🔗 Đang kết nối tới thiết bị: {self.target}...")

        async with BleakClient(self.target) as client:
            self.client = client
            if not client.is_connected:
                print("❌ Không thể kết nối tới thiết bị.")
                return False

            print(f"✅ Đã kết nối thành công tới: {client.address}")

            # Đọc mức pin nếu có Battery Service
            try:
                battery_bytes = await client.read_gatt_char(BATTERY_LEVEL_UUID)
                battery_level = int(battery_bytes[0])
                print(f"🔋 Pin thiết bị: {battery_level}%")
            except Exception:
                pass

            # Đăng ký nhận thông báo nhịp tim
            await client.start_notify(
                HEART_RATE_MEASUREMENT_UUID, self._notification_handler
            )
            print("📡 Đang lắng nghe dữ liệu nhịp tim... Nhấn Ctrl+C để thoát.\n")

            self._is_running = True
            try:
                # is_connected as well as the flag: when the watch walks out of
                # range this returns on the next half second, so the caller can
                # go back to scanning. Waiting for the context manager alone to
                # notice took about twelve seconds.
                while self._is_running and client.is_connected:
                    await asyncio.sleep(0.5)
            except asyncio.CancelledError:
                pass
            finally:
                if client.is_connected:
                    await client.stop_notify(HEART_RATE_MEASUREMENT_UUID)
                    print("\n🛑 Đã dừng nhận dữ liệu nhịp tim.")
            return True


async def scan_and_select_device() -> Optional[str | BLEDevice]:
    """Continuously scan for BLE devices until the user selects one."""
    devices: list[BLEDevice] = []
    device_indexes: dict[str, int] = {}

    def on_detected(device: BLEDevice, advertisement: AdvertisementData):
        if device.address in device_indexes:
            return

        device_indexes[device.address] = len(devices)
        devices.append(device)
        index = len(devices)
        name = advertisement.local_name or device.name or "Không tên (No Name)"
        uuids = [str(uuid).lower() for uuid in advertisement.service_uuids]
        is_hr = (
            "❤️ [HR Service]"
            if any("180d" in uuid or "2a37" in uuid for uuid in uuids)
            else ""
        )
        print(
            f"\n[{index:2d}] {device.address} | "
            f"RSSI: {advertisement.rssi:>3} dBm | {name} {is_hr}"
        )

    scanner = BleakScanner(on_detected, scanning_mode="active")
    print("🔍 Đang quét BLE liên tục. Thiết bị mới sẽ hiện ngay khi tìm thấy.")
    print("👉 Nhập số thiết bị hoặc địa chỉ MAC/UUID (Enter để hủy).")

    try:
        await scanner.start()
        while True:
            choice = (await asyncio.to_thread(input, "\nChọn thiết bị: ")).strip()
            if not choice:
                return None
            if choice.isdigit():
                index = int(choice) - 1
                if 0 <= index < len(devices):
                    return devices[index]
            elif ":" in choice or "-" in choice:
                return choice
            print("❌ Chưa có thiết bị mang số đó; máy vẫn đang quét...")
    except Exception as e:
        print(f"⚠️ Lỗi quét BLE: {e}")
        return None
    finally:
        await scanner.stop()


async def async_main():
    parser = argparse.ArgumentParser(description="BLE Heart Rate Monitor")
    parser.add_argument(
        "-a",
        "--address",
        type=str,
        default=None,
        help="Địa chỉ MAC hoặc UUID của đồng hồ",
    )
    parser.add_argument(
        "-n",
        "--name",
        type=str,
        default=None,
        help="Tên đồng hồ cần tìm để tự động kết nối (ví dụ: 'HUAWEI', 'Band')",
    )
    args = parser.parse_args()

    target_address = args.address
    using_default_device = False

    if not target_address and args.name:
        print(f"🔍 Đang tìm thiết bị có tên chứa '{args.name}'...")
        while not target_address:
            device = await BleakScanner.find_device_by_filter(
                lambda d, ad: (d.name and args.name.lower() in d.name.lower())
                or (ad.local_name and args.name.lower() in ad.local_name.lower()),
                timeout=8.0,
                scanning_mode="active",
            )
            if device:
                target_address = device
                print(f"🎯 Đã tìm thấy: {device.name} ({device.address})")
            else:
                print("⏳ Chưa thấy, tiếp tục quét... (Ctrl+C để dừng)")

    if not target_address and not args.name:
        target_address = DEFAULT_HEART_RATE_DEVICE
        using_default_device = True
        print(f"🎯 Tự động kết nối đồng hồ: {target_address}")

    if not target_address:
        print("Đã thoát.")
        return

    monitor = HeartRateMonitor(target_address)
    try:
        connected = await monitor.connect_and_listen()
    except Exception as error:
        if not using_default_device:
            raise
        connected = False
        print(f"⚠️ Kết nối trực tiếp thất bại: {error}")

    if using_default_device and not connected:
        print("🔄 Chuyển sang quét BLE liên tục...")
        target_address = await scan_and_select_device()
        if target_address:
            await HeartRateMonitor(target_address).connect_and_listen()


def main():
    try:
        asyncio.run(async_main())
    except KeyboardInterrupt:
        print("\n👋 Đã tắt ứng dụng.")


if __name__ == "__main__":
    main()
    
