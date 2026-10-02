"""Project-relative paths independent of the process working directory."""
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def data_directory() -> Path:
    path = PROJECT_ROOT / "data"
    path.mkdir(parents=True, exist_ok=True)
    return path


def database_path() -> Path:
    return data_directory() / "strokekey.db"
