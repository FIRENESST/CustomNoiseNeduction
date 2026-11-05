"""The 便携式降噪麦克风 endpoint: finding it and handing it processed audio.

The NoiseMic driver (driver/NoiseMic) exposes a capture endpoint that other
apps can pick as their microphone. This module finds the driver's wave filter
and writes 48 kHz 16-bit mono PCM to it through a private KS property.
The constants mirror driver/NoiseMic/noisemic_shared.h.
"""

import ctypes
from ctypes import wintypes
from dataclasses import dataclass
import sys
import uuid

import numpy as np


HARDWARE_ID = r"Root\PortableNoiseMic"
WAVE_REFERENCE = "wavenoisemic"
# Device descriptions from NoiseMic.inx, in every language it ships.
DEVICE_NAMES = ("便携式降噪麦克风", "Portable Noise Reduction Mic")

SAMPLE_RATE = 48000
INTERFACE_VERSION = 1
MAX_WRITE_BYTES = 16384

KSCATEGORY_CAPTURE = "{65E8773D-8F56-11D0-A3B9-00A0C9223196}"
KSPROPSETID_NOISEMIC_FEED = "{28ACEC45-31B3-4F23-828D-1DC330D0665C}"
KSPROPERTY_NOISEMIC_WRITE = 0
KSPROPERTY_NOISEMIC_STATUS = 1
KSPROPERTY_TYPE_GET = 0x1
KSPROPERTY_TYPE_SET = 0x2
IOCTL_KS_PROPERTY = 0x002F0003

DIGCF_PRESENT = 0x2
DIGCF_ALLCLASSES = 0x4
DIGCF_DEVICEINTERFACE = 0x10
SPDRP_HARDWAREID = 0x1
GENERIC_READ = 0x80000000
GENERIC_WRITE = 0x40000000
FILE_SHARE_READ = 0x1
FILE_SHARE_WRITE = 0x2
OPEN_EXISTING = 3
FILE_ATTRIBUTE_NORMAL = 0x80
ERROR_NO_MORE_ITEMS = 259
ERROR_INSUFFICIENT_BUFFER = 122
INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value

ULONG_PTR = ctypes.c_size_t


def is_virtual_mic(device_name):
    """Whether a PortAudio device name is this app's own microphone."""
    name = device_name.casefold()
    return any(known.casefold() in name for known in DEVICE_NAMES)


class GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", wintypes.DWORD), ("Data2", wintypes.WORD),
        ("Data3", wintypes.WORD), ("Data4", ctypes.c_ubyte * 8),
    ]

    @classmethod
    def parse(cls, text):
        return cls.from_buffer_copy(uuid.UUID(text).bytes_le)


class SP_DEVINFO_DATA(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD), ("ClassGuid", GUID),
        ("DevInst", wintypes.DWORD), ("Reserved", ULONG_PTR),
    ]

    def __init__(self):
        super().__init__()
        self.cbSize = ctypes.sizeof(self)


class SP_DEVICE_INTERFACE_DATA(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD), ("InterfaceClassGuid", GUID),
        ("Flags", wintypes.DWORD), ("Reserved", ULONG_PTR),
    ]

    def __init__(self):
        super().__init__()
        self.cbSize = ctypes.sizeof(self)


class KSPROPERTY(ctypes.Structure):
    _fields_ = [("Set", GUID), ("Id", wintypes.ULONG), ("Flags", wintypes.ULONG)]


class NOISEMIC_STATUS(ctypes.Structure):
    _fields_ = [(name, wintypes.ULONG) for name in (
        "Version", "SampleRate", "Channels", "BitsPerSample",
        "CaptureStreams", "BufferedBytes", "Underruns", "Overruns",
    )]


