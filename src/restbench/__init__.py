from pathlib import Path as _Path
import os as _os

def data_dir() -> _Path:
    """Resolve the project data/ directory.

    Checks RESTBENCH_PROJECT_DIR env var first (for Docker / production),
    then falls back to relative path from source tree.
    """
    env = _os.getenv("RESTBENCH_PROJECT_DIR")
    if env:
        return _Path(env) / "data"
    return _Path(__file__).resolve().parent.parent.parent / "data"
