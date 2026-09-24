import shutil
import subprocess
from pathlib import Path

import pytest

TEST_FILE = Path(__file__).parent / "js" / "search.test.js"


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js non installé")
def test_search_logic_in_javascript():
    result = subprocess.run(["node", str(TEST_FILE)], capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
