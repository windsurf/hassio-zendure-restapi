"""Shared setup: the repository root on the path, so custom_components imports.

The tests need Home Assistant installed (any release the integration supports)
and pytest. They build the integration's objects without a running Home
Assistant: the device, the meter and the repairs registry are stand-ins.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
