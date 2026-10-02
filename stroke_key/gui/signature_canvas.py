"""Qt adapters for mouse, tablet and a single active touch contact."""
from PySide6.QtCore import QEvent, QPointF, Qt, Signal
from PySide6.QtGui import (QColor, QEventPoint, QInputDevice, QMouseEvent,
                          QPainter, QPaintEvent, QPen, QTabletEvent, QTouchEvent)
from PySide6.QtWidgets import QWidget

from stroke_key.capture.events import PointerMeasurement
from stroke_key.capture.recorder import SignatureRecorder
from stroke_key.models.signature import SignatureSample


class SignatureCanvas(QWidget):
    changed = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.recorder = SignatureRecorder()
        self._touch_id: int | None = None
        self.setMinimumSize(650, 300)
        self.setAttribute(Qt.WidgetAttribute.WA_AcceptTouchEvents)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setCursor(Qt.CursorShape.CrossCursor)

    def clear(self) -> None:
        self.recorder.clear()
        self._touch_id = None
        self.update()
        self.changed.emit()

    def sample(self) -> SignatureSample:
        sample = self.recorder.sample()
        sample.metadata.update({"canvas_width": self.width(), "canvas_height": self.height()})
        return sample

    def _record(self, measurement: PointerMeasurement, state: str) -> None:
        recorded = self.recorder.record(measurement, state)
        if recorded or self.recorder.interrupted:
            self.update()
            self.changed.emit()

    def _mouse(self, event: QMouseEvent, state: str) -> None:
        if event.source() != Qt.MouseEventSource.MouseEventNotSynthesized:
            event.accept()
            return
        position = event.position()
        self._record(PointerMeasurement(position.x(), position.y(), "mouse"), state)
        event.accept()

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._mouse(event, "down")

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if event.buttons() & Qt.MouseButton.LeftButton:
            self._mouse(event, "move")

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._mouse(event, "up")

    def tabletEvent(self, event: QTabletEvent) -> None:
        states = {QEvent.Type.TabletPress: "down", QEvent.Type.TabletMove: "move",
                  QEvent.Type.TabletRelease: "up"}
        state = states.get(event.type())
        if state:
            position = event.position()
            capabilities = event.pointingDevice().capabilities()
            pressure = event.pressure() if capabilities & QInputDevice.Capability.Pressure else None
            tilt_x = event.xTilt() if capabilities & QInputDevice.Capability.XTilt else None
            tilt_y = event.yTilt() if capabilities & QInputDevice.Capability.YTilt else None
            rotation = event.rotation() if capabilities & QInputDevice.Capability.Rotation else None
            self._record(PointerMeasurement(position.x(), position.y(), "stylus", pressure,
                                            tilt_x, tilt_y, rotation, event.buttons().value,
                                            str(event.pointingDevice().systemId())), state)
        event.accept()  # Prevent duplicate Qt-synthesized mouse capture.

    def event(self, event: QEvent) -> bool:
        touch_types = {QEvent.Type.TouchBegin, QEvent.Type.TouchUpdate, QEvent.Type.TouchEnd}
        if event.type() in touch_types and isinstance(event, QTouchEvent):
            for point in event.points():
                if point.state() == QEventPoint.State.Pressed and self._touch_id is None:
                    if self.recorder.active_device is not None:
                        continue
                    self._touch_id = point.id()
                if point.id() != self._touch_id:
                    continue
                states = {QEventPoint.State.Pressed: "down", QEventPoint.State.Updated: "move",
                          QEventPoint.State.Released: "up"}
                state = states.get(point.state())
                if state:
                    position = point.position()
                    pressure = point.pressure() if event.pointingDevice().capabilities() & QInputDevice.Capability.Pressure else None
                    self._record(PointerMeasurement(position.x(), position.y(), "touch", pressure,
                                                    device_id=str(event.pointingDevice().systemId())), state)
                if state == "up":
                    self._touch_id = None
            event.accept()
            return True
        if event.type() in {QEvent.Type.TouchCancel, QEvent.Type.WindowDeactivate, QEvent.Type.Hide}:
            if hasattr(self, "recorder"):
                self.recorder.cancel()
                self._touch_id = None
                self.changed.emit()
        return super().event(event)

    def paintEvent(self, event: QPaintEvent) -> None:
        painter = QPainter(self)
        painter.fillRect(self.rect(), Qt.GlobalColor.white)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(QColor("#202938"), 2.2, Qt.PenStyle.SolidLine,
                            Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        points = self.recorder.points
        for index, point in enumerate(points):
            position = QPointF(point.x, point.y)
            if index and points[index - 1].stroke_number == point.stroke_number:
                previous = points[index - 1]
                painter.drawLine(QPointF(previous.x, previous.y), position)
            else:
                painter.drawPoint(position)
        painter.setPen(QColor("#aab2bd"))
        painter.drawRect(self.rect().adjusted(0, 0, -1, -1))
