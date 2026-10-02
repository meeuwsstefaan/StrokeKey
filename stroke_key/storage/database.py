"""Local relational schema. SQLite is not encrypted storage."""
from pathlib import Path
import sqlite3


SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    user_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    created_at TEXT NOT NULL,
    enrollment_statistics TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS signature_samples (
    sample_id TEXT PRIMARY KEY,
    user_id TEXT REFERENCES users(user_id),
    created_at TEXT NOT NULL,
    device_type TEXT NOT NULL,
    total_duration REAL NOT NULL,
    number_of_strokes INTEGER NOT NULL,
    metadata TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS signature_points (
    sample_id TEXT NOT NULL REFERENCES signature_samples(sample_id) ON DELETE CASCADE,
    point_index INTEGER NOT NULL,
    x REAL NOT NULL, y REAL NOT NULL,
    timestamp REAL NOT NULL, elapsed_time REAL NOT NULL,
    pressure REAL,
    stroke_number INTEGER NOT NULL,
    pointer_state TEXT NOT NULL,
    device_type TEXT NOT NULL,
    tilt_x REAL, tilt_y REAL, orientation REAL,
    stylus_buttons INTEGER, device_id TEXT,
    PRIMARY KEY (sample_id, point_index)
);
CREATE INDEX IF NOT EXISTS samples_by_user ON signature_samples(user_id);
PRAGMA user_version = 1;
"""


class Database:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(path)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys = ON")
        try:
            version = self.connection.execute("PRAGMA user_version").fetchone()[0]
            if version not in (0, 1):
                raise ValueError("Unsupported database version")
            self.connection.executescript(SCHEMA)
        except Exception:
            self.connection.close()
            raise

    def close(self) -> None:
        self.connection.close()
