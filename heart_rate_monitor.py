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

try:
    from bleak import BleakClient, BleakScanner
    from bleak.backends.device import BLEDevice
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
                return

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
                while self._is_running:
                    await asyncio.sleep(0.5)
            except asyncio.CancelledError:
                pass
            finally:
                if client.is_connected:
                    await client.stop_notify(HEART_RATE_MEASUREMENT_UUID)
                    print("\n🛑 Đã dừng nhận dữ liệu nhịp tim.")


async def scan_and_select_device() -> Optional[str]:
    """Scan for BLE devices and let user pick from a numbered list."""
    print("🔍 Đang quét thiết bị BLE xung quanh trong 8 giây (Active Scan)...")
    try:
        devices = await BleakScanner.discover(timeout=8.0, scanning_mode="active")
    except Exception as e:
        print(f"⚠️ Lỗi quét BLE: {e}")
        return None

    if not devices:
        print("⚠️ Không tìm thấy thiết bị BLE nào!")
        return None

    print("\n📋 Danh sách thiết bị tìm thấy:")
    print("-" * 75)
    valid_devices = []
    for idx, d in enumerate(devices, start=1):
        name = d.name or "Không tên (No Name)"
        uuids = [str(u).lower() for u in d.metadata.get("uuids", [])]
        is_hr = (
            "❤️ [HR Service]"
            if any("180d" in u or "2a37" in u for u in uuids)
            else ""
        )
        print(f"[{idx:2d}] {d.address} | RSSI: {d.rssi:>3} dBm | {name} {is_hr}")
        valid_devices.append(d)
    print("-" * 75)

    while True:
        choice = input(
            f"\n👉 Chọn số thứ tự thiết bị (1-{len(valid_devices)}) hoặc nhập MAC trực tiếp (Enter để hủy): "
        ).strip()
        if not choice:
            return None
        if choice.isdigit() and 1 <= int(choice) <= len(valid_devices):
            return valid_devices[int(choice) - 1].address
        if ":" in choice or "-" in choice:
            return choice
        print("❌ Lựa chọn không hợp lệ, vui lòng thử lại.")


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

    if not target_address and args.name:
        print(f"🔍 Đang tìm thiết bị có tên chứa '{args.name}'...")
        device = await BleakScanner.find_device_by_filter(
            lambda d, ad: (d.name and args.name.lower() in d.name.lower())
            or (ad.local_name and args.name.lower() in ad.local_name.lower()),
            timeout=8.0,
            scanning_mode="active",
        )
        if device:
            target_address = device.address
            print(f"🎯 Đã tìm thấy: {device.name} ({device.address})")
        else:
            print(f"❌ Không tìm thấy thiết bị nào có tên chứa '{args.name}'")
            return

    if not target_address:
        target_address = await scan_and_select_device()

    if not target_address:
        print("Đã thoát.")
        return

    monitor = HeartRateMonitor(target_address)
    await monitor.connect_and_listen()


def main():
    try:
        asyncio.run(async_main())
    except KeyboardInterrupt:
        print("\n👋 Đã tắt ứng dụng.")


if __name__ == "__main__":
    main()
