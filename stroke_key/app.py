"""Application entry point and resource ownership."""
import logging
import sys

from PySide6.QtWidgets import QApplication, QMessageBox

from stroke_key.gui.main_window import MainWindow
from stroke_key.storage.database import Database
from stroke_key.utils.logging_config import configure_logging
from stroke_key.utils.paths import database_path


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("StrokeKey")
    database = None
    try:
        configure_logging()
        database = Database(database_path())
        window = MainWindow(database)
        window.show()
        return app.exec()
    except Exception:
        logging.getLogger(__name__).exception("Application startup failed")
        QMessageBox.critical(None, "StrokeKey could not start",
                             "Could not initialize local storage or the desktop interface. "
                             "Check that the project data directory is writable and the database is valid.")
        return 1
    finally:
        if database is not None:
            database.close()
