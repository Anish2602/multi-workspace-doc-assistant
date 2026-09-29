"""Guards against committing real secrets into tracked config templates."""

import re
from pathlib import Path

ENV_EXAMPLE = Path(__file__).resolve().parents[2] / ".env.example"

# Shapes of real credentials we use: Neon hosts, Google/Groq keys, Discord webhooks.
REAL_SECRET_PATTERNS = [
    r"ep-[a-z0-9-]+\.[a-z0-9.-]*neon\.tech",
    r"AIza[0-9A-Za-z_-]{30,}",
    r"gsk_[0-9A-Za-z]{20,}",
    r"discord(?:app)?\.com/api/webhooks/\d+/[\w-]{20,}",
]


def test_env_example_contains_only_placeholders():
    content = ENV_EXAMPLE.read_text()
    for pattern in REAL_SECRET_PATTERNS:
        assert not re.search(pattern, content), (
            f".env.example looks like it holds a real secret ({pattern})"
        )
