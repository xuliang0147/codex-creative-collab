from __future__ import annotations

import json
import os
import signal
import subprocess
import time
import uuid
from pathlib import Path
from typing import Any, Dict, Iterable, Optional


DEFAULT_CODEX_BINARY = Path("/Applications/ChatGPT.app/Contents/Resources/codex")
DEFAULT_LOG_DIR = Path.home() / ".codex" / "agent-collab" / "logs"
DEFAULT_PROXY_ENV_FILE = Path.home() / ".codex" / "env"


class CodexThreadBridge:
    """Submit a prompt to an existing Codex thread without blocking the sender."""

    def __init__(
        self,
        codex_binary: Path = DEFAULT_CODEX_BINARY,
        log_dir: Path = DEFAULT_LOG_DIR,
        startup_timeout_seconds: float = 15,
        working_directory: Optional[Path] = None,
        writable_roots: Optional[Iterable[Path]] = None,
        proxy_url: Optional[str] = None,
    ) -> None:
        self.codex_binary = Path(codex_binary)
        self.log_dir = Path(log_dir)
        self.startup_timeout_seconds = startup_timeout_seconds
        self.working_directory = (
            Path(working_directory) if working_directory is not None else None
        )
        self.writable_roots = tuple(
            Path(path).expanduser().resolve() for path in (writable_roots or ())
        )
        self.proxy_url = proxy_url or self._configured_proxy_url()

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

        submission_id = "codex-resume-" + uuid.uuid4().hex
        self.log_dir.mkdir(parents=True, exist_ok=True)
        stdout_log = self.log_dir / f"{submission_id}.jsonl"
        stderr_log = self.log_dir / f"{submission_id}.stderr.log"
        command = [
            str(self.codex_binary),
            "exec",
        ]
        for writable_root in self.writable_roots:
            command.extend(["--add-dir", str(writable_root)])
        command.extend(
            [
                "resume",
                "--json",
                "--all",
                "--skip-git-repo-check",
                thread_id.strip(),
                message,
            ]
        )

        with stdout_log.open("ab", buffering=0) as stdout_handle, stderr_log.open(
            "ab", buffering=0
        ) as stderr_handle:
            process = subprocess.Popen(
                command,
                cwd=self.working_directory,
                env=self._child_env(),
                stdout=stdout_handle,
                stderr=stderr_handle,
                stdin=subprocess.DEVNULL,
                start_new_session=True,
                close_fds=True,
            )

        accepted_event = self._wait_for_acceptance(process, stdout_log, stderr_log)
        # The child is deliberately detached and owns its log handles. Mark the
        # local Popen wrapper complete so Python does not emit a false leak warning.
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
        }

    def _wait_for_acceptance(
        self, process: subprocess.Popen, stdout_log: Path, stderr_log: Path
    ) -> Optional[Dict[str, Any]]:
        deadline = time.monotonic() + self.startup_timeout_seconds
        while time.monotonic() < deadline:
            event = self._first_json_event(stdout_log)
            if event is not None:
                return event
            return_code = process.poll()
            if return_code is not None:
                stderr = self._read_text(stderr_log)
                if return_code != 0:
                    raise RuntimeError(
                        f"Codex thread submission failed with exit {return_code}: {stderr}"
                    )
                raise RuntimeError("Codex thread submission exited without an acceptance event")
            time.sleep(0.05)

        if process.poll() is None:
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
        stderr = self._read_text(stderr_log)
        raise RuntimeError(
            "Codex thread submission did not start within "
            f"{self.startup_timeout_seconds:g}s: {stderr}"
        )

    def _child_env(self) -> Dict[str, str]:
        child_env = os.environ.copy()
        if not self.proxy_url:
            return child_env
        no_proxy = (
            self._env_file_value("NO_PROXY")
            or child_env.get("NO_PROXY")
            or child_env.get("no_proxy")
            or "127.0.0.1,localhost,::1"
        )
        for key in (
            "HTTP_PROXY",
            "HTTPS_PROXY",
            "ALL_PROXY",
            "http_proxy",
            "https_proxy",
            "all_proxy",
        ):
            child_env[key] = self.proxy_url
        child_env["NO_PROXY"] = no_proxy
        child_env["no_proxy"] = no_proxy
        return child_env

    @classmethod
    def _configured_proxy_url(cls) -> Optional[str]:
        return os.environ.get("CODEX_PROXY_URL") or cls._env_file_value(
            "CODEX_PROXY_URL"
        )

    @staticmethod
    def _env_file_value(key: str) -> Optional[str]:
        if not DEFAULT_PROXY_ENV_FILE.exists():
            return None
        for raw_line in DEFAULT_PROXY_ENV_FILE.read_text(
            encoding="utf-8", errors="replace"
        ).splitlines():
            line = raw_line.split("#", 1)[0].strip()
            if not line or "=" not in line:
                continue
            current_key, value = line.split("=", 1)
            if current_key.strip() == key:
                return value.strip().strip("\"'") or None
        return None

    @staticmethod
    def _first_json_event(path: Path) -> Optional[Dict[str, Any]]:
        if not path.exists() or path.stat().st_size == 0:
            return None
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(event, dict):
                return event
        return None

    @staticmethod
    def _read_text(path: Path) -> str:
        if not path.exists():
            return ""
        return path.read_text(encoding="utf-8", errors="replace").strip()
