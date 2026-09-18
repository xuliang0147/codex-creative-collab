#!/usr/bin/env python3
from __future__ import annotations

import os
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
TEAM_ROOT = Path(os.environ.get("CREATIVE_COLLAB_ROOT", str(Path.home() / "codex-creative-team"))).expanduser()

os.environ.setdefault(
    "CREATIVE_COLLAB_DB_PATH",
    str(TEAM_ROOT / "00_协作账本" / "creative-collab.sqlite"),
)
os.environ.setdefault("CREATIVE_COLLAB_ROOT", str(TEAM_ROOT))
os.environ.setdefault("CREATIVE_COLLAB_REQUESTER_LABEL", "任务发起人")
os.environ.setdefault(
    "CREATIVE_COLLAB_DISPATCH_QUEUE",
    str(TEAM_ROOT / "00_协作账本" / "线程派发队列"),
)
sys.path.insert(0, str(REPO_ROOT))

from creative_collab.cli import main


if __name__ == "__main__":
    main()
