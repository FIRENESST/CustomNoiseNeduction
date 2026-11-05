"""Install or remove the 便携式降噪麦克风 driver.

The app stays portable: the driver package travels inside the EXE and is
installed only when the user asks. Installing needs administrator rights, so
the app starts a second, elevated copy of itself with --virtual-mic-setup;
that copy does the SetupAPI work and reports back through a small JSON file.
"""

import ctypes
from ctypes import wintypes
from dataclasses import dataclass
import json
import os
from pathlib import Path
import sys
import tempfile
import winreg

from virtual_mic import (
    DIGCF_ALLCLASSES, ERROR_NO_MORE_ITEMS, GUID, HARDWARE_ID, INVALID_HANDLE_VALUE,
    SP_DEVINFO_DATA, hardware_ids, setupapi,
)


SETUP_FLAG = "--virtual-mic-setup"
INF_NAME = "NoiseMic.inf"
PACKAGE_FILES = (INF_NAME, "NoiseMic.sys", "noisemic.cat")

DICD_GENERATE_ID = 0x1
DIF_REGISTERDEVICE = 0x19
DIF_REMOVE = 0x05
INSTALLFLAG_FORCE = 0x1
DICS_FLAG_GLOBAL = 0x1
DIREG_DRV = 0x2
KEY_READ = 0x20019
SUOI_FORCEDELETE = 0x1
SEE_MASK_NOCLOSEPROCESS = 0x40
SEE_MASK_NO_CONSOLE = 0x8000
SW_HIDE = 0
INFINITE = 0xFFFFFFFF
ERROR_CANCELLED = 1223
STILL_ACTIVE = 259


def package_dir():
    """The driver package shipped with this copy of the app, or None."""
    if getattr(sys, "frozen", False):
        candidates = [Path(sys._MEIPASS) / "driver"]
    else:
        project = Path(__file__).resolve().parent
        # Prefer the Microsoft-signed package returned by attestation signing.
        candidates = [project / "driver" / "signed", project / "driver" / "package"]
    for folder in candidates:
        if all((folder / name).is_file() for name in PACKAGE_FILES):
            return folder
    return None


# --- SetupAPI work, run in the elevated copy ------------------------------------

def _api():
    newdev = ctypes.WinDLL("newdev", use_last_error=True)
    HDEVINFO = ctypes.c_void_p
    s = setupapi
    s.SetupDiGetINFClassW.restype = wintypes.BOOL
    s.SetupDiGetINFClassW.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(GUID), wintypes.LPWSTR, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
    s.SetupDiCreateDeviceInfoList.restype = HDEVINFO
    s.SetupDiCreateDeviceInfoList.argtypes = [ctypes.POINTER(GUID), wintypes.HWND]
    s.SetupDiCreateDeviceInfoW.restype = wintypes.BOOL
    s.SetupDiCreateDeviceInfoW.argtypes = [
        HDEVINFO, wintypes.LPCWSTR, ctypes.POINTER(GUID), wintypes.LPCWSTR, wintypes.HWND,
        wintypes.DWORD, ctypes.POINTER(SP_DEVINFO_DATA),
    ]
    s.SetupDiSetDeviceRegistryPropertyW.restype = wintypes.BOOL
    s.SetupDiSetDeviceRegistryPropertyW.argtypes = [HDEVINFO, ctypes.POINTER(SP_DEVINFO_DATA), wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD]
    s.SetupDiCallClassInstaller.restype = wintypes.BOOL
    s.SetupDiCallClassInstaller.argtypes = [wintypes.DWORD, HDEVINFO, ctypes.POINTER(SP_DEVINFO_DATA)]
    s.SetupDiOpenDevRegKey.restype = ctypes.c_void_p
    s.SetupDiOpenDevRegKey.argtypes = [HDEVINFO, ctypes.POINTER(SP_DEVINFO_DATA), wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD]
    s.SetupUninstallOEMInfW.restype = wintypes.BOOL
    s.SetupUninstallOEMInfW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, ctypes.c_void_p]
    newdev.UpdateDriverForPlugAndPlayDevicesW.restype = wintypes.BOOL
    newdev.UpdateDriverForPlugAndPlayDevicesW.argtypes = [
        wintypes.HWND, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD, ctypes.POINTER(wintypes.BOOL),
    ]
    newdev.DiUninstallDevice.restype = wintypes.BOOL
    newdev.DiUninstallDevice.argtypes = [
        wintypes.HWND, HDEVINFO, ctypes.POINTER(SP_DEVINFO_DATA), wintypes.DWORD, ctypes.POINTER(wintypes.BOOL),
    ]
    return newdev


