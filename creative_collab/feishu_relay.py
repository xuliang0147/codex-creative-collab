from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, Optional, Protocol


class ThreadBridge(Protocol):
    def send(
        self,
        thread_id: str,
        host_id: str,
        message: str,
        dispatch_id: str,
    ) -> Dict[str, Any]: ...


@dataclass(frozen=True)
class FeishuRelayConfig:
    director_thread_id: str
    director_host_id: str
    chat_id: str
    lark_profile: str
    lark_channel_home: Path
    lark_cli_config_dir: Path

    @classmethod
    def load(cls, path: Path) -> "FeishuRelayConfig":
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError("Feishu relay config must be a JSON object")
        if any("secret" in str(key).lower() or "token" in str(key).lower() for key in raw):
            raise ValueError("Feishu relay config must not contain secret or token fields")

        required = (
            "director_thread_id",
            "director_host_id",
            "chat_id",
            "lark_profile",
            "lark_channel_home",
            "lark_cli_config_dir",
        )
        values: dict[str, str] = {}
        for key in required:
            value = raw.get(key)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"Feishu relay config field cannot be blank: {key}")
            values[key] = value.strip()

        return cls(
            director_thread_id=values["director_thread_id"],
            director_host_id=values["director_host_id"],
            chat_id=values["chat_id"],
            lark_profile=values["lark_profile"],
            lark_channel_home=Path(values["lark_channel_home"]).expanduser(),
            lark_cli_config_dir=Path(values["lark_cli_config_dir"]).expanduser(),
        )


class RoutedThreadBridge:
    def __init__(
        self,
        default_bridge: ThreadBridge,
        director_bridge: ThreadBridge,
        director_thread_id: str,
        director_host_id: str,
    ) -> None:
        self.default_bridge = default_bridge
        self.director_bridge = director_bridge
        self.director_thread_id = director_thread_id.strip()
        self.director_host_id = director_host_id.strip()

    def send(
        self,
        thread_id: str,
        host_id: str,
        message: str,
        dispatch_id: str,
    ) -> Dict[str, Any]:
        target = (
            self.director_bridge
            if (
                thread_id.strip() == self.director_thread_id
                or host_id.strip() == self.director_host_id
            )
            else self.default_bridge
        )
        return target.send(
            thread_id=thread_id,
            host_id=host_id,
            message=message,
            dispatch_id=dispatch_id,
        )


