# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path
import sys

from PyInstaller.utils.win32.versioninfo import (
    FixedFileInfo, StringFileInfo, StringStruct, StringTable, VarFileInfo, VarStruct, VSVersionInfo,
)
from PyQt5.QtWidgets import QApplication


project = Path(SPECPATH).resolve()
sys.path.insert(0, str(project))
from main import APP_NAME
from monitor import brand_mark


qt_app = QApplication.instance() or QApplication([])
icon = Path(workpath) / "app.ico"
if not brand_mark().save(str(icon), "ICO"):
    raise RuntimeError("Could not generate the application icon.")

# The 便携式降噪麦克风 driver travels inside the EXE. Release builds take the
# Microsoft-signed package from driver\signed; driver\package is the local,
# unsigned build from driver\build.ps1 and only installs on test machines.
driver_files = ("NoiseMic.inf", "NoiseMic.sys", "noisemic.cat")
driver_package = next(
    (folder for folder in (project / "driver" / "signed", project / "driver" / "package")
     if all((folder / name).is_file() for name in driver_files)),
    None,
)
if driver_package is None:
    print("WARNING: no driver package found; the EXE will not offer the noise reduction microphone.")
    driver_datas = []
else:
    print(f"Bundling driver package from {driver_package}")
    driver_datas = [(str(driver_package / name), "driver") for name in driver_files]

version = VSVersionInfo(
    ffi=FixedFileInfo(
        filevers=(1, 0, 0, 0), prodvers=(1, 0, 0, 0), mask=0x3F, flags=0,
        OS=0x40004, fileType=1, subtype=0, date=(0, 0),
    ),
    kids=[
        StringFileInfo([StringTable("080404B0", [
            StringStruct("FileDescription", APP_NAME),
            StringStruct("ProductName", APP_NAME),
            StringStruct("OriginalFilename", f"{APP_NAME}.exe"),
            StringStruct("FileVersion", "1.0.0"),
            StringStruct("ProductVersion", "1.0.0"),
        ])]),
        VarFileInfo([VarStruct("Translation", [0x0804, 1200])]),
    ],
)

analysis = Analysis(
    [str(project / "main.py")],
    pathex=[str(project)],
    datas=driver_datas,
    excludes=[
        "scipy", "tkinter", "ssl", "_ssl", "_hashlib",
        "numpy.f2py", "numpy.testing", "numpy.random", "numpy.ma", "numpy.polynomial",
        "PyQt5.QtDBus", "PyQt5.QtNetwork", "PyQt5.QtQml", "PyQt5.QtQuick",
        "PyQt5.QtSvg", "PyQt5.QtWebSockets",
    ],
    # NumPy uses docstrings while installing its array functions.
    optimize=1,
)


def needed_binary(entry):
    name = entry[0].replace("\\", "/").lower()
    filename = name.rsplit("/", 1)[-1]
    if name.startswith("pyqt5/") and "/plugins/" in name:
        return name.endswith("/platforms/qwindows.dll")
    if name.startswith("pyqt5/"):
        # The workbench paints widgets with QPainter and does not create GL contexts.
        if filename.startswith("qt5") and filename.endswith(".dll"):
            return filename in {"qt5core.dll", "qt5gui.dll", "qt5widgets.dll"}
        return filename not in {
            "libegl.dll", "libglesv2.dll", "opengl32sw.dll", "d3dcompiler_47.dll",
        }
    if name.startswith("_sounddevice_data/portaudio-binaries/"):
        return filename in {"libportaudio64bit.dll", "libportaudio64bit-asio.dll"}
    if filename in {"libcrypto-3-x64.dll", "libssl-3-x64.dll"}:
        return False
    return True


analysis.binaries = [entry for entry in analysis.binaries if needed_binary(entry)]
analysis.datas = [
    entry for entry in analysis.datas
    if not entry[0].replace("\\", "/").lower().startswith("pyqt5/qt5/translations/")
]
archive = PYZ(analysis.pure)
exe = EXE(
    archive, analysis.scripts, analysis.binaries, analysis.datas, [],
    name=APP_NAME,
    console=False,
    upx=False,
    icon=str(icon),
    version=version,
)
