"""Offscreen Qt integration exercises real events and local synthetic workflows."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QEvent, QPointF, Qt
from PySide6.QtGui import QEventPoint, QInputDevice, QMouseEvent, QPointingDevice, QTabletEvent, QTouchEvent
from PySide6.QtWidgets import QApplication, QMessageBox

from stroke_key.gui.enrollment_dialog import EnrollmentDialog
from stroke_key.gui.main_window import CaptureDialog, MainWindow
from stroke_key.gui.sample_viewer import SampleViewer
from stroke_key.gui.signature_canvas import SignatureCanvas
from stroke_key.gui.verification_dialog import VerificationDialog
from stroke_key.models.signature import validity_errors
from stroke_key.services.enrollment import EnrollmentService
from stroke_key.services.verification import VerificationService
from stroke_key.storage.database import Database
from stroke_key.storage.repositories import SampleRepository, UserRepository


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def send_mouse(canvas, kind, x, y):
    button = Qt.MouseButton.NoButton if kind == QEvent.Type.MouseMove else Qt.MouseButton.LeftButton
    buttons = Qt.MouseButton.NoButton if kind == QEvent.Type.MouseButtonRelease else Qt.MouseButton.LeftButton
    event = QMouseEvent(kind, QPointF(x, y), QPointF(x, y), button, buttons, Qt.KeyboardModifier.NoModifier)
    QApplication.sendEvent(canvas, event)


def test_mouse_capture_and_stroke_render(app, monkeypatch):
    clock = iter(i * 0.02 for i in range(200))
    monkeypatch.setattr("stroke_key.capture.recorder.time.perf_counter", lambda: next(clock))
    canvas = SignatureCanvas()
    canvas.show()
    app.processEvents()
    for stroke in range(2):
        send_mouse(canvas, QEvent.Type.MouseButtonPress, 10 + stroke * 300, 20)
        for i in range(25):
            send_mouse(canvas, QEvent.Type.MouseMove, 10 + stroke * 300 + i * 3, 20 + i * 2)
        send_mouse(canvas, QEvent.Type.MouseButtonRelease, 90 + stroke * 300, 75)
    sample = canvas.sample()
    assert len(sample.points) == 54
    assert sample.number_of_strokes == 2
    assert not validity_errors(sample)
    assert all(p.pressure is None for p in sample.points)
    image = canvas.grab().toImage()
    assert image.pixelColor(10, 20).lightness() < 200
    assert image.pixelColor(200, 45).lightness() > 240  # no bridge across strokes
    canvas.clear()
    assert not canvas.sample().points
    canvas.close()


@pytest.mark.parametrize("has_pressure", [False, True])
def test_tablet_capabilities(app, has_pressure):
    capabilities = QInputDevice.Capability.Position
    if has_pressure:
        capabilities |= QInputDevice.Capability.Pressure | QInputDevice.Capability.XTilt | QInputDevice.Capability.YTilt
    device = QPointingDevice("Synthetic pen", 123, QInputDevice.DeviceType.Stylus,
                             QPointingDevice.PointerType.Pen, capabilities, 1, 2)
    canvas = SignatureCanvas()
    for kind in (QEvent.Type.TabletPress, QEvent.Type.TabletMove, QEvent.Type.TabletRelease):
        event = QTabletEvent(kind, device, QPointF(10, 20), QPointF(10, 20), 0.6, 12, -5,
                             0, 0, 0, Qt.KeyboardModifier.NoModifier,
                             Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton)
        QApplication.sendEvent(canvas, event)
    assert len(canvas.sample().points) == 3
    point = canvas.sample().points[0]
    assert point.device_type == "stylus"
    assert point.pressure == (0.6 if has_pressure else None)
    assert point.tilt_x == (12 if has_pressure else None)
    assert point.tilt_y == (-5 if has_pressure else None)
    assert point.orientation is None


def test_touch_capture_single_contact_and_cancellation(app):
    device = QPointingDevice("Synthetic touch", 321, QInputDevice.DeviceType.TouchScreen,
                             QPointingDevice.PointerType.Finger, QInputDevice.Capability.Position, 5, 0)
    canvas = SignatureCanvas()
    for kind, state in ((QEvent.Type.TouchBegin, QEventPoint.State.Pressed),
                        (QEvent.Type.TouchUpdate, QEventPoint.State.Updated),
                        (QEvent.Type.TouchEnd, QEventPoint.State.Released)):
        point = QEventPoint(1, state, QPointF(50, 60), QPointF(50, 60))
        other_state = QEventPoint.State.Pressed if kind == QEvent.Type.TouchBegin else QEventPoint.State.Stationary
        other = QEventPoint(2, other_state, QPointF(80, 90), QPointF(80, 90))
        canvas.event(QTouchEvent(kind, device, Qt.KeyboardModifier.NoModifier, [point, other]))
    assert len(canvas.sample().points) == 3
    assert all(p.device_type == "touch" and p.pressure is None for p in canvas.sample().points)
    assert canvas.recorder.active_device is None
    point = QEventPoint(3, QEventPoint.State.Pressed, QPointF(10, 20), QPointF(10, 20))
    canvas.event(QTouchEvent(QEvent.Type.TouchBegin, device, Qt.KeyboardModifier.NoModifier, [point]))
    canvas.event(QEvent(QEvent.Type.TouchCancel))
    assert canvas.sample().metadata["interrupted"]


def test_dialog_workflow_and_viewer(app, tmp_path, sample_factory, monkeypatch):
    monkeypatch.setattr(QMessageBox, "information", lambda *args: None)
    database = Database(tmp_path / "gui.db")
    try:
        main = MainWindow(database)
        main.show()
        app.processEvents()
        assert main.isVisible()
        samples, users = SampleRepository(database), UserRepository(database)
        capture = CaptureDialog(samples)
        capture.capture.canvas.recorder.points = sample_factory().points
        capture.save()
        assert len(samples.list_summaries()) == 1
        assert not capture.capture.canvas.sample().points
        empty_verification = VerificationDialog(VerificationService(samples), [])
        assert not empty_verification.verify_button.isEnabled()
        assert "No enrolled users" in empty_verification.output.toPlainText()
        dialog = EnrollmentDialog(EnrollmentService(users, samples))
        dialog.name.setText("Synthetic GUI participant")
        for i in range(5):
            dialog.capture.canvas.recorder.points = sample_factory().points
            dialog.save_sample()
        assert dialog.finish_button.isEnabled()
        dialog.samples_list.setCurrentRow(2)
        dialog.retry_sample()
        assert len(dialog.sample_ids) == 4
        dialog.capture.canvas.recorder.points = sample_factory().points
        dialog.save_sample()
        dialog.complete()
        assert len(users.list_users()) == 1
        verification = VerificationDialog(VerificationService(samples), users.list_users())
        verification.capture.canvas.recorder.points = sample_factory().points
        verification.verify()
        assert verification.worker.wait(15_000)
        app.processEvents()
        assert "ACCEPTED" in verification.output.toPlainText()
        assert verification.worker is None
        viewer = SampleViewer(samples)
        viewer.show()
        app.processEvents()
        assert len(viewer.figure.axes) == 3
        assert "60 points" in viewer.metadata.text()
        viewer.close()
        main.close()
    finally:
        database.close()