def _error(code=None):
    code = ctypes.get_last_error() if code is None else code
    unsigned = code & 0xFFFFFFFF
    if 0xE0000240 <= unsigned <= 0xE0000247 or 0x800B0000 <= unsigned <= 0x800BFFFF:
        return OSError(code, "驱动没有通过 Windows 的签名验证。正式版需要微软签名的驱动包；"
                             "测试版只能装在开启了测试签名的电脑上。")
    if unsigned == 5:
        return OSError(code, "需要管理员权限。")
    text = ctypes.FormatError(code).strip() if unsigned < 0x10000 else ""
    return OSError(code, f"{text} (0x{unsigned:08X})" if text else f"安装程序返回错误 0x{unsigned:08X}。")


def _devices_with_our_id(callback):
    """Call callback(device_set, device) for every device node, present or not, with our hardware id."""
    device_set = setupapi.SetupDiGetClassDevsW(None, "ROOT", None, DIGCF_ALLCLASSES)
    if device_set in (None, INVALID_HANDLE_VALUE):
        raise _error()
    try:
        index = 0
        while True:
            device = SP_DEVINFO_DATA()
            if not setupapi.SetupDiEnumDeviceInfo(device_set, index, ctypes.byref(device)):
                if ctypes.get_last_error() == ERROR_NO_MORE_ITEMS:
                    return
                raise _error()
            index += 1
            if any(item.casefold() == HARDWARE_ID.casefold() for item in hardware_ids(device_set, device)):
                callback(device_set, device)
    finally:
        setupapi.SetupDiDestroyDeviceInfoList(device_set)


def _driver_inf(device_set, device):
    """The oemNN.inf name Windows gave our package, read from the device's driver key."""
    key = setupapi.SetupDiOpenDevRegKey(device_set, ctypes.byref(device), DICS_FLAG_GLOBAL, 0, DIREG_DRV, KEY_READ)
    if key in (None, INVALID_HANDLE_VALUE):
        return None
    try:
        value, _kind = winreg.QueryValueEx(key, "InfPath")
        return value
    except OSError:
        return None
    finally:
        winreg.CloseKey(key)


@dataclass
class SetupResult:
    ok: bool
    message: str
    reboot: bool = False


def install(folder):
    newdev = _api()
    inf = str(Path(folder, INF_NAME).resolve())
    existing = []
    _devices_with_our_id(lambda _set, _device: existing.append(True))

    created = None
    if not existing:
        class_guid = GUID()
        class_name = ctypes.create_unicode_buffer(64)
        if not setupapi.SetupDiGetINFClassW(inf, ctypes.byref(class_guid), class_name, len(class_name), None):
            raise _error()
        device_set = setupapi.SetupDiCreateDeviceInfoList(ctypes.byref(class_guid), None)
        if device_set in (None, INVALID_HANDLE_VALUE):
            raise _error()
        device = SP_DEVINFO_DATA()
        try:
            if not setupapi.SetupDiCreateDeviceInfoW(
                device_set, class_name.value, ctypes.byref(class_guid), None, None, DICD_GENERATE_ID, ctypes.byref(device),
            ):
                raise _error()
            ids = ctypes.create_unicode_buffer(HARDWARE_ID + "\0")
            if not setupapi.SetupDiSetDeviceRegistryPropertyW(device_set, ctypes.byref(device), 1, ids, ctypes.sizeof(ids)):
                raise _error()
            if not setupapi.SetupDiCallClassInstaller(DIF_REGISTERDEVICE, device_set, ctypes.byref(device)):
                raise _error()
            created = (device_set, device)
        except OSError:
            setupapi.SetupDiDestroyDeviceInfoList(device_set)
            raise

    reboot = wintypes.BOOL()
    try:
        if not newdev.UpdateDriverForPlugAndPlayDevicesW(None, HARDWARE_ID, inf, INSTALLFLAG_FORCE, ctypes.byref(reboot)):
            raise _error()
    except OSError:
        if created is not None:
            # Leave no driverless device node behind.
            setupapi.SetupDiCallClassInstaller(DIF_REMOVE, created[0], ctypes.byref(created[1]))
        raise
    finally:
        if created is not None:
            setupapi.SetupDiDestroyDeviceInfoList(created[0])
    message = "已安装「便携式降噪麦克风」。"
    if reboot.value:
        message += "重启电脑后生效。"
    return SetupResult(True, message, bool(reboot.value))


