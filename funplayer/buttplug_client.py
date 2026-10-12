"""Minimal Buttplug protocol (v3) client, talks to Intiface Central's websocket
server. No dependency on the buttplug-py package (unmaintained on PyPI); we
speak the JSON protocol directly.

Runs its own asyncio event loop on a background thread so it can be driven
from Qt's main thread without blocking the UI.
"""
from __future__ import annotations

import asyncio
import itertools
import json
import threading
from dataclasses import dataclass, field

import websockets
from PySide6.QtCore import QObject, Signal

CLIENT_NAME = "FunPlayer"
MESSAGE_VERSION = 3


@dataclass
class Device:
    index: int
    name: str
    has_linear: bool = False
    has_scalar_vibrate: bool = False
    linear_feature_index: int = 0
    scalar_feature_index: int = 0

    @property
    def display_name(self) -> str:
        caps = []
        if self.has_linear:
            caps.append("linear")
        if self.has_scalar_vibrate:
            caps.append("vibrate")
        return f"{self.name} ({', '.join(caps) or 'unknown'})"


def _parse_device(raw: dict) -> Device:
    dev = Device(index=raw["DeviceIndex"], name=raw.get("DeviceName", "Unknown"))
    msgs = raw.get("DeviceMessages", {})
    if "LinearCmd" in msgs:
        dev.has_linear = True
        dev.linear_feature_index = msgs["LinearCmd"][0].get("Index", 0) if msgs["LinearCmd"] else 0
    if "ScalarCmd" in msgs:
        for i, feat in enumerate(msgs["ScalarCmd"]):
            if feat.get("ActuatorType") == "Vibrate":
                dev.has_scalar_vibrate = True
                dev.scalar_feature_index = feat.get("Index", i)
                break
    return dev


