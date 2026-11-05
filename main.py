"""Portable microphone noise reduction for Windows."""

import sys

from PyQt5.QtCore import QPointF, QSignalBlocker, QTimer, Qt, QUrl
from PyQt5.QtGui import QColor, QDesktopServices, QFont, QIcon, QPainter, QPen, QPolygonF
from PyQt5.QtWidgets import (
    QApplication, QButtonGroup, QComboBox, QFrame, QHBoxLayout, QInputDialog, QLabel, QMessageBox,
    QProgressBar, QPushButton, QSizePolicy, QSlider, QStyle, QStyleOptionComboBox, QStylePainter,
    QVBoxLayout, QWidget,
)
import sounddevice as sd

from live_audio import AudioDeviceError, LiveAudio, MIN_DB, audio_backends
from monitor import INPUT_COLOR, METER_INTERVAL_MS, OUTPUT_COLOR, LevelHistory, brand_mark
from preset_store import MicSource, PRESET_PATH, Preset, PresetError, PresetNameTaken, PresetStore
from realtime_dsp import LEARNING_SECONDS, NoiseSettings


APP_NAME = "便携式降噪软件"
WINDOW_SIZE = (800, 485)
MIN_WINDOW_SIZE = (700, 435)


STYLE = """
QWidget#workbench, QMessageBox, QInputDialog { background: #101720; }
QLabel { color: #e7eff6; background: transparent; }
QLabel#brand { font-size: 21px; font-weight: 700; }
QLabel#heading { font-size: 15px; font-weight: 600; }
QLabel#muted, QLabel#status { color: #a3b5c6; font-size: 11px; }
QLabel#db { color: #bac9d8; font-family: Consolas; font-size: 11px; }
QLabel#badge, QLabel#value {
    color: #8ee5cf; background: #1b3635; border: 1px solid #294947;
    border-radius: 6px; padding: 3px 7px; font-size: 11px;
}
QLabel#value { padding: 1px 5px; }
QFrame#panel { background: #192330; border: 1px solid #293849; border-radius: 10px; }
QFrame#rule { background: #2b3948; }
QPushButton {
    color: #e5eff8; background: #243448; border: 1px solid #34465a; border-radius: 6px;
    padding: 4px 9px; min-height: 16px; font-size: 12px; font-weight: 600;
}
QPushButton:hover { background: #2c4157; border-color: #4c6881; }
QPushButton:pressed { background: #1e2d3f; }
QPushButton:focus { border-color: #81b1d2; }
QPushButton:disabled { color: #73879a; background: #1d2836; border-color: #2a3747; }
QPushButton#primary { color: #0d2e29; background: #64e3c0; border-color: #64e3c0; }
QPushButton#primary:hover { background: #88efd3; }
QPushButton#primary:pressed { background: #4bcbab; }
QPushButton#primary:focus { border-color: #d1fff3; }
QPushButton#primary:disabled { color: #819991; background: #233b3a; border-color: #2a4441; }
QPushButton#mode, QPushButton#tool { padding: 3px 8px; min-height: 14px; font-size: 11px; }
QPushButton#tool { color: #adbdcc; background: #1b2a3a; }
QPushButton#tool:enabled:hover { background: #2c4157; }
QPushButton#tool:disabled { color: #647789; background: #1d2836; }
QPushButton#mode:checked { background: #244342; border-color: #467773; color: #acf5e1; }
QComboBox, QLineEdit {
    color: #e4eef7; background: #111c27; border: 1px solid #37495b;
    border-radius: 6px; padding: 3px 19px 3px 7px; min-height: 16px;
}
QComboBox:focus, QLineEdit:focus { border-color: #64e3c0; }
QComboBox:disabled { color: #8498ab; border-color: #2b3a4c; }
QComboBox::drop-down { width: 20px; border: 0; }
QComboBox::down-arrow { width: 0; height: 0; }
QComboBox QAbstractItemView {
    background: #182533; color: #e4eef7; border: 1px solid #37495b;
    selection-background-color: #2e514f; selection-color: #effcf8; outline: 0;
}
QSlider { min-height: 16px; }
QSlider::groove:horizontal { background: #34475a; border-radius: 2px; height: 4px; }
QSlider::sub-page:horizontal { background: #57bba5; border-radius: 2px; }
QSlider::handle:horizontal {
    background: #8fead2; border: 1px solid #8fead2;
    width: 9px; margin: -3px 0; border-radius: 5px;
}
QSlider::handle:horizontal:hover { background: #c0f8e9; }
QProgressBar { background: #2a3b4d; border: 0; border-radius: 3px; }
QProgressBar::chunk { background: #64e3c0; border-radius: 3px; }
QProgressBar#inputMeter::chunk { background: #83b5df; }
"""


