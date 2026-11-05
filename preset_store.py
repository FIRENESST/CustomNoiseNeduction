"""Local noise preset storage with atomic updates."""

from dataclasses import dataclass
import json
from pathlib import Path
import sys

from PyQt5.QtCore import QIODevice, QLockFile, QSaveFile

from realtime_dsp import NoiseProfile


_APP_FILE = sys.executable if getattr(sys, "frozen", False) else __file__
PRESET_PATH = Path(_APP_FILE).resolve().with_name("presets.json")
MAX_PRESET_BYTES = 8 * 1024**2


class PresetError(Exception):
    """The preset library needs attention."""


class PresetNameTaken(PresetError):
    """The user can choose a new name or replace the existing preset."""


def validate_name(name):
    if not isinstance(name, str) or not 1 <= len(name) <= 40 or not name.isprintable():
        raise PresetError("预设名称需要 1–40 个可显示字符。")


@dataclass(frozen=True)
class MicSource:
    backend: str
    device: str
    channel: int

    def __post_init__(self):
        if (
            not isinstance(self.backend, str) or not self.backend.strip()
            or not isinstance(self.device, str) or not self.device.strip()
            or type(self.channel) is not int or self.channel not in (-1, 0, 1)
        ):
            raise ValueError("预设的麦克风信息无效。")


@dataclass(frozen=True)
class Preset:
    source: MicSource
    profile: NoiseProfile
    strength: int
    max_attenuation: int

    def __post_init__(self):
        if type(self.strength) is not int or not 0 <= self.strength <= 100:
            raise ValueError("预设的降噪强度无效。")
        if type(self.max_attenuation) is not int or not 6 <= self.max_attenuation <= 36:
            raise ValueError("预设的最大衰减无效。")

    def to_record(self):
        return {
            "source": {
                "backend": self.source.backend,
                "device": self.source.device,
                "channel": self.source.channel,
            },
            "sample_rate": self.profile.sample_rate,
            "power": self.profile.power.tolist(),
            "strength": self.strength,
            "max_attenuation": self.max_attenuation,
        }

    @classmethod
    def from_record(cls, record):
        return cls(
            MicSource(**record["source"]),
            NoiseProfile(record["sample_rate"], record["power"]),
            record["strength"],
            record["max_attenuation"],
        )


class PresetStore:
    def __init__(self, path=PRESET_PATH):
        self.path = Path(path)

    def load(self):
        try:
            with self.path.open("rb") as stream:
                data = stream.read(MAX_PRESET_BYTES + 1)
        except FileNotFoundError:
            return {}
        except OSError as error:
            raise PresetError(f"无法读取预设文件。\n{self.path}\n{error}") from error

        try:
            if len(data) > MAX_PRESET_BYTES:
                raise ValueError("预设文件超过 8 MiB。")
            document = json.loads(data)
            if not isinstance(document, dict) or document.get("version") != 1:
                raise ValueError("预设文件版本无效。")
            presets = {}
            for name, record in document["presets"].items():
                validate_name(name)
                presets[name] = Preset.from_record(record)
            return presets
        except (AttributeError, KeyError, TypeError, ValueError, OverflowError, RecursionError, PresetError) as error:
            raise PresetError(f"预设文件无法读取。\n{self.path}\n{error}") from error

    def change(self, name, preset, *, replace=False):
        """Save or remove one preset without dropping changes from other instances."""
        validate_name(name)
        lock = QLockFile(f"{self.path}.lock")
        if not lock.tryLock(300):
            raise PresetError("无法锁定预设文件。请确认项目目录可写，稍后重试。")
        try:
            presets = self.load()
            if preset is None:
                if name not in presets:
                    return presets
                del presets[name]
            else:
                if name in presets and not replace:
                    raise PresetNameTaken(name)
                presets[name] = preset
            self._write(presets)
            return presets
        finally:
            lock.unlock()

    def _write(self, presets):
        document = {"version": 1, "presets": {name: preset.to_record() for name, preset in presets.items()}}
        data = (json.dumps(document, ensure_ascii=False, allow_nan=False, separators=(",", ":")) + "\n").encode("utf-8")
        if len(data) > MAX_PRESET_BYTES:
            raise PresetError("预设文件已达到 8 MiB，请删除用不到的预设。")
        stream = QSaveFile(str(self.path))
        if not stream.open(QIODevice.WriteOnly):
            raise PresetError(f"无法保存预设。\n{stream.errorString()}")
        if stream.write(data) != len(data):
            stream.cancelWriting()
            raise PresetError(f"无法写入预设。\n{stream.errorString()}")
        if not stream.commit():
            raise PresetError(f"无法完成预设保存。\n{stream.errorString()}")
