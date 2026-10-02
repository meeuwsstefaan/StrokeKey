"""Research enrollment identity, without credentials."""
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4


@dataclass
class User:
    name: str
    user_id: str = field(default_factory=lambda: str(uuid4()))
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    enrollment_statistics: dict[str, Any] = field(default_factory=dict)
