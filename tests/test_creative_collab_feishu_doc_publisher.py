from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from creative_collab.feishu_doc_publisher import FeishuDocPublisher


class FeishuDocPublisherTests(unittest.TestCase):
    def test_launch_agent_executes_node_worker_directly(self) -> None:
        plist = (
            Path(__file__).resolve().parents[1]
            / "deploy"
            / "feishu_creative_team"
            / "com.example.creative-feishu-doc-publisher.example.plist"
        ).read_text(encoding="utf-8")

        self.assertIn(
            "/path/to/node",
            plist,
        )
        self.assertIn(
            "/path/to/codex-creative-collab/deploy/feishu_creative_team/creative-feishu-doc-publisher-worker.mjs",
            plist,
        )
        self.assertNotIn("<string>-m</string>", plist)

    def test_enqueue_rejects_sources_outside_team_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            team_root = root / "team"
            team_root.mkdir()
            outside = root / "outside.xml"
            outside.write_text("<title>outside</title>", encoding="utf-8")
            publisher = FeishuDocPublisher(
                team_root=team_root,
                queue_root=team_root / "queue",
                chat_id="oc_team",
            )

            with self.assertRaisesRegex(ValueError, "团队目录"):
                publisher.enqueue(
                    request_id="DOC-001",
                    source_path=outside,
                    doc_format="xml",
                )

    def test_process_next_creates_document_and_grants_group_view(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            team_root = root / "team"
            source = team_root / "01_编导" / "需求确认.xml"
            source.parent.mkdir(parents=True)
            source.write_text("<title>需求确认</title><p>正文</p>", encoding="utf-8")
            calls: list[tuple[list[str], Path]] = []

            def fake_run(args, cwd, env):
                calls.append((list(args), Path(cwd)))
                if "docs" in args:
                    payload = {
                        "ok": True,
                        "data": {
                            "document": {
                                "document_id": "docx_test",
                                "url": "https://example.feishu.cn/docx/docx_test",
                            }
                        },
                    }
                else:
                    payload = {"ok": True, "data": {"member": {"perm": "view"}}}
                return subprocess.CompletedProcess(args, 0, json.dumps(payload), "")

            publisher = FeishuDocPublisher(
                team_root=team_root,
                queue_root=team_root / "queue",
                chat_id="oc_team",
                command_runner=fake_run,
            )
            publisher.enqueue(
                request_id="DOC-002",
                source_path=source,
                doc_format="xml",
            )

            result = publisher.process_next()

            self.assertEqual("complete", result["status"])
            self.assertEqual("docx_test", result["document_id"])
            self.assertEqual(2, len(calls))
            create_args, create_cwd = calls[0]
            self.assertEqual(source.parent.resolve(), create_cwd.resolve())
            self.assertIn("docs", create_args)
            self.assertIn("+create", create_args)
            self.assertIn("@需求确认.xml", create_args)
            permission_args, _ = calls[1]
            self.assertIn("permission.members", permission_args)
            self.assertIn("oc_team", " ".join(permission_args))
            stored = json.loads(
                (team_root / "queue" / "results" / "DOC-002.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(result, stored)

    def test_duplicate_request_returns_existing_result_without_republishing(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            team_root = root / "team"
            source = team_root / "需求确认.xml"
            source.parent.mkdir(parents=True, exist_ok=True)
            source.write_text("<title>需求确认</title>", encoding="utf-8")
            publisher = FeishuDocPublisher(
                team_root=team_root,
                queue_root=team_root / "queue",
                chat_id="oc_team",
            )
            result_path = team_root / "queue" / "results" / "DOC-003.json"
            result_path.parent.mkdir(parents=True)
            expected = {
                "request_id": "DOC-003",
                "status": "complete",
                "url": "https://example.feishu.cn/docx/existing",
            }
            result_path.write_text(json.dumps(expected), encoding="utf-8")

            result = publisher.enqueue(
                request_id="DOC-003",
                source_path=source,
                doc_format="xml",
            )

            self.assertEqual(expected, result)
            self.assertFalse((team_root / "queue" / "pending" / "DOC-003.json").exists())

    def test_sync_project_enqueues_content_addressed_upsert(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            team_root = root / "team"
            source = (
                team_root
                / "03_视频剪辑项目"
                / "2026-07-20-测试项目"
                / "00_项目管理"
                / "项目协作文档.md"
            )
            source.parent.mkdir(parents=True)
            source.write_text("# 测试项目\n\n当前阶段：待确认。\n", encoding="utf-8")
            publisher = FeishuDocPublisher(
                team_root=team_root,
                queue_root=team_root / "queue",
                chat_id="oc_team",
            )

            queued = publisher.sync_project(
                project_id="TOPIC-20260720-001",
                source_path=source,
                doc_format="markdown",
                title="测试项目｜创意协作",
            )

            self.assertEqual("queued", queued["status"])
            self.assertEqual("TOPIC-20260720-001", queued["project_id"])
            request_path = next((team_root / "queue" / "pending").glob("*.json"))
            payload = json.loads(request_path.read_text(encoding="utf-8"))
            self.assertEqual("project_sync", payload["operation"])
            self.assertEqual("TOPIC-20260720-001", payload["project_id"])
            self.assertTrue(payload["request_id"].startswith("SYNC-TOPIC-20260720-001-"))

    def test_pull_project_enqueues_comment_refresh(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            publisher = FeishuDocPublisher(
                team_root=root / "team",
                queue_root=root / "team" / "queue",
                chat_id="oc_team",
            )

            result = publisher.pull_project(
                project_id="TOPIC-20260720-001",
                request_id="PULL-TOPIC-001",
            )

            self.assertEqual("queued", result["status"])
            payload = json.loads(
                (
                    root
                    / "team"
                    / "queue"
                    / "pending"
                    / "PULL-TOPIC-001.json"
                ).read_text(encoding="utf-8")
            )
            self.assertEqual("project_pull", payload["operation"])
            self.assertEqual("TOPIC-20260720-001", payload["project_id"])

    def test_build_project_source_collects_role_deliverables(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            team_root = root / "team"
            project = team_root / "03_视频剪辑项目" / "2026-07-20-测试项目"
            (project / "00_项目管理").mkdir(parents=True)
            (project / "01_编导").mkdir()
            (project / "02_拍摄").mkdir()
            (project / "00_项目管理" / "当前状态.md").write_text(
                "# 当前状态\n\n等待素材。\n", encoding="utf-8"
            )
            (project / "01_编导" / "脚本.md").write_text(
                "# 正式脚本\n\n脚本正文。\n", encoding="utf-8"
            )
            (project / "02_拍摄" / "补拍表.md").write_text(
                "# 补拍需求\n\n补拍两条。\n", encoding="utf-8"
            )
            publisher = FeishuDocPublisher(
                team_root=team_root,
                queue_root=team_root / "queue",
                chat_id="oc_team",
            )

            result = publisher.build_project_source(project)

            output = Path(result["source_path"])
            content = output.read_text(encoding="utf-8")
            self.assertIn("测试项目｜创意协作", content)
            self.assertIn("脚本正文", content)
            self.assertIn("补拍两条", content)
            self.assertLess(content.index("脚本正文"), content.index("补拍两条"))


if __name__ == "__main__":
    unittest.main()
