from __future__ import annotations

import json
import stat
import sys
import tempfile
import time
import unittest
from pathlib import Path

from creative_collab.cli import build_delivery_bridge
from creative_collab.feishu_relay import (
    FeishuRelayConfig,
    FeishuDirectorRelayBridge,
    RoutedThreadBridge,
    extract_final_agent_message,
    format_group_reply,
)
from creative_collab.service import CreativeCollabService, WorkflowError


class FeishuRelayConfigTests(unittest.TestCase):
    def test_loads_non_secret_group_binding(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "relay.json"
            path.write_text(
                json.dumps(
                    {
                        "director_thread_id": "thread-team-director",
                        "director_host_id": "feishu-creative",
                        "chat_id": "oc_team",
                        "lark_profile": "creative",
                        "lark_channel_home": "/tmp/lark-channel",
                        "lark_cli_config_dir": "/tmp/lark-channel/profiles/creative/lark-cli",
                    }
                ),
                encoding="utf-8",
            )

            config = FeishuRelayConfig.load(path)

            self.assertEqual("thread-team-director", config.director_thread_id)
            self.assertEqual("oc_team", config.chat_id)
            self.assertEqual("creative", config.lark_profile)

    def test_rejects_secret_fields_in_binding_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "relay.json"
            path.write_text(
                json.dumps(
                    {
                        "director_thread_id": "thread-team-director",
                        "director_host_id": "feishu-creative",
                        "chat_id": "oc_team",
                        "lark_profile": "creative",
                        "app_secret": "must-not-live-here",
                    }
                ),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "secret"):
                FeishuRelayConfig.load(path)


class RoutedThreadBridgeTests(unittest.TestCase):
    def test_only_team_director_dispatch_uses_group_relay(self) -> None:
        calls: list[tuple[str, dict[str, str]]] = []

        class FakeBridge:
            def __init__(self, name: str) -> None:
                self.name = name

            def send(self, **kwargs):
                calls.append((self.name, kwargs))
                return {"submission_id": f"{self.name}-receipt", **kwargs}

        bridge = RoutedThreadBridge(
            default_bridge=FakeBridge("default"),
            director_bridge=FakeBridge("feishu"),
            director_thread_id="thread-team-director",
            director_host_id="feishu-creative",
        )

        director_result = bridge.send(
            thread_id="thread-team-director",
            host_id="feishu-creative",
            message="请团队编导汇总",
            dispatch_id="DISPATCH-TEAM-001",
        )
        shooting_result = bridge.send(
            thread_id="thread-team-shooting",
            host_id="studio-mac",
            message="请拍摄核验素材",
            dispatch_id="DISPATCH-TEAM-002",
        )

        self.assertEqual(["feishu", "default"], [name for name, _ in calls])
        self.assertEqual("feishu-receipt", director_result["submission_id"])
        self.assertEqual("default-receipt", shooting_result["submission_id"])

    def test_new_group_director_thread_with_same_host_still_uses_group_relay(self) -> None:
        calls: list[str] = []

        class FakeBridge:
            def __init__(self, name: str) -> None:
                self.name = name

            def send(self, **kwargs):
                calls.append(self.name)
                return {"submission_id": self.name, **kwargs}

        bridge = RoutedThreadBridge(
            default_bridge=FakeBridge("default"),
            director_bridge=FakeBridge("feishu"),
            director_thread_id="thread-old",
            director_host_id="feishu-creative",
        )

        bridge.send(
            thread_id="thread-after-new",
            host_id="feishu-creative",
            message="请继续汇总",
            dispatch_id="DISPATCH-TEAM-NEW",
        )

        self.assertEqual(["feishu"], calls)


class RelayOutputTests(unittest.TestCase):
    def test_extracts_last_completed_agent_message_only(self) -> None:
        events = [
            json.dumps({"type": "thread.started", "thread_id": "thread-team-director"}),
            json.dumps(
                {
                    "type": "item.completed",
                    "item": {"type": "agent_message", "text": "我先核对回传结果。"},
                }
            ),
            json.dumps(
                {
                    "type": "item.completed",
                    "item": {"type": "command_execution", "aggregated_output": "internal log"},
                }
            ),
            json.dumps(
                {
                    "type": "item.completed",
                    "item": {
                        "type": "agent_message",
                        "text": "素材还缺一条真人口播，已暂停剪辑。",
                    },
                }
            ),
        ]

        self.assertEqual(
            "素材还缺一条真人口播，已暂停剪辑。",
            extract_final_agent_message(events),
        )

    def test_group_reply_is_short_and_identifies_team_director(self) -> None:
        reply = format_group_reply("素材齐套，已经交给剪辑。")

        self.assertEqual("🎬 **团队编导**\n\n素材齐套，已经交给剪辑。", reply)


class FeishuDirectorRelayBridgeTests(unittest.TestCase):
    def test_relay_resumes_director_and_posts_only_final_reply_to_group(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            fake_codex = root / "fake-codex"
            fake_lark = root / "fake-lark"
            lark_args = root / "lark-args.json"
            lark_env = root / "lark-env.json"
            fake_codex.write_text(
                "#!/bin/sh\n"
                "printf '%s\\n' "
                "'{\"type\":\"thread.started\",\"thread_id\":\"thread-team-director\"}' "
                "'{\"type\":\"item.completed\",\"item\":{\"type\":\"command_execution\",\"aggregated_output\":\"private path /tmp/a\"}}' "
                "'{\"type\":\"item.completed\",\"item\":{\"type\":\"agent_message\",\"text\":\"已汇总素材缺口，剪辑保持暂停。\"}}'\n",
                encoding="utf-8",
            )
            fake_lark.write_text(
                "#!/usr/bin/env python3\n"
                "import json\n"
                "import os\n"
                "import sys\n"
                f"open({str(lark_args)!r}, 'w').write(json.dumps(sys.argv[1:]))\n"
                f"open({str(lark_env)!r}, 'w').write(json.dumps({{"
                "k: os.environ.get(k) for k in "
                "['LARK_CHANNEL_HOME', 'LARK_CHANNEL_PROFILE', 'LARKSUITE_CLI_CONFIG_DIR']"
                "}))\n",
                encoding="utf-8",
            )
            for executable in (fake_codex, fake_lark):
                executable.chmod(executable.stat().st_mode | stat.S_IXUSR)

            config = FeishuRelayConfig(
                director_thread_id="thread-team-director",
                director_host_id="feishu-creative",
                chat_id="oc_team",
                lark_profile="creative",
                lark_channel_home=root / "lark-channel",
                lark_cli_config_dir=root / "lark-channel/profiles/creative/lark-cli",
            )
            bridge = FeishuDirectorRelayBridge(
                config=config,
                python_binary=Path(sys.executable),
                codex_binary=fake_codex,
                lark_binary=fake_lark,
                spool_root=root / "spool",
                working_directory=root,
                writable_roots=[root / "registry"],
                startup_timeout_seconds=2,
            )

            receipt = bridge.send(
                thread_id="thread-team-director",
                host_id="feishu-creative",
                message="请汇总拍摄反馈",
                dispatch_id="DISPATCH-TEAM-003",
            )

            deadline = time.monotonic() + 3
            while time.monotonic() < deadline and not lark_args.exists():
                time.sleep(0.05)
            self.assertTrue(lark_args.exists(), "relay did not invoke lark-cli")
            args = json.loads(lark_args.read_text(encoding="utf-8"))
            self.assertIn("oc_team", args)
            markdown = args[args.index("--markdown") + 1]
            self.assertIn("已汇总素材缺口，剪辑保持暂停。", markdown)
            self.assertNotIn("private path", markdown)
            env = json.loads(lark_env.read_text(encoding="utf-8"))
            self.assertEqual("creative", env["LARK_CHANNEL_PROFILE"])
            self.assertEqual(str(config.lark_channel_home), env["LARK_CHANNEL_HOME"])
            self.assertEqual(str(config.lark_cli_config_dir), env["LARKSUITE_CLI_CONFIG_DIR"])
            self.assertTrue(receipt["submission_id"].startswith("feishu-director-relay-"))
            self.assertEqual("DISPATCH-TEAM-003", receipt["dispatch_id"])
            self.assertTrue((root / "spool" / ".director-relay.lock").exists())


class TeamCliIntegrationTests(unittest.TestCase):
    def test_build_delivery_bridge_routes_only_configured_director_to_group(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            relay_path = root / "relay.json"
            relay_path.write_text(
                json.dumps(
                    {
                        "director_thread_id": "thread-team-director",
                        "director_host_id": "feishu-creative",
                        "chat_id": "oc_team",
                        "lark_profile": "creative",
                        "lark_channel_home": str(root / "lark-channel"),
                        "lark_cli_config_dir": str(root / "lark-cli"),
                    }
                ),
                encoding="utf-8",
            )
            service = CreativeCollabService(
                db_path=root / "registry.sqlite",
                creative_root=root / "team-root",
            )

            bridge = build_delivery_bridge(
                service=service,
                relay_config_path=relay_path,
                codex_binary=root / "codex",
                startup_timeout_seconds=2,
            )

            self.assertIsInstance(bridge, RoutedThreadBridge)
            self.assertEqual("thread-team-director", bridge.director_thread_id)
            self.assertIsInstance(bridge.director_bridge, FeishuDirectorRelayBridge)
            self.assertEqual("oc_team", bridge.director_bridge.config.chat_id)
            operations_root = service.creative_root / "00_协作账本" / "运行记录"
            self.assertEqual(
                operations_root / "thread-bridge",
                bridge.default_bridge.log_dir,
            )
            self.assertEqual(
                operations_root / "relay",
                bridge.director_bridge.spool_root,
            )

    def test_deployed_team_runner_keeps_database_inside_team_workspace(self) -> None:
        source = (
            Path(__file__).resolve().parents[1]
            / "deploy"
            / "feishu_creative_team"
            / "creative-collab-team-runner.py"
        ).read_text(encoding="utf-8")

        self.assertIn(
            'TEAM_ROOT / "00_协作账本" / "creative-collab.sqlite"',
            source,
        )
        self.assertNotIn(
            '"/path/to/home/.codex/agent-collab/feishu-creative.sqlite"',
            source,
        )

    def test_team_requester_label_replaces_personal_name_in_user_errors(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            service = CreativeCollabService(
                db_path=root / "registry.sqlite",
                creative_root=root / "team-root",
                requester_label="团队制作人",
            )
            service.bootstrap_v01(request_id="TEAM-BOOTSTRAP")
            project = service.project_create(
                request_id="TEAM-PROJECT",
                title="团队测试",
                owner_role="编导",
                brief="验证团队称呼",
                tags=["测试"],
                requirements_required=True,
            )

            with self.assertRaisesRegex(WorkflowError, "团队制作人") as raised:
                service.requirements_confirm(
                    request_id="TEAM-CONFIRM",
                    project_id=project["project_id"],
                    role="编导",
                    confirmation_note="确认",
                )
            self.assertNotIn("任务发起人", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
