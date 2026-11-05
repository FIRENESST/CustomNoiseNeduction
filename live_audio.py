"""PortAudio devices and one live microphone processing session."""

from dataclasses import dataclass
import math

import numpy as np
import sounddevice as sd

from realtime_dsp import NoiseProfile, NoiseReducer


MIN_DB = -72.0


class AudioDeviceError(Exception):
    """A microphone or output device could not be opened."""


def audio_backends():
    hosts = sd.query_hostapis()
    capture_apis = {device["hostapi"] for device in sd.query_devices() if device["max_input_channels"]}
    preferred = ("Windows WASAPI", "Windows DirectSound", "MME")
    available = [(index, hosts[index]["name"]) for index in sorted(capture_apis)]
    return [
        (index, name)
        for backend in preferred
        for index, name in available
        if name == backend
    ] or available


def _level(samples):
    power = max(float(np.dot(samples, samples)) / len(samples), 1e-12)
    return min(0, max(MIN_DB, 10 * math.log10(power)))


@dataclass(frozen=True)
class Reading:
    input_db: float = MIN_DB
    output_db: float = MIN_DB
    learning_progress: float | None = None
    profile: NoiseProfile | None = None
    learning_silent: bool = False
    underruns: int = 0
    learning_request: int = 0

    @property
    def has_profile(self):
        return self.profile is not None


class LiveAudio:
    def __init__(self, input_device, output_device, channel, settings):
        self.input_device = input_device
        self.output_device = output_device
        self.channel = channel
        self.settings = settings
        self.stream = None
        self.reducer = None
        self.reading = Reading()
        self.failure = None
        self._learning_request = 0
        self._learning_started = 0
        self._underruns = 0

    def start(self, profile=None):
        microphone = sd.query_devices(self.input_device, "input")
        speaker = sd.query_devices(self.output_device, "output") if self.output_device is not None else None
        if speaker is not None and speaker["hostapi"] != microphone["hostapi"]:
            raise AudioDeviceError("请选择同一音频接口下的输入和输出设备。")

        host = sd.query_hostapis(microphone["hostapi"])["name"]
        extra = sd.WasapiSettings(auto_convert=True) if host == "Windows WASAPI" else None
        input_channels = min(2, microphone["max_input_channels"])
        if self.channel >= input_channels:
            raise AudioDeviceError("所选的麦克风通道不可用。")

        rates = [48000, int(microphone["default_samplerate"])]
        if speaker is not None:
            rates.append(int(speaker["default_samplerate"]))
        rates.append(44100)
        if profile is not None:
            rates = [profile.sample_rate]
        last_error = None
        for rate in dict.fromkeys(rates):
            self.reducer = NoiseReducer(rate)
            if profile is not None:
                self.reducer.use_profile(profile)
                self.reading = Reading(profile=profile)
            options = dict(samplerate=rate, blocksize=self.reducer.hop, dtype="float32", latency="low")
            try:
                if speaker is None:
                    self.stream = sd.InputStream(
                        device=self.input_device, channels=input_channels, extra_settings=extra,
                        callback=self._input_callback, **options,
                    )
                else:
                    self.stream = sd.Stream(
                        device=(self.input_device, self.output_device),
                        channels=(input_channels, min(2, speaker["max_output_channels"])),
                        extra_settings=(extra, extra), callback=self._duplex_callback, **options,
                    )
                self.stream.start()
                return
            except (sd.PortAudioError, ValueError) as error:
                last_error = error
                self.close()
        raise AudioDeviceError(str(last_error)) from last_error

    @property
    def active(self):
        return self.stream is not None and self.stream.active

    @property
    def sample_rate(self):
        return round(self.stream.samplerate)

    @property
    def delay_ms(self):
        latency = self.stream.latency
        io_latency = sum(latency) if isinstance(latency, tuple) else latency
        return 1000 * (io_latency + self.reducer.delay_frames / self.sample_rate)

    def request_learning(self):
        self._learning_request += 1

    @property
    def learning(self):
        reading = self.reading
        return (
            reading.learning_progress is not None
            or reading.learning_request != self._learning_request
        )

    def _process(self, indata, status):
        request = self._learning_request
        if request != self._learning_started:
            self.reducer.learn()
            self._learning_started = request
        if status.input_overflow or status.output_underflow:
            self._underruns += 1

        mono = indata.mean(axis=1) if self.channel == -1 else indata[:, self.channel]
        mono = np.clip(mono, -1.0, 1.0)
        if not np.isfinite(mono).all():
            np.nan_to_num(mono, copy=False)
        settings = self.settings
        result = self.reducer.process(mono, settings)
        if not settings.bypass and settings.strength and not self.reducer.has_profile:
            result.fill(0)
        np.clip(result, -1.0, 1.0, out=result)
        previous = self.reading
        self.reading = Reading(
            max(_level(mono), previous.input_db - 1.0),
            max(_level(result), previous.output_db - 1.0),
            self.reducer.learning_progress,
            self.reducer.profile,
            self.reducer.learning_silent,
            self._underruns,
            self._learning_started,
        )
        return result

    def _input_callback(self, indata, _frames, _time, status):
        try:
            self._process(indata, status)
        except Exception as error:
            self.failure = str(error)
            raise sd.CallbackAbort from error

    def _duplex_callback(self, indata, outdata, _frames, _time, status):
        try:
            outdata[:] = self._process(indata, status)[:, None]
        except Exception as error:
            self.failure = str(error)
            outdata.fill(0)
            raise sd.CallbackAbort from error

    def close(self):
        if self.stream is not None:
            try:
                self.stream.abort(ignore_errors=True)
            finally:
                self.stream.close(ignore_errors=True)
                self.stream = None
