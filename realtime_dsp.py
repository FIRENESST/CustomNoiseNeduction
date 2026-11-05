"""Causal, sample-profile noise reduction for a mono microphone stream."""

from dataclasses import dataclass
import math

import numpy as np
from numpy.fft import irfft, rfft


LEARNING_SECONDS = 1.5


def analysis_size(sample_rate):
    return 2 ** round(math.log2(sample_rate * (1024 / 48000)))


@dataclass(frozen=True)
class NoiseSettings:
    strength: int = 70
    max_attenuation: int = 24
    bypass: bool = False


@dataclass(frozen=True, eq=False)
class NoiseProfile:
    sample_rate: int
    power: np.ndarray

    def __post_init__(self):
        if type(self.sample_rate) is not int or not 1000 <= self.sample_rate <= 768000:
            raise ValueError("底噪采样率无效。")
        size = analysis_size(self.sample_rate)
        power = np.asarray(self.power)
        if (
            power.shape != (size // 2 + 1,) or power.dtype.kind not in "fiu"
            or not np.isfinite(power).all() or power.min() < 0
            or not 1e-9 < power.max() <= size**2
        ):
            raise ValueError("底噪频谱无效。")
        spectrum = power.astype(np.float32, copy=True)
        spectrum.setflags(write=False)
        object.__setattr__(self, "power", spectrum)


class NoiseReducer:
    def __init__(self, sample_rate):
        self.sample_rate = sample_rate
        self.size = analysis_size(sample_rate)
        self.hop = self.size // 4
        self.delay_frames = self.size - self.hop
        self._window = np.hanning(self.size + 1)[:-1].astype(np.float32)
        self._synthesis = self._window / np.square(self._window[::self.hop]).sum()
        self._history = np.zeros(self.size, dtype=np.float32)
        self._overlap = np.zeros_like(self._history)
        self._smoothed_power = np.zeros(self.size // 2 + 1, dtype=np.float32)
        self._gain = np.ones_like(self._smoothed_power)
        self._fade = np.linspace(0, 1, self.hop, dtype=np.float32)
        self._mix = 0.0

        self._learn_blocks = math.ceil(LEARNING_SECONDS * sample_rate / self.hop)
        self._warmup = 0
        self._observed = 0
        self._profile_sum = np.zeros_like(self._smoothed_power)
        self.profile = None
        self.learning_progress = None
        self.learning_silent = False

    @property
    def has_profile(self):
        return self.profile is not None

    def use_profile(self, profile):
        if profile.sample_rate != self.sample_rate:
            raise ValueError("底噪预设与当前采样率不匹配。")
        self.profile = profile
        self.learning_progress = None
        self.learning_silent = False

    def learn(self):
        # Wait until every sample in the analysis window was captured after the request.
        self._warmup = self.size // self.hop - 1
        self._observed = 0
        self._profile_sum.fill(0)
        self.learning_progress = 0.0
        self.learning_silent = False

    def _observe_noise(self, power):
        if self._warmup:
            self._warmup -= 1
            return
        self._profile_sum += power
        self._observed += 1
        if self._observed == self._learn_blocks:
            profile = self._profile_sum / self._learn_blocks
            self.learning_silent = bool(profile.max() <= 1e-9)
            if not self.learning_silent:
                self.use_profile(NoiseProfile(self.sample_rate, profile))
            self.learning_progress = None
        else:
            self.learning_progress = self._observed / self._learn_blocks

    def process(self, samples, settings):
        if samples.shape != (self.hop,):
            raise ValueError(f"Expected {self.hop} mono samples per audio block.")

        self._history[:-self.hop] = self._history[self.hop:]
        self._history[-self.hop:] = samples
        spectrum = rfft(self._history * self._window)
        power = np.square(np.abs(spectrum))
        if self.learning_progress is not None:
            self._observe_noise(power)

        if self.has_profile:
            self._smoothed_power *= 0.52
            self._smoothed_power += 0.48 * power
            reference = np.maximum(self._smoothed_power, 0.65 * power)
            reference = np.maximum(reference, np.finfo(np.float32).tiny)
            subtraction = (4.0 * (settings.strength / 100) ** 1.5) * self.profile.power
            floor = 10 ** (-settings.max_attenuation / 10)
            gain = np.sqrt(np.maximum(1 - np.minimum(subtraction, reference) / reference, floor))
            gain[1:-1] = 0.12 * gain[:-2] + 0.76 * gain[1:-1] + 0.12 * gain[2:]
            change = gain - self._gain
            # Open quickly for speech onsets and close more gradually between them.
            self._gain += 0.24 * change + 0.38 * np.maximum(change, 0)
            spectrum *= self._gain

        self._overlap += irfft(spectrum, n=self.size) * self._synthesis
        dry = self._history[:self.hop]
        wet = self._overlap[:self.hop]
        target = float(self.has_profile and settings.strength > 0 and not settings.bypass)
        if target == self._mix:
            output = (wet if target else dry).copy()
        else:
            blend = self._mix + (target - self._mix) * self._fade
            output = dry + blend * (wet - dry)
            self._mix = target

        self._overlap[:-self.hop] = self._overlap[self.hop:]
        self._overlap[-self.hop:] = 0
        return output
