from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class CreativeCollabTriggerContractTests(unittest.TestCase):
    def test_team_rules_accept_short_and_legacy_trigger_phrases(self) -> None:
        team_rules = (ROOT / "deploy/feishu_creative_team/AGENTS.md").read_text(
            encoding="utf-8"
        )
        runtime_rules = (
            ROOT / "deploy/feishu_creative_team/bridge-runtime-instructions.md"
        ).read_text(encoding="utf-8")

        for phrase in (
            "/协作",
            "开始协作",
            "启动协作",
            "启用协作",
            "启动创意协作",
            "启用创意协作",
        ):
            self.assertIn(phrase, team_rules)
            self.assertIn(phrase, runtime_rules)

    def test_confirm_script_shortcut_is_explicit(self) -> None:
        team_rules = (ROOT / "deploy/feishu_creative_team/AGENTS.md").read_text(
            encoding="utf-8"
        )
        runtime_rules = (
            ROOT / "deploy/feishu_creative_team/bridge-runtime-instructions.md"
        ).read_text(encoding="utf-8")

        for content in (team_rules, runtime_rules):
            self.assertIn("确认脚本，开始协作", content)
            self.assertIn("/协作 确认", content)
            self.assertIn("/协作 状态", content)
            self.assertIn("关键要求", content)

    def test_usage_guide_uses_short_trigger_as_primary_example(self) -> None:
        guide = (ROOT / "deploy/feishu_creative_team/usage-guide.xml").read_text(
            encoding="utf-8"
        )

        self.assertIn("@创意 Agent /协作", guide)
        self.assertIn("一个项目只维护一份云文档", guide)
        self.assertIn("只回复一次最终结果", guide)
        self.assertIn("已评论，请同步文档并继续", guide)

    def test_status_command_requires_selection_when_multiple_projects_are_active(self) -> None:
        team_rules = (ROOT / "deploy/feishu_creative_team/AGENTS.md").read_text(
            encoding="utf-8"
        )
        runtime_rules = (
            ROOT / "deploy/feishu_creative_team/bridge-runtime-instructions.md"
        ).read_text(encoding="utf-8")

        for content in (team_rules, runtime_rules):
            self.assertIn("多个进行中项目", content)
            self.assertIn("/协作 状态 2", content)
            self.assertIn("项目名称", content)

    def test_team_outputs_use_one_project_document_and_final_reply_only(self) -> None:
        team_rules = (ROOT / "deploy/feishu_creative_team/AGENTS.md").read_text(
            encoding="utf-8"
        )
        runtime_rules = (
            ROOT / "deploy/feishu_creative_team/bridge-runtime-instructions.md"
        ).read_text(encoding="utf-8")

        for content in (team_rules, runtime_rules):
            self.assertIn("同一个链接", content)
            self.assertIn("sync-project", content)
            self.assertIn("pull-project", content)
            self.assertIn("未解决评论", content)
            self.assertIn("只回复一次最终", content)


if __name__ == "__main__":
    unittest.main()