def uninstall():
    newdev = _api()
    infs = set()
    reboot = wintypes.BOOL()

    def remove(device_set, device):
        inf = _driver_inf(device_set, device)
        if inf:
            infs.add(inf)
        needs = wintypes.BOOL()
        if not newdev.DiUninstallDevice(None, device_set, ctypes.byref(device), 0, ctypes.byref(needs)):
            raise _error()
        reboot.value = reboot.value or needs.value

    _devices_with_our_id(remove)
    for inf in sorted(infs):
        if not setupapi.SetupUninstallOEMInfW(inf, SUOI_FORCEDELETE, None):
            raise _error()
    if not infs and not reboot.value:
        return SetupResult(True, "没有找到已安装的降噪麦克风。")
    message = "已卸载「便携式降噪麦克风」。"
    if reboot.value:
        message += "重启电脑后完成移除。"
    return SetupResult(True, message, bool(reboot.value))


def run_setup(argv):
    """Entry point of the elevated copy: <action> --result <path>."""
    action = argv[0] if argv else ""
    result_path = argv[argv.index("--result") + 1] if "--result" in argv else None
    try:
        if action == "install":
            folder = package_dir()
            if folder is None:
                raise OSError("这个版本没有附带降噪麦克风驱动。")
            result = install(folder)
        elif action == "uninstall":
            result = uninstall()
        else:
            raise OSError(f"未知操作：{action}")
    except OSError as error:
        result = SetupResult(False, error.strerror or str(error))
    except Exception as error:  # Report anything unexpected instead of failing silently.
        result = SetupResult(False, f"{type(error).__name__}: {error}")
    if result_path:
        Path(result_path).write_text(json.dumps(result.__dict__, ensure_ascii=False), encoding="utf-8")
    return 0 if result.ok else 1


# --- Launching the elevated copy, from the app ----------------------------------

class SHELLEXECUTEINFOW(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD), ("fMask", wintypes.ULONG), ("hwnd", wintypes.HWND),
        ("lpVerb", wintypes.LPCWSTR), ("lpFile", wintypes.LPCWSTR), ("lpParameters", wintypes.LPCWSTR),
        ("lpDirectory", wintypes.LPCWSTR), ("nShow", ctypes.c_int), ("hInstApp", wintypes.HINSTANCE),
        ("lpIDList", ctypes.c_void_p), ("lpClass", wintypes.LPCWSTR), ("hkeyClass", wintypes.HKEY),
        ("dwHotKey", wintypes.DWORD), ("hIconOrMonitor", wintypes.HANDLE), ("hProcess", wintypes.HANDLE),
    ]


class ElevatedSetup:
    """One running elevated setup. Poll finished() from a timer."""

    def __init__(self, action, hwnd=None):
        shell32 = ctypes.WinDLL("shell32", use_last_error=True)
        shell32.ShellExecuteExW.restype = wintypes.BOOL
        shell32.ShellExecuteExW.argtypes = [ctypes.POINTER(SHELLEXECUTEINFOW)]
        self._kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self._kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        self._kernel32.CloseHandle.argtypes = [wintypes.HANDLE]

        self.action = action
        handle, self._result_path = tempfile.mkstemp(prefix="noisemic-", suffix=".json")
        os.close(handle)
        if getattr(sys, "frozen", False):
            program, arguments = sys.executable, []
        else:
            pythonw = Path(sys.executable).with_name("pythonw.exe")
            program = str(pythonw if pythonw.exists() else sys.executable)
            arguments = [str(Path(__file__).resolve().with_name("main.py"))]
        arguments += [SETUP_FLAG, action, "--result", self._result_path]

        info = SHELLEXECUTEINFOW()
        info.cbSize = ctypes.sizeof(info)
        info.fMask = SEE_MASK_NOCLOSEPROCESS | SEE_MASK_NO_CONSOLE
        info.hwnd = hwnd
        info.lpVerb = "runas"
        info.lpFile = program
        info.lpParameters = " ".join(f'"{argument}"' for argument in arguments)
        info.nShow = SW_HIDE
        if not shell32.ShellExecuteExW(ctypes.byref(info)):
            code = ctypes.get_last_error()
            self._cleanup()
            if code == ERROR_CANCELLED:
                raise OSError(code, "已取消：没有获得管理员权限。")
            raise OSError(code, ctypes.FormatError(code).strip())
        self._process = info.hProcess

    def finished(self):
        """None while running, else the SetupResult."""
        code = wintypes.DWORD()
        if not self._kernel32.GetExitCodeProcess(self._process, ctypes.byref(code)) or code.value == STILL_ACTIVE:
            return None
        self._kernel32.CloseHandle(self._process)
        try:
            data = json.loads(Path(self._result_path).read_text(encoding="utf-8"))
            result = SetupResult(bool(data["ok"]), str(data["message"]), bool(data.get("reboot")))
        except (OSError, ValueError, KeyError):
            result = SetupResult(False, f"安装程序意外退出（代码 {code.value}）。")
        self._cleanup()
        return result

    def _cleanup(self):
        try:
            os.remove(self._result_path)
        except OSError:
            pass
