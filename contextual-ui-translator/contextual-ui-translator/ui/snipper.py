"""Full-desktop region selector: multi-monitor and High-DPI aware."""
from __future__ import annotations

import logging
from typing import List, Optional, Tuple

from PIL import Image
from PyQt5.QtCore import QPoint, QRect, Qt, pyqtSignal
from PyQt5.QtGui import QColor, QFont, QGuiApplication, QImage, QPainter, QPen, QPixmap
from PyQt5.QtWidgets import QWidget

log = logging.getLogger(__name__)
MIN_SELECTION = 10


def qimage_to_pil(qimg: QImage) -> Image.Image:
    """Convert a QImage to a PIL RGB image without touching the disk."""
    qimg = qimg.convertToFormat(QImage.Format_RGBA8888)
    width, height = qimg.width(), qimg.height()
    size = qimg.sizeInBytes() if hasattr(qimg, "sizeInBytes") else qimg.byteCount()
    data = qimg.constBits().asstring(size)
    return Image.frombuffer("RGBA", (width, height), data, "raw", "RGBA",
                            qimg.bytesPerLine(), 1).convert("RGB")


class SnippingWidget(QWidget):
    """Shows a frozen screenshot of *every* monitor and lets the user drag a rectangle.

    The crop is taken from the physical-resolution grab of the monitor holding
    most of the selection, so text stays sharp at 100 %, 125 % or 150 % scaling.
    """

    captured = pyqtSignal(object, object, object)   # PIL image, QRect (global), AppContext

    def __init__(self):
        super().__init__()
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.setCursor(Qt.CrossCursor)
        self._shots: List[Tuple[object, QPixmap]] = []
        self._virtual = QRect()
        self._begin: Optional[QPoint] = None
        self._end: Optional[QPoint] = None
        self._context = None

    # ------------------------------------------------------------------ flow
    def start(self, context) -> None:
        if self.isVisible():
            return
        screens = QGuiApplication.screens()
        if not screens:
            return
        self._context = context
        self._shots = [(s, s.grabWindow(0)) for s in screens]
        self._virtual = screens[0].virtualGeometry()
        self.setGeometry(self._virtual)
        self._begin = self._end = None
        self.show()
        self.raise_()
        self.activateWindow()
        self.setFocus()

    def _cancel(self) -> None:
        self._begin = self._end = None
        self.hide()

    def _selection(self) -> Optional[QRect]:
        if self._begin is None or self._end is None:
            return None
        return QRect(self._begin, self._end).normalized()

    def _crop(self, global_rect: QRect) -> Optional[Image.Image]:
        best = None
        best_area = 0
        for screen, pixmap in self._shots:
            geo = screen.geometry()
            inter = global_rect.intersected(geo)
            area = max(0, inter.width()) * max(0, inter.height())
            if area > best_area:
                best, best_area = (screen, pixmap, geo, inter), area
        if best is None:
            return None
        _screen, pixmap, geo, inter = best
        sx = pixmap.width() / max(geo.width(), 1)      # device pixels per logical pixel
        sy = pixmap.height() / max(geo.height(), 1)
        image = pixmap.toImage()
        device = QRect(int(round((inter.x() - geo.x()) * sx)), int(round((inter.y() - geo.y()) * sy)),
                       max(1, int(round(inter.width() * sx))), max(1, int(round(inter.height() * sy))))
        device = device.intersected(image.rect())
        if device.isEmpty():
            return None
        return qimage_to_pil(image.copy(device))

    # ---------------------------------------------------------------- painting
    def _draw_shots(self, painter: QPainter) -> None:
        for screen, pixmap in self._shots:
            painter.drawPixmap(screen.geometry().topLeft() - self._virtual.topLeft(), pixmap)

    def paintEvent(self, event):
        painter = QPainter(self)
        self._draw_shots(painter)
        painter.fillRect(self.rect(), QColor(0, 0, 0, 115))
        sel = self._selection()
        if sel is not None:
            painter.save()
            painter.setClipRect(sel)
            self._draw_shots(painter)
            painter.restore()
            painter.setPen(QPen(QColor(59, 130, 246), 2))
            painter.drawRect(sel)
            label = f"{sel.width()} \u00d7 {sel.height()}"
            painter.setFont(QFont("Segoe UI", 8))
            painter.setPen(QColor(255, 255, 255))
            painter.fillRect(QRect(sel.left(), max(0, sel.top() - 20), 78, 18), QColor(37, 99, 235, 220))
            painter.drawText(QRect(sel.left() + 6, max(0, sel.top() - 20), 72, 18),
                             Qt.AlignVCenter | Qt.AlignLeft, label)

    # ------------------------------------------------------------------ input
    def mousePressEvent(self, event):
        if event.button() == Qt.RightButton:
            self._cancel()
        elif event.button() == Qt.LeftButton:
            self._begin = event.pos()
            self._end = self._begin
            self.update()

    def mouseMoveEvent(self, event):
        if self._begin is not None:
            self._end = event.pos()
            self.update()

    def mouseReleaseEvent(self, event):
        if event.button() != Qt.LeftButton or self._begin is None:
            return
        sel = self._selection()
        self._begin = self._end = None
        self.hide()
        if sel is None or sel.width() < MIN_SELECTION or sel.height() < MIN_SELECTION:
            return
        global_rect = sel.translated(self._virtual.topLeft())
        image = self._crop(global_rect)
        if image is None:
            log.warning("selection %s did not intersect any screen", global_rect)
            return
        self.captured.emit(image, global_rect, self._context)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape:
            self._cancel()
