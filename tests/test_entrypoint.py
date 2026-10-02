"""Launch the actual entry point from a different working directory."""
from pathlib import Path
import os
import subprocess
import sys


def test_main_starts_and_exits_cleanly(tmp_path):
    root = Path(__file__).resolve().parents[1]
    script = """
import runpy
import sys
from pathlib import Path
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication
sys.path.insert(0, sys.argv[1])
import stroke_key.app as application
application.database_path = lambda: Path(sys.argv[2]) / 'entrypoint.db'
application.configure_logging = lambda: None
def inspect_window():
    windows = QApplication.topLevelWidgets()
    visible = [w for w in windows if w.isVisible() and w.windowTitle() == 'StrokeKey']
    print('MAIN_WINDOW_VISIBLE=' + str(bool(visible)), flush=True)
    QApplication.instance().quit()
class TimedApplication(QApplication):
    def __init__(self, arguments):
        super().__init__(arguments)
        QTimer.singleShot(300, inspect_window)
application.QApplication = TimedApplication
runpy.run_path(str(Path(sys.argv[1]) / 'main.py'), run_name='__main__')
"""
    environment = {**os.environ, "QT_QPA_PLATFORM": "offscreen"}
    result = subprocess.run([sys.executable, "-c", script, str(root), str(tmp_path)],
                            cwd=tmp_path, env=environment, capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr
    assert "MAIN_WINDOW_VISIBLE=True" in result.stdout
    assert (tmp_path / "entrypoint.db").exists()
