from __future__ import annotations

import json
import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from creative_collab.cli import deliver_dispatch, register_current_thread
from creative_collab.service import CreativeCollabService
from creative_collab.thread_bridge import CodexThreadBridge


class CodexThreadBridgeTests(unittest.TestCase):
    def test_send_starts_resume_process_and_returns_persistent_receipt(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            args_path = root / "args.json"
            cwd_path = root / "cwd.txt"
            env_path = root / "env.json"
            target_cwd = root / "creative-root"
            target_cwd.mkdir()
            fake_codex = root / "fake-codex"
            fake_codex.write_text(
                "#!/bin/sh\n"
                "python3 -c 'import json,os,sys; "
                "open(os.environ[\"FAKE_ARGS_PATH\"], \"w\").write(json.dumps(sys.argv[1:])); "
                "open(os.environ[\"FAKE_CWD_PATH\"], \"w\").write(os.getcwd()); "
                "open(os.environ[\"FAKE_ENV_PATH\"], \"w\").write(json.dumps({k:os.environ.get(k) for k in [\"HTTP_PROXY\",\"HTTPS_PROXY\",\"ALL_PROXY\",\"http_proxy\",\"https_proxy\",\"all_proxy\",\"NO_PROXY\",\"no_proxy\"]})); "
                "print(json.dumps({\"type\": \"thread.started\", \"thread_id\": sys.argv[-2]}), flush=True)' "
                '"$@"\n',
                encoding="utf-8",
            )
            fake_codex.chmod(fake_codex.stat().st_mode | stat.S_IXUSR)

            previous = os.environ.get("FAKE_ARGS_PATH")
            previous_cwd_path = os.environ.get("FAKE_CWD_PATH")
            previous_env_path = os.environ.get("FAKE_ENV_PATH")
            os.environ["FAKE_ARGS_PATH"] = str(args_path)
            os.environ["FAKE_CWD_PATH"] = str(cwd_path)
            os.environ["FAKE_ENV_PATH"] = str(env_path)
            try:
                result = CodexThreadBridge(
                    codex_binary=fake_codex,
                    log_dir=root / "logs",
                    startup_timeout_seconds=2,
                    working_directory=target_cwd,
                    writable_roots=[root / "registry"],
                    proxy_url="http://127.0.0.1:7897",
                ).send(
                    thread_id="thread-shooting",
                    host_id="host-local",
                    message="执行拍摄任务",
                    dispatch_id="DISPATCH-001",
                )
            finally:
                if previous is None:
                    os.environ.pop("FAKE_ARGS_PATH", None)
                else:
                    os.environ["FAKE_ARGS_PATH"] = previous
                if previous_cwd_path is None:
                    os.environ.pop("FAKE_CWD_PATH", None)
                else:
                    os.environ["FAKE_CWD_PATH"] = previous_cwd_path
                if previous_env_path is None:
                    os.environ.pop("FAKE_ENV_PATH", None)
                else:
                    os.environ["FAKE_ENV_PATH"] = previous_env_path

            args = json.loads(args_path.read_text(encoding="utf-8"))
            self.assertEqual(
                args,
                [
                    "exec",
                    "--add-dir",
                    str((root / "registry").resolve()),
                    "resume",
                    "--json",
                    "--all",
                    "--skip-git-repo-check",
                    "thread-shooting",
                    "执行拍摄任务",
                ],
            )
            self.assertEqual(result["thread_id"], "thread-shooting")
            self.assertEqual(result["host_id"], "host-local")
            self.assertEqual(result["dispatch_id"], "DISPATCH-001")
            self.assertTrue(result["submission_id"].startswith("codex-resume-"))
            self.assertTrue(Path(result["stdout_log"]).exists())
            self.assertTrue(Path(result["stderr_log"]).exists())
            self.assertEqual(
                Path(cwd_path.read_text(encoding="utf-8")).resolve(),
                target_cwd.resolve(),
            )
            child_env = json.loads(env_path.read_text(encoding="utf-8"))
            for key in (
                "HTTP_PROXY",
                "HTTPS_PROXY",
                "ALL_PROXY",
                "http_proxy",
                "https_proxy",
                "all_proxy",
            ):
                self.assertEqual("http://127.0.0.1:7897", child_env[key])
            for key in ("NO_PROXY", "no_proxy"):
                no_proxy_entries = {
                    entry.strip()
                    for entry in child_env[key].split(",")
                    if entry.strip()
                }
                self.assertTrue(
                    {"127.0.0.1", "localhost", "::1"}.issubset(no_proxy_entries)
                )

    def test_send_raises_when_codex_exits_before_accepting_message(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            fake_codex = root / "fake-codex"
            fake_codex.write_text("#!/bin/sh\necho failed >&2\nexit 7\n", encoding="utf-8")
            fake_codex.chmod(fake_codex.stat().st_mode | stat.S_IXUSR)

            bridge = CodexThreadBridge(
                codex_binary=fake_codex,
                log_dir=root / "logs",
                startup_timeout_seconds=2,
            )
            with self.assertRaisesRegex(RuntimeError, "failed"):
                bridge.send(
                    thread_id="thread-shooting",
                    host_id="host-local",
                    message="执行拍摄任务",
                    dispatch_id="DISPATCH-002",
                )


class DeliverDispatchTests(unittest.TestCase):
    def test_deliver_dispatch_prepares_sends_and_marks_sent(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            service = CreativeCollabService(
                db_path=root / "registry.sqlite",
                creative_root=root / "创意部",
            )
            service.bootstrap_v01(request_id="TEST-BOOTSTRAP")
            service.agent_bind_thread(
                request_id="TEST-BIND-SHOOTING",
                role="拍摄",
                thread_id="thread-shooting",
                host_id="host-local",
            )
            project = service.project_create(
                request_id="TEST-PROJECT",
                title="桥接验收",
                owner_role="编导",
                brief="验证真实派发桥接",
                tags=["测试"],
            )
            task = service.task_assign(
                request_id="TEST-TASK",
                project_id=project["project_id"],
                from_role="编导",
                to_role="拍摄",
                summary="检索素材",
                inputs=["01_编导/脚本.md"],
                acceptance_criteria=["输出素材匹配表"],
            )
            dispatch = next(
                item
                for item in service.dispatch_list(project_id=project["project_id"])
                if item["entity_id"] == task["task_id"]
            )

            class FakeBridge:
                def send(self, **kwargs):
                    self.arguments = kwargs
                    return {
                        "submission_id": "codex-resume-test-receipt",
                        "thread_id": kwargs["thread_id"],
                        "host_id": kwargs["host_id"],
                        "dispatch_id": kwargs["dispatch_id"],
                    }

            bridge = FakeBridge()
            result = deliver_dispatch(
                service,
                request_id="TEST-DELIVER",
                dispatch_id=dispatch["dispatch_id"],
                role="编导",
                bridge=bridge,
            )

            self.assertEqual(bridge.arguments["thread_id"], "thread-shooting")
            self.assertIn(dispatch["dispatch_id"], bridge.arguments["message"])
            self.assertIn("不得直接调用飞书接口", bridge.arguments["message"])
            self.assertIn("send-dispatch", bridge.arguments["message"])
            self.assertIn("不得停止", bridge.arguments["message"])
            self.assertEqual(result["dispatch"]["status"], "sent")
            self.assertEqual(
                result["dispatch"]["submission_id"], "codex-resume-test-receipt"
            )


class RegisterCurrentThreadTests(unittest.TestCase):
    def test_register_current_thread_adds_director_without_replacing_existing(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            service = CreativeCollabService(
                db_path=root / "registry.sqlite",
                creative_root=root / "创意部",
            )
            service.bootstrap_v01(request_id="TEST-BOOTSTRAP")
            service.agent_bind_thread(
                request_id="TEST-BIND-OLD-DIRECTOR",
                role="编导",
                thread_id="thread-old-director",
                host_id="local",
            )

            with mock.patch.dict(
                os.environ, {"CODEX_THREAD_ID": "thread-new-director"}
            ):
                result = register_current_thread(
                    service,
                    role="编导",
                    host_id="local",
                )

            self.assertEqual("编导", result["role"])
            self.assertEqual("thread-new-director", result["thread_id"])
            self.assertEqual("local", result["host_id"])
            self.assertFalse(result["replaced_existing"])
            bindings = service.agent_thread_list(role="编导")
            self.assertEqual(
                {"thread-old-director", "thread-new-director"},
                {binding["thread_id"] for binding in bindings if binding["status"] == "active"},
            )
            primary = {
                agent["role"]: agent for agent in service.agent_list()
            }["编导"]
            self.assertEqual("thread-old-director", primary["thread_id"])

    def test_register_current_thread_replaces_only_when_explicitly_requested(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            service = CreativeCollabService(
                db_path=root / "registry.sqlite",
                creative_root=root / "创意部",
            )
            service.bootstrap_v01(request_id="TEST-BOOTSTRAP")
            service.agent_bind_thread(
                request_id="TEST-BIND-OLD-DIRECTOR",
                role="编导",
                thread_id="thread-old-director",
                host_id="local",
            )

            with mock.patch.dict(
                os.environ, {"CODEX_THREAD_ID": "thread-new-director"}
            ):
                result = register_current_thread(
                    service,
                    role="编导",
                    host_id="local",
                    replace_existing=True,
                )

            self.assertTrue(result["replaced_existing"])
            bindings = {
                binding["thread_id"]: binding
                for binding in service.agent_thread_list(role="编导")
            }
            self.assertEqual("inactive", bindings["thread-old-director"]["status"])
            self.assertEqual("active", bindings["thread-new-director"]["status"])
            primary = {
                agent["role"]: agent for agent in service.agent_list()
            }["编导"]
            self.assertEqual("thread-new-director", primary["thread_id"])

    def test_register_current_thread_rejects_missing_codex_thread_context(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            service = CreativeCollabService(
                db_path=root / "registry.sqlite",
                creative_root=root / "创意部",
            )
            service.bootstrap_v01(request_id="TEST-BOOTSTRAP")

            with mock.patch.dict(os.environ, {}, clear=True):
                with self.assertRaisesRegex(
                    RuntimeError, "当前 Codex 任务"
                ):
                    register_current_thread(service, role="编导")


if __name__ == "__main__":
    unittest.main()
