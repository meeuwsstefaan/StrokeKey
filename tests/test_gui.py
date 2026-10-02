"""Offscreen Qt integration exercises real events and local synthetic workflows."""
import os
import sqlite3
from threading import Event
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


def finish_guidance(app, dialog):
    dialog.start_guidance()
    assert dialog.worker is not None
    assert dialog.worker.wait(15_000)
    app.processEvents()
    assert dialog.worker is None


def test_guided_dialog_additional_samples_and_leave_one_out(app, tmp_path, sample_factory):
    database = Database(tmp_path / "guided.db")
    dialog = EnrollmentDialog(EnrollmentService(UserRepository(database), SampleRepository(database)))
    try:
        dialog.name.setText("Synthetic guided participant")
        for index in range(6):
            dialog.capture.canvas.recorder.points = sample_factory(duration=1 + index / 10).points
            dialog.save_sample()
        assert dialog.finish_button.isEnabled()
        assert dialog.save_button.isEnabled() and dialog.capture.isEnabled()
        assert not dialog.target_selector.isEnabled()
        finish_guidance(app, dialog)
        assert "Compared with 5 compatible references" in dialog.saved_guidance.toPlainText()
        dialog.capture.canvas.recorder.points = sample_factory(duration=8).points
        finish_guidance(app, dialog)
        assert "Duration: 8" in dialog.current_guidance.toPlainText()
        assert len(dialog.sample_ids) == 6  # Analysis never saves or removes samples.
        dialog.save_sample()
        assert len(dialog.sample_ids) == 7
        dialog.complete()
        assert dialog.result() == dialog.DialogCode.Accepted
        assert dialog.service.users.list_users()[0].enrollment_statistics["sample_count"] == 7
    finally:
        dialog.close()
        database.close()


def test_guided_dialog_existing_participant_later_session(app, tmp_path, sample_factory):
    from stroke_key.storage.research_repositories import ReferenceSetRepository
    database = Database(tmp_path / "extend-gui.db")
    service = EnrollmentService(UserRepository(database), SampleRepository(database))
    originals = [sample_factory() for _ in range(5)]
    for sample in originals:
        sample.device_type = "mouse"
        sample.metadata["session_id"] = "original-session"
        service.stage(sample)
    user = service.complete("Synthetic existing participant", [s.sample_id for s in originals])
    references = ReferenceSetRepository(database)
    first = references.latest_for_user(user.user_id)[0]
    dialog = EnrollmentDialog(service)
    try:
        dialog.target_selector.setCurrentIndex(dialog.target_selector.findData(user.user_id))
        assert not dialog.name.isEnabled()
        assert not dialog.finish_button.isEnabled()
        assert "5 existing" in dialog.progress.text()
        dialog.capture.canvas.recorder.points = sample_factory().points
        dialog.save_sample()
        assert dialog.finish_button.isEnabled()
        assert dialog.finish_button.text() == "Add to Enrollment"
        # Programmatic switching also cannot change the owner of pending drafts.
        dialog.target_selector.setCurrentIndex(0)
        assert dialog.target_selector.currentData() == user.user_id
        finish_guidance(app, dialog)
        assert "Compared with 5 compatible references" in dialog.saved_guidance.toPlainText()
        dialog.complete()
        updated = service.users.get(user.user_id)
        assert updated.enrollment_statistics["sample_count"] == 6
        assert updated.enrollment_statistics["signing_profiles"][0]["session_count"] == 2
        assert references.get(first.reference_set_id) == first
        assert references.latest_for_user(user.user_id)[0].revision == 2
    finally:
        dialog.close()
        database.close()


def test_completion_keeps_unsaved_capture_until_explicit_save_or_clear(app, tmp_path, sample_factory, monkeypatch):
    messages = []
    monkeypatch.setattr("stroke_key.gui.enrollment_dialog.show_error", lambda parent, text: messages.append(text))
    database = Database(tmp_path / "unsaved.db")
    dialog = EnrollmentDialog(EnrollmentService(UserRepository(database), SampleRepository(database)))
    try:
        dialog.name.setText("Synthetic")
        for _ in range(5):
            dialog.capture.canvas.recorder.points = sample_factory().points
            dialog.save_sample()
        dialog.capture.canvas.recorder.points = sample_factory().points
        dialog.complete()
        assert messages and "clear it" in messages[-1]
        assert not dialog.service.users.list_users()
        assert len(dialog.capture.canvas.recorder.points) == 60
        dialog.capture.canvas.clear()
        dialog.complete()
        assert len(dialog.service.users.list_users()) == 1
    finally:
        dialog.close()
        database.close()


def test_enrollment_worker_defers_close_until_finished(app, tmp_path, sample_factory, monkeypatch):
    from stroke_key.processing.enrollment_guidance import assess_enrollment
    started, release = Event(), Event()

    def slow_analysis(candidate, references):
        started.set()
        assert release.wait(5)
        return assess_enrollment(candidate, references)

    monkeypatch.setattr("stroke_key.gui.enrollment_dialog.assess_enrollment", slow_analysis)
    database = Database(tmp_path / "worker-close.db")
    dialog = EnrollmentDialog(EnrollmentService(UserRepository(database), SampleRepository(database)))
    try:
        dialog.show()
        app.processEvents()
        dialog.capture.canvas.recorder.points = sample_factory().points
        dialog.start_guidance()
        assert started.wait(2)
        worker = dialog.worker
        dialog.reject()
        assert dialog.isVisible()
        assert not dialog.isEnabled()
        release.set()
        assert worker.wait(15_000)
        app.processEvents()
        assert dialog.worker is None
        assert not dialog.isVisible()
    finally:
        release.set()
        if dialog.worker is not None:
            dialog.worker.wait(15_000)
            app.processEvents()
        dialog.close()
        database.close()
