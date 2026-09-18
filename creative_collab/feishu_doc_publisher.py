from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import time
import traceback
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Sequence


REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$")
PROJECT_ID_PATTERN = re.compile(r"^TOPIC-[A-Za-z0-9-]{1,64}$")
MAX_SOURCE_BYTES = 2 * 1024 * 1024
DEFAULT_TEAM_ROOT = Path.home() / "codex-creative-team"
DEFAULT_CHAT_ID = ""
DEFAULT_LARK_BINARY = Path("/usr/local/bin/lark-cli")


CommandRunner = Callable[
    [Sequence[str], Path, Dict[str, str]], subprocess.CompletedProcess
]


def _run_command(
    args: Sequence[str], cwd: Path, env: Dict[str, str]
) -> subprocess.CompletedProcess:
    return subprocess.run(
        list(args),
        cwd=str(cwd),
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=180,
        check=False,
    )


class FeishuDocPublisher:
    def __init__(
        self,
        team_root: Path = DEFAULT_TEAM_ROOT,
        queue_root: Optional[Path] = None,
        chat_id: str = DEFAULT_CHAT_ID,
        lark_binary: Path = DEFAULT_LARK_BINARY,
        command_runner: CommandRunner = _run_command,
    ) -> None:
        self.team_root = Path(team_root).expanduser().resolve()
        self.queue_root = (
            Path(queue_root).expanduser().resolve()
            if queue_root is not None
            else self.team_root / "00_协作账本" / "飞书文档发布队列"
        )
        self.chat_id = chat_id.strip()
        self.lark_binary = Path(lark_binary)
        self.command_runner = command_runner
        if not self.chat_id:
            raise ValueError("chat_id cannot be blank")

    def enqueue(
        self,
        request_id: str,
        source_path: Path,
        doc_format: str,
        title: Optional[str] = None,
    ) -> Dict[str, Any]:
        request_id = self._validate_request_id(request_id)
        source = self._validate_source(source_path, doc_format)
        result_path = self._result_path(request_id)
        if result_path.exists():
            return json.loads(result_path.read_text(encoding="utf-8"))

        pending_path = self.queue_root / "pending" / f"{request_id}.json"
        processing_path = self.queue_root / "processing" / f"{request_id}.json"
        if pending_path.exists() or processing_path.exists():
            return {"request_id": request_id, "status": "queued"}

        payload = {
            "request_id": request_id,
            "source_path": str(source.relative_to(self.team_root)),
            "doc_format": doc_format.strip().lower(),
            "title": (title or "").strip(),
            "created_at": self._now(),
        }
        self._write_json_atomic(pending_path, payload)
        return {"request_id": request_id, "status": "queued"}

    def sync_project(
        self,
        project_id: str,
        source_path: Path,
        doc_format: str,
        title: str,
    ) -> Dict[str, Any]:
        project_id = self._validate_project_id(project_id)
        source = self._validate_source(source_path, doc_format)
        digest = hashlib.sha256(source.read_bytes()).hexdigest()[:16]
        request_id = self._validate_request_id(f"SYNC-{project_id}-{digest}")
        payload = {
            "operation": "project_sync",
            "request_id": request_id,
            "project_id": project_id,
            "source_path": str(source.relative_to(self.team_root)),
            "doc_format": doc_format.strip().lower(),
            "title": title.strip(),
            "created_at": self._now(),
        }
        return self._enqueue_payload(payload)

    def pull_project(
        self, project_id: str, request_id: Optional[str] = None
    ) -> Dict[str, Any]:
        project_id = self._validate_project_id(project_id)
        request_id = self._validate_request_id(
            request_id
            or f"PULL-{project_id}-{datetime.now().strftime('%Y%m%d%H%M%S')}"
        )
        return self._enqueue_payload(
            {
                "operation": "project_pull",
                "request_id": request_id,
                "project_id": project_id,
                "created_at": self._now(),
            }
        )

    def build_project_source(self, project_root: Path) -> Dict[str, Any]:
        project = Path(project_root).expanduser()
        if not project.is_absolute():
            project = self.team_root / project
        project = project.resolve()
        try:
            project.relative_to(self.team_root)
        except ValueError as exc:
            raise ValueError("项目目录必须位于飞书团队目录内") from exc
        if not project.is_dir():
            raise ValueError("project directory does not exist")

        manager = project / "00_项目管理"
        manager.mkdir(parents=True, exist_ok=True)
        output = manager / "项目协作文档.md"
        section_order = (
            ("项目状态", manager),
            ("编导交付", project / "01_编导"),
            ("拍摄交付", project / "02_拍摄"),
            ("平面交付", project / "03_平面"),
            ("剪辑交付", project / "04_剪辑"),
            ("编导审核", project / "05_编导审核"),
        )
        project_title = re.sub(r"^\d{4}-\d{2}-\d{2}-", "", project.name)
        lines = [
            f"# {project_title}｜创意协作",
            "",
            "> 本文档是本项目统一协作入口。需求、脚本、素材反馈、平面方案、剪辑交付、审核与退回均在此汇总；请直接使用飞书评论提出修改意见。",
            "> 群内只返回当前阶段、本文档链接和下一步，不再展开正文或执行过程。",
            "",
        ]
        included: list[str] = []
        for section_title, directory in section_order:
            if not directory.is_dir():
                continue
            candidates = sorted(directory.rglob("*.md"))
            if directory == manager:
                candidates = [
                    path
                    for path in candidates
                    if path.name in {"当前状态.md", "项目卡.md", "待你处理.md"}
                ]
            candidates = [path for path in candidates if path.resolve() != output]
            if not candidates:
                continue
            lines.extend([f"## {section_title}", ""])
            for source in candidates:
                body = source.read_text(encoding="utf-8").strip()
                if not body:
                    continue
                body_lines = body.splitlines()
                adjusted = "\n".join(
                    ("##" + line if line.startswith("#") else line)
                    for line in body_lines
                )
                if not body_lines[0].startswith("# "):
                    lines.extend([f"### {source.stem}", ""])
                lines.extend([adjusted, "", "---", ""])
                included.append(str(source.relative_to(project)))
        output.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
        return {
            "status": "built",
            "source_path": str(output),
            "relative_path": str(output.relative_to(self.team_root)),
            "title": f"{project_title}｜创意协作",
            "included_files": included,
        }

    def wait_for_result(
        self, request_id: str, timeout_seconds: float = 120
    ) -> Dict[str, Any]:
        request_id = self._validate_request_id(request_id)
        deadline = time.monotonic() + max(0, timeout_seconds)
        result_path = self._result_path(request_id)
        while time.monotonic() <= deadline:
            if result_path.exists():
                return json.loads(result_path.read_text(encoding="utf-8"))
            time.sleep(0.2)
        return {
            "request_id": request_id,
            "status": "queued",
            "message": "文档正在创建，请稍后查询结果。",
        }

    def process_next(self) -> Optional[Dict[str, Any]]:
        pending_dir = self.queue_root / "pending"
        pending_dir.mkdir(parents=True, exist_ok=True)
        pending = sorted(pending_dir.glob("*.json"))
        if not pending:
            return None

        request_path = pending[0]
        processing_path = self.queue_root / "processing" / request_path.name
        processing_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.replace(str(request_path), str(processing_path))
        except FileNotFoundError:
            return None

        request: Dict[str, Any] = {}
        try:
            request = json.loads(processing_path.read_text(encoding="utf-8"))
            result = self._publish(request)
        except Exception:
            traceback.print_exc()
            result = {
                "request_id": str(request.get("request_id") or processing_path.stem),
                "status": "failed",
                "message": "飞书云文档创建失败，已保留源文件，请稍后重试。",
                "completed_at": self._now(),
            }

        self._write_json_atomic(self._result_path(result["request_id"]), result)
        processed_path = self.queue_root / "processed" / processing_path.name
        processed_path.parent.mkdir(parents=True, exist_ok=True)
        os.replace(str(processing_path), str(processed_path))
        return result

    def serve(self, poll_seconds: float = 0.5) -> None:
        while True:
            result = self.process_next()
            if result is None:
                time.sleep(max(0.1, poll_seconds))

    def _publish(self, request: Dict[str, Any]) -> Dict[str, Any]:
        request_id = self._validate_request_id(str(request.get("request_id", "")))
        doc_format = str(request.get("doc_format", "")).strip().lower()
        source = self._validate_source(
            self.team_root / str(request.get("source_path", "")), doc_format
        )
        create_args = [
            str(self.lark_binary),
            "docs",
            "+create",
            "--api-version",
            "v2",
            "--as",
            "user",
            "--parent-position",
            "my_library",
            "--doc-format",
            doc_format,
        ]
        title = str(request.get("title") or "").strip()
        if doc_format == "markdown" and title:
            create_args.extend(["--title", title])
        create_args.extend(["--content", f"@{source.name}", "--json"])
        created = self._call_lark(create_args, cwd=source.parent)
        document = created.get("data", {}).get("document", {})
        document_id = str(document.get("document_id") or "").strip()
        url = str(document.get("url") or "").strip()
        if not document_id or not url:
            raise RuntimeError("document create response is incomplete")

        permission_args = [
            str(self.lark_binary),
            "drive",
            "permission.members",
            "create",
            "--as",
            "user",
            "--params",
            json.dumps(
                {
                    "token": document_id,
                    "type": "docx",
                    "need_notification": False,
                },
                ensure_ascii=False,
                separators=(",", ":"),
            ),
            "--data",
            json.dumps(
                {
                    "member_type": "openchat",
                    "member_id": self.chat_id,
                    "perm": "view",
                    "type": "chat",
                },
                ensure_ascii=False,
                separators=(",", ":"),
            ),
            "--yes",
            "--json",
        ]
        self._call_lark(permission_args, cwd=self.team_root)
        return {
            "request_id": request_id,
            "status": "complete",
            "document_id": document_id,
            "url": url,
            "completed_at": self._now(),
        }

    def _call_lark(self, args: Sequence[str], cwd: Path) -> Dict[str, Any]:
        completed = self.command_runner(args, cwd, self._command_env())
        if completed.returncode != 0:
            raise RuntimeError("lark-cli command failed")
        payload = json.loads(completed.stdout)
        if not payload.get("ok"):
            raise RuntimeError("lark-cli API request failed")
        return payload

    def _command_env(self) -> Dict[str, str]:
        env = os.environ.copy()
        channel_home = Path(
            env.get("LARK_CHANNEL_HOME", str(Path.home() / ".lark-channel"))
        )
        profile = env.get("LARK_CHANNEL_PROFILE", "creative")
        env["LARK_CHANNEL"] = "1"
        env["LARK_CHANNEL_HOME"] = str(channel_home)
        env["LARK_CHANNEL_PROFILE"] = profile
        env["LARKSUITE_CLI_CONFIG_DIR"] = env.get(
            "LARKSUITE_CLI_CONFIG_DIR",
            str(channel_home / "profiles" / profile / "lark-cli"),
        )
        return env

    def _validate_request_id(self, request_id: str) -> str:
        value = request_id.strip()
        if not REQUEST_ID_PATTERN.fullmatch(value):
            raise ValueError("request_id format is invalid")
        return value

    def _validate_project_id(self, project_id: str) -> str:
        value = str(project_id).strip()
        if not PROJECT_ID_PATTERN.fullmatch(value):
            raise ValueError("project_id format is invalid")
        return value

    def _enqueue_payload(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        request_id = self._validate_request_id(str(payload["request_id"]))
        result_path = self._result_path(request_id)
        if result_path.exists():
            return json.loads(result_path.read_text(encoding="utf-8"))
        pending_path = self.queue_root / "pending" / f"{request_id}.json"
        processing_path = self.queue_root / "processing" / f"{request_id}.json"
        if not pending_path.exists() and not processing_path.exists():
            self._write_json_atomic(pending_path, payload)
        result = {"request_id": request_id, "status": "queued"}
        if payload.get("project_id"):
            result["project_id"] = payload["project_id"]
        return result

    def _validate_source(self, source_path: Path, doc_format: str) -> Path:
        format_value = doc_format.strip().lower()
        if format_value not in {"xml", "markdown"}:
            raise ValueError("doc_format must be xml or markdown")
        source = Path(source_path).expanduser()
        if not source.is_absolute():
            source = self.team_root / source
        source = source.resolve()
        try:
            source.relative_to(self.team_root)
        except ValueError as exc:
            raise ValueError("文档源文件必须位于飞书团队目录内") from exc
        expected_suffix = ".xml" if format_value == "xml" else ".md"
        if source.suffix.lower() != expected_suffix:
            raise ValueError(f"{format_value} source must use {expected_suffix}")
        if not source.is_file():
            raise ValueError("document source file does not exist")
        if source.stat().st_size > MAX_SOURCE_BYTES:
            raise ValueError("document source file is too large")
        return source

    def _result_path(self, request_id: str) -> Path:
        return self.queue_root / "results" / f"{request_id}.json"

    @staticmethod
    def _now() -> str:
        return datetime.now().astimezone().isoformat(timespec="seconds")

    @staticmethod
    def _write_json_atomic(path: Path, payload: Dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = path.with_suffix(path.suffix + ".tmp")
        temp_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        os.replace(str(temp_path), str(path))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Controlled Feishu document publisher")
    subparsers = parser.add_subparsers(dest="command", required=True)
    enqueue = subparsers.add_parser("enqueue")
    enqueue.add_argument("--request-id", required=True)
    enqueue.add_argument("--source", required=True)
    enqueue.add_argument("--doc-format", choices=["xml", "markdown"], required=True)
    enqueue.add_argument("--title")
    enqueue.add_argument("--wait", type=float, default=120)
    sync_project = subparsers.add_parser("sync-project")
    sync_project.add_argument("--project-id", required=True)
    sync_project.add_argument("--source", required=True)
    sync_project.add_argument("--doc-format", choices=["xml", "markdown"], required=True)
    sync_project.add_argument("--title", required=True)
    sync_project.add_argument("--wait", type=float, default=120)
    pull_project = subparsers.add_parser("pull-project")
    pull_project.add_argument("--project-id", required=True)
    pull_project.add_argument("--request-id")
    pull_project.add_argument("--wait", type=float, default=120)
    build_project_source = subparsers.add_parser("build-project-source")
    build_project_source.add_argument("--project-root", required=True)
    subparsers.add_parser("once")
    serve = subparsers.add_parser("serve")
    serve.add_argument("--poll-seconds", type=float, default=0.5)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    team_root = Path(os.environ.get("CREATIVE_COLLAB_ROOT", str(DEFAULT_TEAM_ROOT)))
    queue_value = os.environ.get("CREATIVE_COLLAB_FEISHU_DOC_QUEUE")
    publisher = FeishuDocPublisher(
        team_root=team_root,
        queue_root=Path(queue_value) if queue_value else None,
        chat_id=os.environ.get("CREATIVE_COLLAB_FEISHU_CHAT_ID", DEFAULT_CHAT_ID),
        lark_binary=Path(os.environ.get("LARK_CLI_BINARY", str(DEFAULT_LARK_BINARY))),
    )
    if args.command == "enqueue":
        result = publisher.enqueue(
            request_id=args.request_id,
            source_path=Path(args.source),
            doc_format=args.doc_format,
            title=args.title,
        )
        if result.get("status") == "queued" and args.wait > 0:
            result = publisher.wait_for_result(args.request_id, args.wait)
    elif args.command == "sync-project":
        result = publisher.sync_project(
            project_id=args.project_id,
            source_path=Path(args.source),
            doc_format=args.doc_format,
            title=args.title,
        )
        if result.get("status") == "queued" and args.wait > 0:
            result = publisher.wait_for_result(result["request_id"], args.wait)
    elif args.command == "pull-project":
        result = publisher.pull_project(
            project_id=args.project_id,
            request_id=args.request_id,
        )
        if result.get("status") == "queued" and args.wait > 0:
            result = publisher.wait_for_result(result["request_id"], args.wait)
    elif args.command == "build-project-source":
        result = publisher.build_project_source(Path(args.project_root))
    elif args.command == "once":
        result = publisher.process_next() or {"status": "idle"}
    else:
        publisher.serve(args.poll_seconds)
        return
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
