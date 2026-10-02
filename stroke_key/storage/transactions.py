"""Composable SQLite transactions, including nested repository writes."""
from contextlib import contextmanager
import sqlite3
from uuid import uuid4


@contextmanager
def atomic(connection: sqlite3.Connection):
    # SAVEPOINT works both alone and inside another repository's transaction.
    name = "write_" + uuid4().hex
    connection.execute(f"SAVEPOINT {name}")
    try:
        yield
        connection.execute(f"RELEASE SAVEPOINT {name}")
    except BaseException:
        connection.execute(f"ROLLBACK TO SAVEPOINT {name}")
        connection.execute(f"RELEASE SAVEPOINT {name}")
        raise
