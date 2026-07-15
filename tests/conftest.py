"""Put src/ on the import path so tests can `import vault_api...` without an
install step. The parser under test is pure stdlib, so these tests do not need
fastapi/uvicorn present.
"""

import sys
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
