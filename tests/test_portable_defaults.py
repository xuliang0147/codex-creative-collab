import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from creative_collab.config import DEFAULT_CREATIVE_ROOT, DEFAULT_DB_PATH
from creative_collab.feishu_doc_publisher import FeishuDocPublisher
from creative_collab.cli import build_parser


class PortableDefaultsTests(unittest.TestCase):
    def test_default_workspace_is_separate_from_existing_codex_registry(self):
        self.assertEqual(Path.home() / "codex-creative-workspace", DEFAULT_CREATIVE_ROOT)
        self.assertEqual(DEFAULT_CREATIVE_ROOT / "00_协作账本" / "registry.sqlite", DEFAULT_DB_PATH)

    def test_publisher_requires_explicit_recipient(self):
        with tempfile.TemporaryDirectory() as root:
            with self.assertRaisesRegex(ValueError, "chat_id cannot be blank"):
                FeishuDocPublisher(team_root=Path(root))

    def test_requester_default_is_generic(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual("任务发起人", build_parser().parse_args(["agent-list"]).requester_label)

    def test_explicit_configuration_remains_supported(self):
        with patch.dict(os.environ, {"CREATIVE_COLLAB_DB_PATH": "/tmp/test-collab/db.sqlite", "CREATIVE_COLLAB_ROOT": "/tmp/test-collab", "CREATIVE_COLLAB_REQUESTER_LABEL": "制作人"}):
            args = build_parser().parse_args(["agent-list"])
        self.assertEqual("/tmp/test-collab/db.sqlite", args.db_path)
        self.assertEqual("/tmp/test-collab", args.creative_root)
        self.assertEqual("制作人", args.requester_label)