def _windows_api():
    setupapi = ctypes.WinDLL("setupapi", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    HDEVINFO = ctypes.c_void_p

    setupapi.SetupDiGetClassDevsW.restype = HDEVINFO
    setupapi.SetupDiGetClassDevsW.argtypes = [ctypes.POINTER(GUID), wintypes.LPCWSTR, wintypes.HWND, wintypes.DWORD]
    setupapi.SetupDiDestroyDeviceInfoList.argtypes = [HDEVINFO]
    setupapi.SetupDiEnumDeviceInfo.argtypes = [HDEVINFO, wintypes.DWORD, ctypes.POINTER(SP_DEVINFO_DATA)]
    setupapi.SetupDiEnumDeviceInterfaces.argtypes = [
        HDEVINFO, ctypes.POINTER(SP_DEVINFO_DATA), ctypes.POINTER(GUID), wintypes.DWORD,
        ctypes.POINTER(SP_DEVICE_INTERFACE_DATA),
    ]
    setupapi.SetupDiGetDeviceInterfaceDetailW.argtypes = [
        HDEVINFO, ctypes.POINTER(SP_DEVICE_INTERFACE_DATA), ctypes.c_void_p, wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD), ctypes.POINTER(SP_DEVINFO_DATA),
    ]
    setupapi.SetupDiGetDeviceRegistryPropertyW.argtypes = [
        HDEVINFO, ctypes.POINTER(SP_DEVINFO_DATA), wintypes.DWORD, ctypes.POINTER(wintypes.DWORD),
        ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD),
    ]
    for function in (
        setupapi.SetupDiDestroyDeviceInfoList, setupapi.SetupDiEnumDeviceInfo,
        setupapi.SetupDiEnumDeviceInterfaces, setupapi.SetupDiGetDeviceInterfaceDetailW,
        setupapi.SetupDiGetDeviceRegistryPropertyW,
    ):
        function.restype = wintypes.BOOL

    kernel32.CreateFileW.restype = wintypes.HANDLE
    kernel32.CreateFileW.argtypes = [
        wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
        wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE,
    ]
    kernel32.CloseHandle.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.DeviceIoControl.restype = wintypes.BOOL
    kernel32.DeviceIoControl.argtypes = [
        wintypes.HANDLE, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD,
        ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p,
    ]
    return setupapi, kernel32


setupapi, kernel32 = _windows_api() if sys.platform == "win32" else (None, None)


def _check(ok):
    if not ok:
        raise ctypes.WinError(ctypes.get_last_error())


