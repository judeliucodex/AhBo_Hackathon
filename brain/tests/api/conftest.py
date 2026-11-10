import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "platform"))

from platform_app import client, settings  # noqa: F401