def label(text, style=""):
    widget = QLabel(text)
    widget.setTextFormat(Qt.PlainText)
    widget.setObjectName(style)
    return widget


def button(text, style=""):
    widget = QPushButton(text)
    widget.setObjectName(style)
    return widget


def panel():
    card = QFrame()
    card.setObjectName("panel")
    layout = QVBoxLayout(card)
    layout.setContentsMargins(12, 10, 12, 11)
    layout.setSpacing(7)
    return card, layout


def progress_bar(maximum, height, style=""):
    bar = QProgressBar()
    bar.setObjectName(style)
    bar.setRange(0, maximum)
    bar.setValue(0)
    bar.setFixedHeight(height)
    bar.setTextVisible(False)
    return bar


def cable_line(name, port):
    base, _, vendor = name.casefold().partition(" (")
    if "vb-audio" in vendor and base.startswith("cable") and base.endswith(f" {port}"):
        return base.rsplit(" ", 1)[0]
    return None


class CompactCombo(QComboBox):
    def __init__(self, name):
        super().__init__()
        self.setAccessibleName(name)
        self.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.setMinimumContentsLength(4)
        self.currentTextChanged.connect(self.setToolTip)

    def paintEvent(self, _event):
        painter = QStylePainter(self)
        option = QStyleOptionComboBox()
        self.initStyleOption(option)
        field = self.style().subControlRect(QStyle.CC_ComboBox, option, QStyle.SC_ComboBoxEditField, self)
        option.currentText = self.fontMetrics().elidedText(option.currentText, Qt.ElideRight, max(0, field.width() - 2))
        painter.drawComplexControl(QStyle.CC_ComboBox, option)
        painter.drawControl(QStyle.CE_ComboBoxLabel, option)
        painter.setRenderHint(QPainter.Antialiasing)
        color = QColor("#a3b5c6" if self.isEnabled() else "#586b7d")
        painter.setPen(QPen(color, 1.25, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        x, y = self.width() - 10, self.height() / 2
        painter.drawPolyline(QPolygonF([
            QPointF(x - 3, y - 1.5), QPointF(x, y + 1.5), QPointF(x + 3, y - 1.5),
        ]))
        painter.end()


class Workbench(QWidget):
    def __init__(self, preset_path=PRESET_PATH):
        super().__init__()
        self.session = None
        self.stream_info = ""
        self.preset_store = PresetStore(preset_path)
        self.presets = {}
        self.last_capture = None
        self.devices = {}
        self.setObjectName("workbench")
        self.setWindowTitle(APP_NAME)
        self.setWindowIcon(QIcon(brand_mark()))
        self.setMinimumSize(*MIN_WINDOW_SIZE)
        self._build_ui()
        self.timer = QTimer(self)
        self.timer.setInterval(METER_INTERVAL_MS)
        self.timer.timeout.connect(self._poll)
        self._load_backends()
        self._load_presets()

    def _build_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(14, 11, 14, 9)
        outer.setSpacing(9)

        header = QHBoxLayout()
        header.setSpacing(9)
        mark = QLabel()
        mark.setPixmap(brand_mark())
        header.addWidget(mark)
        identity = QVBoxLayout()
        identity.setSpacing(0)
        identity.addWidget(label(APP_NAME, "brand"))
        identity.addWidget(label("实时麦克风降噪", "muted"))
        header.addLayout(identity)
        header.addStretch()
        self.state = label("待启动", "badge")
        header.addWidget(self.state, 0, Qt.AlignVCenter)
        self.run_button = button("启动麦克风", "primary")
        self.run_button.setMinimumWidth(90)
        self.run_button.clicked.connect(self._toggle_session)
        header.addWidget(self.run_button)
        outer.addLayout(header)

        body = QHBoxLayout()
        body.setSpacing(9)
        body.addWidget(self._build_monitor(), 1)
        body.addWidget(self._build_settings())
        outer.addLayout(body, 1)

        footer = QHBoxLayout()
        self.status = label("选择设备后启动麦克风。", "status")
        self.status.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        footer.addWidget(self.status, 1)
        self.transport = label("本地处理", "muted")
        self.transport.setToolTip("设备缓冲和算法延迟的估计值")
        footer.addWidget(self.transport)
        outer.addLayout(footer)

    def _build_monitor(self):
        card, layout = panel()
        heading = QHBoxLayout()
        heading.addWidget(label("实时信号", "heading"))
        heading.addStretch()
        self.clean_mode = button("降噪", "mode")
        self.raw_mode = button("原音", "mode")
        modes = QButtonGroup(self)
        for control in (self.clean_mode, self.raw_mode):
            control.setCheckable(True)
            modes.addButton(control)
            control.clicked.connect(self._settings_changed)
            heading.addWidget(control)
        self.clean_mode.setChecked(True)
        layout.addLayout(heading)

        meters = QHBoxLayout()
        meters.setSpacing(13)
        self.input_level, self.input_value = self._add_meter(meters, "麦克风输入", INPUT_COLOR, "inputMeter")
        self.output_level, self.output_value = self._add_meter(meters, "处理后输出", OUTPUT_COLOR, "outputMeter")
        layout.addLayout(meters)
        self.history = LevelHistory()
        layout.addWidget(self.history, 1)
        layout.addWidget(label("最近 6 秒 · dBFS", "muted"))
        return card

    def _add_meter(self, layout, title, color, style):
        group = QVBoxLayout()
        group.setSpacing(3)
        row = QHBoxLayout()
        row.setSpacing(4)
        dot = QFrame()
        dot.setFixedSize(6, 6)
        dot.setStyleSheet(f"background: {color}; border-radius: 3px;")
        row.addWidget(dot)
        row.addWidget(label(title))
        row.addStretch()
        value = label("— dBFS", "db")
        row.addWidget(value)
        group.addLayout(row)
        bar = progress_bar(round(-MIN_DB * 10), 6, style)
        bar.setAccessibleName(title)
        group.addWidget(bar)
        layout.addLayout(group, 1)
        return bar, value

    def _build_settings(self):
        card, layout = panel()
        card.setFixedWidth(296)
        devices = QHBoxLayout()
        devices.addWidget(label("设备", "heading"))
        devices.addStretch()
        route_button = button("应用接入", "tool")
        route_button.clicked.connect(self._show_routing)
        devices.addWidget(route_button)
        layout.addLayout(devices)

        self.input_box = self._add_combo(layout, "麦克风")
        self.output_box = self._add_combo(layout, "输出")
        options = QHBoxLayout()
        options.setSpacing(9)
        self.backend_box = self._add_combo(options, "接口", 27)
        self.channel_box = self._add_combo(options, "通道", 27)
        self.backend_box.setAccessibleName("音频接口")
        self.channel_box.setAccessibleName("麦克风输入通道")
        layout.addLayout(options)

        self.backend_box.currentIndexChanged.connect(self._populate_devices)
        self.input_box.currentIndexChanged.connect(self._populate_channels)
        self.channel_box.currentIndexChanged.connect(self._refresh_controls)

        rule = QFrame()
        rule.setObjectName("rule")
        rule.setFixedHeight(1)
        layout.addWidget(rule)

        profiles = QHBoxLayout()
        profiles.addWidget(label("底噪预设", "heading"))
        profiles.addStretch()
        self.profile_state = label("待学习", "value")
        profiles.addWidget(self.profile_state)
        layout.addLayout(profiles)

        picker = QHBoxLayout()
        picker.setSpacing(5)
        self.preset_box = CompactCombo("底噪预设")
        self.preset_box.currentIndexChanged.connect(self._preset_selected)
        picker.addWidget(self.preset_box, 1)
        self.delete_button = button("删除", "tool")
        self.delete_button.setToolTip("删除选中的底噪预设")
        self.delete_button.clicked.connect(self._delete_preset)
        picker.addWidget(self.delete_button)
        layout.addLayout(picker)

        actions = QHBoxLayout()
        actions.setSpacing(5)
        self.learn_button = button("学习底噪")
        self.learn_button.clicked.connect(self._learn)
        actions.addWidget(self.learn_button, 1)
        self.save_button = button("保存预设")
        self.save_button.clicked.connect(self._save_preset)
        actions.addWidget(self.save_button, 1)
        layout.addLayout(actions)

        self.learning_bar = progress_bar(100, 4)
        self.learning_bar.setAccessibleName("底噪采样进度")
        layout.addWidget(self.learning_bar)
        self.profile_hint = label("", "muted")
        self.profile_hint.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        layout.addWidget(self.profile_hint)

        defaults = NoiseSettings()
        self.strength = self._add_slider(layout, "降噪强度", 0, 100, defaults.strength, "%")
        self.attenuation = self._add_slider(layout, "最大衰减", 6, 36, defaults.max_attenuation, " dB")
        layout.addStretch()
        return card

    def _add_combo(self, layout, name, label_width=45):
        row = QHBoxLayout()
        row.setSpacing(5)
        title = label(name, "muted")
        title.setFixedWidth(label_width)
        row.addWidget(title)
        combo = CompactCombo(name)
        title.setBuddy(combo)
        row.addWidget(combo, 1)
        layout.addLayout(row)
        return combo

    def _add_slider(self, layout, name, low, high, default, suffix):
        row = QHBoxLayout()
        row.setSpacing(8)
        title = label(name)
        title.setFixedWidth(54)
        row.addWidget(title)
        slider = QSlider(Qt.Horizontal)
        slider.setAccessibleName(name)
        title.setBuddy(slider)
        slider.setRange(low, high)
        slider.setValue(default)
        row.addWidget(slider, 1)
        value = label(f"{default}{suffix}", "value")
        value.setAlignment(Qt.AlignCenter)
        value.setFixedWidth(45)
        slider.valueChanged.connect(lambda v: value.setText(f"{v}{suffix}"))
        slider.valueChanged.connect(self._settings_changed)
        row.addWidget(value)
        layout.addLayout(row)
        return slider

    def _load_backends(self):
        try:
            self.devices = {device["index"]: device for device in sd.query_devices()}
            with QSignalBlocker(self.backend_box):
                for index, name in audio_backends():
                    self.backend_box.addItem(name.removeprefix("Windows "), index)
            self.backend_box.setToolTip(self.backend_box.currentText())
            self._populate_devices()
        except sd.PortAudioError as error:
            self._set_status("读取音频设备失败。", str(error))
        self._refresh_controls()

    def _populate_devices(self):
        backend = self.backend_box.currentData()
        with QSignalBlocker(self.input_box), QSignalBlocker(self.output_box):
            self.input_box.clear()
            self.output_box.clear()
            self.output_box.addItem("仅显示电平", None)
            if backend is not None:
                for device in self.devices.values():
                    if device["hostapi"] != backend:
                        continue
                    if device["max_input_channels"]:
                        self.input_box.addItem(device["name"], device["index"])
                    if device["max_output_channels"]:
                        self.output_box.addItem(device["name"], device["index"])
                default = sd.query_hostapis(backend)["default_input_device"]
                index = max(0, self.input_box.findData(default))
                if cable_line(self.input_box.itemText(index), "output") is not None:
                    index = next(
                        (i for i in range(self.input_box.count()) if not any(
                            name in self.input_box.itemText(i).casefold()
                            for name in ("virtual", "streaming", "vb-audio", "voicemeeter")
                        )),
                        index,
                    )
                self.input_box.setCurrentIndex(index)
        self._populate_channels()

    def _populate_channels(self):
        microphone = self.input_box.currentData()
        with QSignalBlocker(self.channel_box):
            self.channel_box.clear()
            if microphone is not None:
                self.channel_box.addItem("通道 1", 0)
                if self.devices[microphone]["max_input_channels"] >= 2:
                    self.channel_box.addItem("通道 2", 1)
                    self.channel_box.addItem("混合 1 / 2", -1)
        self.channel_box.setToolTip(self.channel_box.currentText())
        self.input_box.setToolTip(self.input_box.currentText())
        self.output_box.setToolTip(self.output_box.currentText())
        self._refresh_controls()

    def _selected_source(self):
        channel = self.channel_box.currentData()
        if self.input_box.currentData() is None or channel is None:
            return None
        return MicSource(self.backend_box.currentText(), self.input_box.currentText(), channel)

    def _selected_preset(self):
        return self.presets.get(self.preset_box.currentData())

    def _load_presets(self):
        try:
            self.presets = self.preset_store.load()
        except PresetError as error:
            self._show_presets()
            self._set_status("预设库读取失败。", str(error))
        else:
            self._show_presets()

    def _show_presets(self, selected=None):
        with QSignalBlocker(self.preset_box):
            self.preset_box.clear()
            self.preset_box.addItem("启动时自动学习", None)
            for name in sorted(self.presets, key=str.casefold):
                self.preset_box.addItem(name, name)
                preset = self.presets[name]
                description = f"{preset.source.device} · {preset.profile.sample_rate / 1000:g} kHz"
                self.preset_box.setItemData(self.preset_box.count() - 1, description, Qt.ToolTipRole)
            self.preset_box.setCurrentIndex(max(0, self.preset_box.findData(selected)))
        self.preset_box.setToolTip(self.preset_box.currentText())
        self._refresh_controls()

    def _preset_selected(self):
        if self.session is not None:
            return
        preset = self._selected_preset()
        if preset is not None:
            self.strength.setValue(preset.strength)
            self.attenuation.setValue(preset.max_attenuation)
            source = preset.source
            index = self.backend_box.findText(source.backend)
            backend = self.backend_box.itemData(index) if index >= 0 else None
            device = next(
                (d for d in self.devices.values()
                 if d["hostapi"] == backend and d["name"] == source.device
                 and d["max_input_channels"] >= (1 if source.channel == 0 else 2)),
                None,
            )
            if device is not None:
                self.backend_box.setCurrentIndex(index)
                self.input_box.setCurrentIndex(self.input_box.findData(device["index"]))
                self.channel_box.setCurrentIndex(self.channel_box.findData(source.channel))
        self._refresh_controls()

    def _profile_to_save(self):
        if self.session is not None:
            return None if self.session.learning else self.session.reading.profile
        source, preset = self._selected_source(), self._selected_preset()
        if preset is not None and preset.source == source:
            return preset.profile
        if self.last_capture is not None and self.last_capture[0] == source:
            return self.last_capture[1]
        return None

    def _save_preset(self):
        profile, source = self._profile_to_save(), self._selected_source()
        if profile is None or source is None:
            return
        name, accepted = QInputDialog.getText(
            self, "保存底噪预设", "预设名称", text=self.preset_box.currentData() or "",
        )
        if not accepted:
            return
        name = name.strip()
        preset = Preset(source, profile, self.strength.value(), self.attenuation.value())
        try:
            try:
                self.presets = self.preset_store.change(name, preset)
            except PresetNameTaken:
                if not self._confirm(f"覆盖已有的「{name}」预设？"):
                    return
                self.presets = self.preset_store.change(name, preset, replace=True)
        except PresetError as error:
            self._warn(str(error))
            return
        self._show_presets(name)
        self._set_status(f"已保存预设「{name}」。")

    def _delete_preset(self):
        name = self.preset_box.currentData()
        if name is None or not self._confirm(f"删除「{name}」预设？"):
            return
        try:
            self.presets = self.preset_store.change(name, None)
        except PresetError as error:
            self._warn(str(error))
            return
        self._show_presets()
        self._set_status(f"已删除预设「{name}」。")

    def _show_routing(self):
        message = (
            "在便携式降噪软件中选择物理麦克风，把输出送到虚拟音频线的播放端，例如 CABLE Input。\n\n"
            "在会议或直播软件中，把麦克风切换到同一条线的录音端，例如 CABLE Output。"
            "跟随系统默认输入的应用，可在 Windows 声音设置中将这个录音端设为默认。\n\n"
            "保持软件运行并选择「降噪」。连接新设备后，重新打开软件可刷新设备列表。"
        )
        dialog = QMessageBox(QMessageBox.Information, "应用接入", message, QMessageBox.NoButton, self)
        dialog.setTextFormat(Qt.PlainText)
        settings = dialog.addButton("打开 Windows 声音设置", QMessageBox.ActionRole)
        dialog.addButton("关闭", QMessageBox.RejectRole)
        dialog.exec_()
        if dialog.clickedButton() == settings and not QDesktopServices.openUrl(QUrl("ms-settings:sound")):
            self._warn("请在 Windows 的「设置 → 系统 → 声音」中选择输入设备。")

    def _settings(self):
        return NoiseSettings(self.strength.value(), self.attenuation.value(), self.raw_mode.isChecked())

    def _settings_changed(self):
        if self.session is not None:
            self.session.settings = self._settings()

    def _refresh_controls(self):
        running = self.session is not None
        source, preset = self._selected_source(), self._selected_preset()
        matched = preset is None or preset.source == source
        profile = self._profile_to_save()
        self.run_button.setEnabled(running or (source is not None and matched))
        self.run_button.setText("停止麦克风" if running else "启动麦克风")
        self.backend_box.setEnabled(not running and self.backend_box.count() > 1)
        self.input_box.setEnabled(not running and source is not None)
        self.channel_box.setEnabled(not running and self.channel_box.count() > 1)
        self.output_box.setEnabled(not running)
        self.preset_box.setEnabled(not running)
        self.delete_button.setEnabled(not running and preset is not None)
        self.learn_button.setEnabled(running and not self.session.learning)
        self.save_button.setEnabled(profile is not None)
        if not running:
            self.learning_bar.setValue(100 if profile is not None and matched else 0)
            if not matched:
                self.profile_state.setText("设备待匹配")
                self._set_profile_hint("请选择对应麦克风，或改用自动学习。", preset.source.device)
            elif preset is not None:
                self.profile_state.setText("已选预设")
                self._set_profile_hint(f"启动后直接使用预设 · {preset.profile.sample_rate / 1000:g} kHz")
            elif profile is not None:
                self.profile_state.setText("已学习")
                self._set_profile_hint("本次底噪可保存为预设。")
            else:
                self.profile_state.setText("待学习")
                self._set_profile_hint(f"启动后保持安静约 {LEARNING_SECONDS:g} 秒。")

    def _set_profile_hint(self, message, details=""):
        self.profile_hint.setText(message)
        self.profile_hint.setToolTip(details or message)

    def _set_status(self, message, details=""):
        self.status.setText(message)
        self.status.setToolTip(details or message)

    def _warn(self, message):
        dialog = QMessageBox(QMessageBox.Warning, APP_NAME, message, QMessageBox.Ok, self)
        dialog.setTextFormat(Qt.PlainText)
        dialog.exec_()

    def _confirm(self, message):
        dialog = QMessageBox(QMessageBox.Question, APP_NAME, message, QMessageBox.Yes | QMessageBox.No, self)
        dialog.setTextFormat(Qt.PlainText)
        dialog.setDefaultButton(QMessageBox.No)
        return dialog.exec_() == QMessageBox.Yes

    def _toggle_session(self):
        if self.session is not None:
            self._stop()
            return
        microphone = self.input_box.currentData()
        source, preset = self._selected_source(), self._selected_preset()
        if source is None or (preset is not None and preset.source != source):
            return
        cable = cable_line(source.device, "output")
        if cable is not None and cable == cable_line(self.output_box.currentText(), "input"):
            self._warn("请在「麦克风」中选择物理麦克风。虚拟音频线的录音端供目标软件使用。")
            return
        session = LiveAudio(microphone, self.output_box.currentData(), source.channel, self._settings())
        try:
            session.start(preset.profile if preset is not None else None)
        except (AudioDeviceError, sd.PortAudioError, ValueError) as error:
            self._set_status("无法启动音频设备。", str(error))
            guidance = "请检查 Windows 麦克风权限、设备连接和音频接口设置。"
            if preset is not None:
                guidance += f"\n当前预设使用 {preset.profile.sample_rate / 1000:g} kHz，可改用自动学习。"
            self._warn(f"{guidance}\n\n{error}")
            return

        self.session = session
        self.history.clear()
        self.stream_info = f"{self.backend_box.currentText()} · {session.sample_rate / 1000:g} kHz"
        if session.output_device is not None:
            self.stream_info += f" · 缓冲约 {session.delay_ms:.0f} ms"
        else:
            self.stream_info += " · 仅显示电平"
        if preset is None:
            self._learn()
        self._refresh_controls()
        self.timer.start()
        self._poll()

    def _learn(self):
        if self.session is not None and not self.session.learning:
            with QSignalBlocker(self.preset_box):
                self.preset_box.setCurrentIndex(0)
            self.preset_box.setToolTip(self.preset_box.currentText())
            self.session.request_learning()
            self._refresh_controls()

    def _update_levels(self, input_db, output_db, active):
        for db, bar, value in (
            (input_db, self.input_level, self.input_value),
            (output_db, self.output_level, self.output_value),
        ):
            bar.setValue(round((db - MIN_DB) * 10))
            value.setText(f"{db:.1f} dBFS" if active and db > MIN_DB else ("≤ −72 dBFS" if active else "— dBFS"))

    def _poll(self):
        session = self.session
        if session is None:
            return
        try:
            active = session.active
        except sd.PortAudioError as error:
            session.failure = str(error)
            active = False
        if not active:
            reason = session.failure or "请检查设备连接后重新启动。"
            self._stop("音频流已中断。", reason)
            return

        reading = session.reading
        self._update_levels(reading.input_db, reading.output_db, active=True)
        self.history.append(reading.input_db, reading.output_db)
        learning = session.learning
        self.learn_button.setEnabled(not learning)
        self.save_button.setEnabled(not learning and reading.has_profile)
        if learning:
            self.state.setText("学习中")
            self.profile_state.setText("采样中")
            self.learning_bar.setValue(round(100 * (reading.learning_progress or 0)))
            self._set_profile_hint("保持安静，采集环境底噪。")
            self._set_status("正在采集环境底噪，请保持安静…")
        else:
            self.learning_bar.setValue(100 if reading.has_profile else 0)
            preset = self._selected_preset()
            bypass = self.raw_mode.isChecked() or self.strength.value() == 0
            if reading.has_profile:
                self.profile_state.setText("使用预设" if preset is not None else "已学习")
                self._set_profile_hint("停止麦克风后可切换预设。" if preset is not None else "当前底噪可保存为预设。")
            else:
                self.profile_state.setText("待学习")
                self._set_profile_hint("确认麦克风电平后，点击学习底噪。")
            if reading.learning_silent:
                self._set_status("检测到静音。确认麦克风电平后，可重新学习底噪。")
            elif bypass:
                self._set_status("麦克风已连接，当前输出原音。")
            elif reading.has_profile:
                self._set_status("实时降噪中，可切换原音对比。")
            else:
                self._set_status("学习底噪后开始输出降噪声音。")
            if bypass:
                self.state.setText("原音直通")
            else:
                self.state.setText("降噪中" if reading.has_profile else "等待底噪")

        info = self.stream_info
        if reading.underruns:
            info += f" · 缓冲异常 {reading.underruns} 次"
        self.transport.setText(info)

    def _stop(self, message="麦克风已停止。", details=""):
        self.timer.stop()
        if self.session is not None:
            self.session.close()
            profile = self.session.reading.profile
            source = self._selected_source()
            if profile is not None and source is not None:
                self.last_capture = (source, profile)
            self.session = None
        self.history.clear()
        self._update_levels(MIN_DB, MIN_DB, active=False)
        self.state.setText("待启动")
        self.transport.setText("本地处理")
        self._set_status(message, details)
        self._refresh_controls()

    def closeEvent(self, event):
        self._stop()
        event.accept()


def main():
    QApplication.setAttribute(Qt.AA_EnableHighDpiScaling)
    QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps)
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationDisplayName(APP_NAME)
    app.setStyle("Fusion")
    font = QFont("Microsoft YaHei UI")
    font.setPixelSize(12)
    app.setFont(font)
    app.setStyleSheet(STYLE)
    window = Workbench()
    available = app.primaryScreen().availableGeometry()
    window.resize(min(WINDOW_SIZE[0], available.width() - 24), min(WINDOW_SIZE[1], available.height() - 36))
    window.move(available.center() - window.rect().center())
    window.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