def hardware_ids(device_set, device):
    """The REG_MULTI_SZ hardware ids of one device in a device information set."""
    size = wintypes.DWORD()
    setupapi.SetupDiGetDeviceRegistryPropertyW(device_set, ctypes.byref(device), SPDRP_HARDWAREID, None, None, 0, ctypes.byref(size))
    if not size.value:
        return []
    buffer = ctypes.create_unicode_buffer(size.value // ctypes.sizeof(ctypes.c_wchar) + 1)
    if not setupapi.SetupDiGetDeviceRegistryPropertyW(
        device_set, ctypes.byref(device), SPDRP_HARDWAREID, None, buffer, size.value, None,
    ):
        return []
    return [item for item in buffer[:size.value // ctypes.sizeof(ctypes.c_wchar)].split("\0") if item]


def _interface_path(device_set, interface, device):
    size = wintypes.DWORD()
    setupapi.SetupDiGetDeviceInterfaceDetailW(device_set, ctypes.byref(interface), None, 0, ctypes.byref(size), None)
    if ctypes.get_last_error() != ERROR_INSUFFICIENT_BUFFER or not size.value:
        return None
    detail = ctypes.create_string_buffer(size.value)
    # SP_DEVICE_INTERFACE_DETAIL_DATA_W.cbSize counts the fixed part only.
    ctypes.c_uint32.from_buffer(detail).value = 8 if ctypes.sizeof(ctypes.c_void_p) == 8 else 6
    if not setupapi.SetupDiGetDeviceInterfaceDetailW(
        device_set, ctypes.byref(interface), detail, size.value, None, ctypes.byref(device),
    ):
        return None
    return ctypes.wstring_at(ctypes.addressof(detail) + 4)


def capture_interfaces():
    """Yield (device path, hardware ids) for every present KS capture filter."""
    category = GUID.parse(KSCATEGORY_CAPTURE)
    device_set = setupapi.SetupDiGetClassDevsW(ctypes.byref(category), None, None, DIGCF_PRESENT | DIGCF_DEVICEINTERFACE)
    if device_set in (None, INVALID_HANDLE_VALUE):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        index = 0
        while True:
            interface = SP_DEVICE_INTERFACE_DATA()
            if not setupapi.SetupDiEnumDeviceInterfaces(device_set, None, ctypes.byref(category), index, ctypes.byref(interface)):
                if ctypes.get_last_error() == ERROR_NO_MORE_ITEMS:
                    return
                raise ctypes.WinError(ctypes.get_last_error())
            index += 1
            device = SP_DEVINFO_DATA()
            path = _interface_path(device_set, interface, device)
            if path is not None:
                yield path, hardware_ids(device_set, device)
    finally:
        setupapi.SetupDiDestroyDeviceInfoList(device_set)


def find_virtual_mic():
    """Device path of the 便携式降噪麦克风 wave filter, or None when it is not installed."""
    if setupapi is None:
        return None
    try:
        for path, ids in capture_interfaces():
            if (
                path.casefold().endswith("\\" + WAVE_REFERENCE)
                and any(item.casefold() == HARDWARE_ID.casefold() for item in ids)
            ):
                return path
    except OSError:
        return None
    return None


@dataclass(frozen=True)
class MicStatus:
    capture_streams: int
    buffered_ms: float
    underruns: int
    overruns: int

    @property
    def in_use(self):
        return self.capture_streams > 0


class Resampler:
    """Streaming linear interpolation from the session rate to 48 kHz."""

    def __init__(self, source_rate, target_rate=SAMPLE_RATE):
        self.step = source_rate / target_rate
        self._previous = 0.0
        self._position = 1.0    # next output, in input samples after the kept previous sample

    def process(self, samples):
        signal = np.concatenate(([self._previous], samples))
        last = len(signal) - 1
        if self._position > last:
            self._position -= last
            self._previous = float(signal[-1])
            return np.zeros(0, dtype=np.float32)
        count = int((last - self._position) // self.step) + 1
        positions = self._position + self.step * np.arange(count)
        output = np.interp(positions, np.arange(len(signal)), signal).astype(np.float32)
        self._position = positions[-1] + self.step - last
        self._previous = float(signal[-1])
        return output


class VirtualMic:
    """An open handle to the driver's wave filter.

    push() runs on the audio thread and status() on the UI thread. Each
    property call takes microseconds, so they share one handle.
    """

    def __init__(self, path):
        self.path = path
        self.failure = None
        self._resampler = None
        self._returned = wintypes.DWORD()
        self._write = KSPROPERTY(GUID.parse(KSPROPSETID_NOISEMIC_FEED), KSPROPERTY_NOISEMIC_WRITE, KSPROPERTY_TYPE_SET)
        self._status = KSPROPERTY(GUID.parse(KSPROPSETID_NOISEMIC_FEED), KSPROPERTY_NOISEMIC_STATUS, KSPROPERTY_TYPE_GET)
        handle = kernel32.CreateFileW(
            path, GENERIC_READ | GENERIC_WRITE, FILE_SHARE_READ | FILE_SHARE_WRITE, None,
            OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, None,
        )
        if handle in (None, INVALID_HANDLE_VALUE):
            raise ctypes.WinError(ctypes.get_last_error())
        self._handle = handle
        try:
            status = self._query()
        except OSError:
            self.close()
            raise
        if status.Version != INTERFACE_VERSION or status.SampleRate != SAMPLE_RATE:
            self.close()
            raise OSError("降噪麦克风驱动版本与本软件不匹配，请重新安装虚拟麦克风。")

    def _query(self):
        status = NOISEMIC_STATUS()
        _check(kernel32.DeviceIoControl(
            self._handle, IOCTL_KS_PROPERTY, ctypes.byref(self._status), ctypes.sizeof(self._status),
            ctypes.byref(status), ctypes.sizeof(status), ctypes.byref(wintypes.DWORD()), None,
        ))
        return status

    def status(self):
        status = self._query()
        return MicStatus(
            status.CaptureStreams, status.BufferedBytes / (2 * SAMPLE_RATE / 1000),
            status.Underruns, status.Overruns,
        )

    def begin(self, sample_rate):
        """Prepare for a session's stream rate. Call before the stream starts."""
        self.failure = None
        self._resampler = None if sample_rate == SAMPLE_RATE else Resampler(sample_rate)

    def push(self, samples):
        """Send one block of float samples in [-1, 1]. Runs on the audio thread."""
        if self.failure is not None or self._handle is None:
            return
        if self._resampler is not None:
            samples = self._resampler.process(samples)
            if not len(samples):
                return
        pcm = np.rint(np.clip(samples, -1.0, 1.0) * 32767).astype("<i2")
        for start in range(0, len(pcm), MAX_WRITE_BYTES // 2):
            chunk = pcm[start:start + MAX_WRITE_BYTES // 2]
            if not kernel32.DeviceIoControl(
                self._handle, IOCTL_KS_PROPERTY, ctypes.byref(self._write), ctypes.sizeof(self._write),
                chunk.ctypes.data, chunk.nbytes, ctypes.byref(self._returned), None,
            ):
                self.failure = ctypes.FormatError(ctypes.get_last_error()).strip()
                return

    def close(self):
        handle, self._handle = getattr(self, "_handle", None), None
        if handle is not None:
            kernel32.CloseHandle(handle)