class FeishuDirectorRelayBridge:
    """Resume the team director and relay its final answer to the Feishu group."""

    def __init__(
        self,
        config: FeishuRelayConfig,
        python_binary: Path = Path(sys.executable),
        codex_binary: Path = Path("/Applications/ChatGPT.app/Contents/Resources/codex"),
        lark_binary: Path = Path("/usr/local/bin/lark-cli"),
        spool_root: Path = Path.home() / ".codex" / "agent-collab-feishu" / "relay",
        working_directory: Optional[Path] = None,
        writable_roots: Optional[Iterable[Path]] = None,
        startup_timeout_seconds: float = 15,
    ) -> None:
        self.config = config
        self.python_binary = Path(python_binary)
        self.codex_binary = Path(codex_binary)
        self.lark_binary = Path(lark_binary)
        self.spool_root = Path(spool_root)
        self.working_directory = Path(working_directory) if working_directory else None
        self.writable_roots = tuple(
            Path(path).expanduser().resolve() for path in (writable_roots or ())
        )
        self.startup_timeout_seconds = startup_timeout_seconds

    def send(
        self,
        thread_id: str,
        host_id: str,
        message: str,
        dispatch_id: str,
    ) -> Dict[str, Any]:
        for name, value in (
            ("thread_id", thread_id),
            ("host_id", host_id),
            ("message", message),
            ("dispatch_id", dispatch_id),
        ):
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} cannot be blank")
        if (
            thread_id.strip() != self.config.director_thread_id
            and host_id.strip() != self.config.director_host_id
        ):
            raise ValueError("Feishu relay can only target the configured team director")

        submission_id = "feishu-director-relay-" + uuid.uuid4().hex
        run_root = self.spool_root / submission_id
        run_root.mkdir(parents=True, exist_ok=False)
        message_file = run_root / "message.txt"
        stdout_log = run_root / "relay.jsonl"
        stderr_log = run_root / "relay.stderr.log"
        message_file.write_text(message, encoding="utf-8")
        message_file.chmod(0o600)

        command = [
            str(self.python_binary),
            "-m",
            "creative_collab.feishu_relay_worker",
            "--thread-id",
            thread_id.strip(),
            "--host-id",
            host_id.strip(),
            "--dispatch-id",
            dispatch_id.strip(),
            "--message-file",
            str(message_file),
            "--chat-id",
            self.config.chat_id,
            "--lark-profile",
            self.config.lark_profile,
            "--lark-channel-home",
            str(self.config.lark_channel_home),
            "--lark-cli-config-dir",
            str(self.config.lark_cli_config_dir),
            "--codex-binary",
            str(self.codex_binary),
            "--lark-binary",
            str(self.lark_binary),
            "--lock-file",
            str(self.spool_root / ".director-relay.lock"),
        ]
        for writable_root in self.writable_roots:
            command.extend(["--add-dir", str(writable_root)])

        env = os.environ.copy()
        workspace = str(Path(__file__).resolve().parent.parent)
        current_pythonpath = env.get("PYTHONPATH", "").strip()
        env["PYTHONPATH"] = (
            workspace if not current_pythonpath else workspace + os.pathsep + current_pythonpath
        )
        with stdout_log.open("ab", buffering=0) as stdout_handle, stderr_log.open(
            "ab", buffering=0
        ) as stderr_handle:
            process = subprocess.Popen(
                command,
                cwd=self.working_directory,
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=stdout_handle,
                stderr=stderr_handle,
                start_new_session=True,
                close_fds=True,
            )

        accepted_event = self._wait_for_started(process, stdout_log, stderr_log)
        process.returncode = 0
        return {
            "submission_id": submission_id,
            "dispatch_id": dispatch_id.strip(),
            "thread_id": thread_id.strip(),
            "host_id": host_id.strip(),
            "pid": process.pid,
            "stdout_log": str(stdout_log),
            "stderr_log": str(stderr_log),
            "accepted_event": accepted_event,
            "relay": "feishu-group",
        }

    def _wait_for_started(
        self,
        process: subprocess.Popen,
        stdout_log: Path,
        stderr_log: Path,
    ) -> Dict[str, Any]:
        deadline = time.monotonic() + self.startup_timeout_seconds
        while time.monotonic() < deadline:
            if stdout_log.exists():
                for line in stdout_log.read_text(encoding="utf-8", errors="replace").splitlines():
                    try:
                        event = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if event.get("type") == "relay.started":
                        return event
            return_code = process.poll()
            if return_code is not None:
                stderr = stderr_log.read_text(encoding="utf-8", errors="replace")
                raise RuntimeError(
                    f"Feishu director relay exited before acceptance ({return_code}): {stderr}"
                )
            time.sleep(0.05)
        process.terminate()
        stderr = stderr_log.read_text(encoding="utf-8", errors="replace")
        raise RuntimeError(
            "Feishu director relay did not start within "
            f"{self.startup_timeout_seconds:g}s: {stderr}"
        )


def extract_final_agent_message(lines: Iterable[str]) -> str:
    final_message = ""
    for line in lines:
        try:
            event = json.loads(line)
        except (json.JSONDecodeError, TypeError):
            continue
        if event.get("type") != "item.completed":
            continue
        item = event.get("item")
        if not isinstance(item, dict) or item.get("type") != "agent_message":
            continue
        text = item.get("text")
        if isinstance(text, str) and text.strip():
            final_message = text.strip()
    return final_message


def format_group_reply(message: str) -> str:
    text = message.strip()
    if not text:
        raise ValueError("group reply cannot be blank")
    return f"🎬 **团队编导**\n\n{text}"
