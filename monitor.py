"""Qt drawing for the microphone level history."""

from collections import deque

from PyQt5.QtCore import QLineF, QRectF, Qt
from PyQt5.QtGui import QColor, QFont, QLinearGradient, QPainter, QPainterPath, QPen, QPixmap
from PyQt5.QtWidgets import QWidget

from live_audio import MIN_DB


INPUT_COLOR = "#83b5df"
OUTPUT_COLOR = "#64e3c0"
METER_INTERVAL_MS = 40
HISTORY_SECONDS = 6
POINTS = HISTORY_SECONDS * 1000 // METER_INTERVAL_MS + 1


def brand_mark():
    pixmap = QPixmap(64, 64)
    pixmap.setDevicePixelRatio(2)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setPen(QPen(QColor("#28504a"), 1))
    painter.setBrush(QColor("#183430"))
    painter.drawRoundedRect(QRectF(1, 1, 30, 30), 8, 8)
    painter.setPen(QPen(QColor(OUTPUT_COLOR), 1.5, Qt.SolidLine, Qt.RoundCap))
    for index, height in enumerate((5, 9, 14, 8, 4)):
        x = 16 + (index - 2) * 3.5
        painter.drawLine(QLineF(x, 16 - height / 2, x, 16 + height / 2))
    painter.end()
    return pixmap


class LevelHistory(QWidget):
    def __init__(self):
        super().__init__()
        self.setMinimumHeight(165)
        self.setAccessibleName("最近六秒的麦克风输入与输出电平")
        self.levels = deque(maxlen=POINTS)

    def append(self, input_db, output_db):
        self.levels.append((input_db, output_db))
        self.update()

    def clear(self):
        self.levels.clear()
        self.update()

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(QPen(QColor("#273646"), 1))
        painter.setBrush(QColor("#101821"))
        painter.drawRoundedRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), 8, 8)

        plot = QRectF(32, 14, self.width() - 46, self.height() - 39)
        axis_font = QFont("Consolas")
        axis_font.setPixelSize(10)
        painter.setFont(axis_font)
        for db in range(0, int(MIN_DB) - 1, -18):
            y = plot.top() + db / MIN_DB * plot.height()
            painter.setPen(QColor("#263747"))
            painter.drawLine(QLineF(plot.left(), y, plot.right(), y))
            painter.setPen(QColor("#92a6ba"))
            painter.drawText(QRectF(0, y - 7, 25, 14), Qt.AlignRight | Qt.AlignVCenter, str(db))
        for seconds in range(0, HISTORY_SECONDS + 1, 2):
            x = plot.left() + seconds / HISTORY_SECONDS * plot.width()
            painter.setPen(QColor("#1f2e3c"))
            painter.drawLine(QLineF(x, plot.top(), x, plot.bottom()))
            painter.setPen(QColor("#92a6ba"))
            painter.drawText(
                QRectF(x - 18, plot.bottom() + 4, 36, 15), Qt.AlignCenter,
                f"{seconds - HISTORY_SECONDS} s",
            )

        if len(self.levels) < 2:
            font = QFont(self.font())
            font.setPixelSize(13)
            painter.setFont(font)
            painter.setPen(QColor("#a3b5c6"))
            painter.drawText(plot, Qt.AlignCenter, "等待麦克风信号")
        else:
            paths = (QPainterPath(), QPainterPath())
            dx = plot.width() / (POINTS - 1)
            left = plot.right() - (len(self.levels) - 1) * dx
            for i, levels in enumerate(self.levels):
                x = left + i * dx
                for path, db in zip(paths, levels):
                    y = plot.top() + db / MIN_DB * plot.height()
                    if i == 0:
                        path.moveTo(x, y)
                    else:
                        path.lineTo(x, y)
            area = QPainterPath(paths[1])
            area.lineTo(plot.right(), plot.bottom())
            area.lineTo(left, plot.bottom())
            area.closeSubpath()
            shade = QLinearGradient(0, plot.top(), 0, plot.bottom())
            shade.setColorAt(0, QColor(100, 227, 192, 53))
            shade.setColorAt(1, QColor(100, 227, 192, 6))
            painter.fillPath(area, shade)
            painter.setBrush(Qt.NoBrush)
            for path, color, width in zip(paths, (INPUT_COLOR, OUTPUT_COLOR), (2.3, 1.45)):
                painter.setPen(QPen(QColor(color), width, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
                painter.drawPath(path)
        painter.end()