class ButtplugClient(QObject):
    connected = Signal(bool)
    devices_changed = Signal(list)   # list[Device]
    error = Signal(str)

    def __init__(self) -> None:
        super().__init__()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._ws = None
        self._id_counter = itertools.count(1)
        self._devices: dict[int, Device] = {}
        self._is_connected = False

    # ---- public, thread-safe API (call from Qt main thread) ----

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()

    def connect_to(self, host: str, port: int) -> None:
        self._submit(self._connect(host, port))

    def disconnect(self) -> None:
        self._submit(self._disconnect())

    def start_scanning(self) -> None:
        self._submit(self._send({"StartScanning": {"Id": self._next_id()}}))

    def stop_scanning(self) -> None:
        self._submit(self._send({"StopScanning": {"Id": self._next_id()}}))

    def request_device_list(self) -> None:
        self._submit(self._request_device_list())

    def send_linear(self, device_index: int, feature_index: int, position: float, duration_ms: int) -> None:
        position = max(0.0, min(1.0, position))
        msg = {
            "LinearCmd": {
                "Id": self._next_id(),
                "DeviceIndex": device_index,
                "Vectors": [{"Index": feature_index, "Duration": max(1, int(duration_ms)), "Position": position}],
            }
        }
        self._submit(self._send(msg))

    def send_scalar_vibrate(self, device_index: int, feature_index: int, scalar: float) -> None:
        scalar = max(0.0, min(1.0, scalar))
        msg = {
            "ScalarCmd": {
                "Id": self._next_id(),
                "DeviceIndex": device_index,
                "Scalars": [{"Index": feature_index, "Scalar": scalar, "ActuatorType": "Vibrate"}],
            }
        }
        self._submit(self._send(msg))

    def stop_device(self, device_index: int) -> None:
        self._submit(self._send({"StopDeviceCmd": {"Id": self._next_id(), "DeviceIndex": device_index}}))

    def stop_all(self) -> None:
        self._submit(self._send({"StopAllDevices": {"Id": self._next_id()}}))

    def shutdown(self) -> None:
        if self._loop:
            self._submit(self._disconnect())
            self._loop.call_soon_threadsafe(self._loop.stop)

    @property
    def devices(self) -> list[Device]:
        return list(self._devices.values())

    # ---- internals ----

    def _next_id(self) -> int:
        return next(self._id_counter)

    def _submit(self, coro) -> None:
        if self._loop is None:
            self.error.emit("Buttplug client not started")
            return
        asyncio.run_coroutine_threadsafe(coro, self._loop)

    def _run_loop(self) -> None:
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        self._loop.run_forever()

    async def _connect(self, host: str, port: int) -> None:
        url = f"ws://{host}:{port}"
        try:
            self._ws = await websockets.connect(url, ping_interval=None)
        except OSError as exc:
            self.error.emit(f"Could not connect to Intiface at {url}: {exc}")
            return

        asyncio.ensure_future(self._recv_loop())

        handshake = {
            "RequestServerInfo": {
                "Id": self._next_id(),
                "ClientName": CLIENT_NAME,
                "MessageVersion": MESSAGE_VERSION,
            }
        }
        await self._send(handshake)

    async def _disconnect(self) -> None:
        if self._ws is not None:
            try:
                await self._ws.close()
            except Exception:
                pass
        self._reset_connection_state()

    def _reset_connection_state(self) -> None:
        """Common cleanup for both a deliberate disconnect and the server
        closing the connection on its own (Intiface quitting, device
        manager restarting, etc.) -- both need to leave the client in the
        same clean state. Previously the unexpected-closure path in
        _recv_loop only flipped self._is_connected and emitted `connected`,
        but left self._ws pointing at a dead (closed, non-None) socket
        object. Every subsequent send from SyncEngine's background thread
        (which keeps ticking regardless) would then hit _send()'s
        `if self._ws is None: return` guard, find it *not* None, actually
        attempt to send on the dead socket, raise, and emit `error` --
        every single tick, ~25/sec. Each one triggered a blocking modal
        QMessageBox in the UI, so within a second or two dozens of nested
        modal dialogs piled up, which is what actually presented as a
        crash/hang. Setting self._ws back to None here closes that off at
        the source, matching what a manual disconnect already did.
        """
        self._ws = None
        self._is_connected = False
        self._devices.clear()
        self.connected.emit(False)
        self.devices_changed.emit([])

    async def _request_device_list(self) -> None:
        await self._send({"RequestDeviceList": {"Id": self._next_id()}})

    async def _send(self, message: dict) -> None:
        if self._ws is None:
            return
        try:
            await self._ws.send(json.dumps([message]))
        except Exception as exc:
            self.error.emit(f"Buttplug send failed: {exc}")

    async def _recv_loop(self) -> None:
        try:
            async for raw in self._ws:
                try:
                    payload = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                for entry in payload:
                    self._handle_message(entry)
        except websockets.ConnectionClosed:
            self._reset_connection_state()
        except Exception as exc:
            self.error.emit(f"Buttplug receive error: {exc}")
            self._reset_connection_state()
        else:
            # async-for ended without an exception -- some websockets
            # versions surface a clean server-initiated close this way
            # instead of raising. Still need the same cleanup.
            if self._ws is not None:
                self._reset_connection_state()

    def _handle_message(self, entry: dict) -> None:
        if "ServerInfo" in entry:
            self._is_connected = True
            self.connected.emit(True)
            asyncio.ensure_future(self._request_device_list())
        elif "DeviceList" in entry:
            devices = [_parse_device(d) for d in entry["DeviceList"].get("Devices", [])]
            self._devices = {d.index: d for d in devices}
            self.devices_changed.emit(list(self._devices.values()))
        elif "DeviceAdded" in entry:
            d = _parse_device(entry["DeviceAdded"])
            self._devices[d.index] = d
            self.devices_changed.emit(list(self._devices.values()))
        elif "DeviceRemoved" in entry:
            idx = entry["DeviceRemoved"].get("DeviceIndex")
            self._devices.pop(idx, None)
            self.devices_changed.emit(list(self._devices.values()))
        elif "Error" in entry:
            self.error.emit(entry["Error"].get("ErrorMessage", "Unknown Buttplug error"))
