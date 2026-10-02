"""Offscreen Qt integration exercises real events and local synthetic workflows."""
import os
import sqlite3
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QEvent, QPointF, Qt
from PySide6.QtGui import QEventPoint, QInputDevice, QMouseEvent, QPointingDevice, QTabletEvent, QTouchEvent
from PySide6.QtWidgets import QApplication, QMessageBox

from stroke_key.gui.enrollment_dialog import EnrollmentDialog
from stroke_key.gui.capture_panel import CapturePanel
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
    assert sample.metadata["sensor_capabilities"] == {"mouse": ["position"]}
    assert sample.metadata["session_id"]
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
    sensors = canvas.sample().metadata["sensor_capabilities"]["stylus"]
    assert ("pressure" in sensors) == has_pressure
    assert ("tilt_x" in sensors) == has_pressure


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
        assert viewer.report.profile.sample_count == 4
        assert viewer.current_sample.sample_id not in viewer.report.profile.reference_ids
        assert viewer.feature_table.rowCount() == 12
        viewer.tabs.setCurrentIndex(1)
        app.processEvents()
        viewer.analysis_plot.draw()
        assert viewer.reference_selector.count() == 5
        viewer.reference_selector.setCurrentIndex(0)
        assert len(viewer.analysis_figure.axes) == 3
        viewer.close()
        main.close()
    finally:
        database.close()


def test_capture_session_labels_quality_and_clear(app, sample_factory):
    panel = CapturePanel()
    session_id = panel.canvas.session_id
    panel.canvas.recorder.points = sample_factory().points
    panel.session_tag.setText("Synthetic morning session")
    panel.research_label.setCurrentIndex(panel.research_label.findData("genuine"))
    panel.update_status()
    assert not panel.new_session_button.isEnabled()
    sample = panel.validated_sample()
    assert sample.metadata["session_id"] == session_id
    assert sample.metadata["session_tag"] == "Synthetic morning session"
    assert sample.metadata["research_label"] == "genuine"
    assert "59.0 Hz" in panel.quality.toPlainText()
    panel.canvas.clear()
    assert panel.canvas.session_id == session_id
    assert panel.new_session_button.isEnabled()
    panel.new_session_button.click()
    assert panel.canvas.session_id != session_id
    assert panel.session_tag.text() == ""


def test_viewer_draft_profile_selection_and_corrupt_reference_recovery(app, tmp_path, sample_factory, monkeypatch):
    monkeypatch.setattr("stroke_key.gui.sample_viewer.show_error", lambda *args: None)
    database = Database(tmp_path / "viewer.db")
    try:
        samples, users = SampleRepository(database), UserRepository(database)
        service = EnrollmentService(users, samples)
        references = [sample_factory(duration=1 + i / 10) for i in range(5)]
        for sample in references:
            service.stage(sample)
        user = service.complete("Synthetic participant", [s.sample_id for s in references])
        draft = sample_factory(duration=3)
        samples.save(draft)
        viewer = SampleViewer(samples, users=[user])
        draft_row = next(i for i, row in enumerate(viewer.rows) if row["sample_id"] == draft.sample_id)
        viewer.list.setCurrentRow(draft_row)
        assert viewer.profile_selector.isEnabled()
        assert viewer.report.profile.sample_count == 0
        viewer.profile_selector.setCurrentIndex(viewer.profile_selector.findData(user.user_id))
        assert viewer.report.profile.sample_count == 5
        assert viewer.reference_selector.count() == 6
        # Simulate external corruption after explicitly bypassing the new immutability guard.
        with pytest.raises(sqlite3.IntegrityError):
            database.connection.execute("UPDATE signature_samples SET metadata = 'broken' WHERE sample_id = ?",
                                        (references[0].sample_id,))
        with database.connection:
            database.connection.execute("DROP TRIGGER protect_signature_samples_update")
            database.connection.execute("UPDATE signature_samples SET metadata = 'broken' WHERE sample_id = ?",
                                        (references[0].sample_id,))
        viewer.update_analysis()
        assert viewer.report.profile.sample_count == 4
        assert "1 unreadable" in viewer.profile_summary.text()
        bad_row = next(i for i, row in enumerate(viewer.rows) if row["sample_id"] == references[0].sample_id)
        viewer.list.setCurrentRow(bad_row)
        assert viewer.current_sample is None
        assert viewer.report is None
        assert viewer.feature_table.rowCount() == 0
        viewer.list.setCurrentRow(draft_row)
        assert viewer.current_sample.sample_id == draft.sample_id
        viewer.close()
    finally:
        database.close()
