#!/usr/bin/env python3
from __future__ import annotations

import os
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
TEAM_ROOT = Path(os.environ.get("CREATIVE_COLLAB_ROOT", str(Path.home() / "codex-creative-team"))).expanduser()

os.environ.setdefault("CREATIVE_COLLAB_ROOT", str(TEAM_ROOT))
os.environ.setdefault(
    "CREATIVE_COLLAB_FEISHU_DOC_QUEUE",
    str(TEAM_ROOT / "00_协作账本" / "飞书文档发布队列"),
)
sys.path.insert(0, str(REPO_ROOT))

from creative_collab.feishu_doc_publisher import main


if __name__ == "__main__":
    main()
