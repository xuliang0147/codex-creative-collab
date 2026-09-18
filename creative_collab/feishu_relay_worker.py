from __future__ import annotations

import argparse
import fcntl
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Iterable

from .feishu_relay import extract_final_agent_message, format_group_reply


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Relay one team-director result to Feishu")
    parser.add_argument("--thread-id", required=True)
    parser.add_argument("--host-id", required=True)
    parser.add_argument("--dispatch-id", required=True)
    parser.add_argument("--message-file", required=True)
    parser.add_argument("--chat-id", required=True)
    parser.add_argument("--lark-profile", required=True)
    parser.add_argument("--lark-channel-home", required=True)
    parser.add_argument("--lark-cli-config-dir", required=True)
    parser.add_argument("--codex-binary", required=True)
    parser.add_argument("--lark-binary", required=True)
    parser.add_argument("--lock-file", required=True)
    parser.add_argument("--add-dir", action="append", default=[])
    return parser


def main() -> None:
    args = build_parser().parse_args()
    lock_path = Path(args.lock_file)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+", encoding="utf-8") as lock_handle:
        fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX)
        print(
            json.dumps(
                {
                    "type": "relay.started",
                    "dispatch_id": args.dispatch_id,
                    "thread_id": args.thread_id,
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
        try:
            result = run_relay(args)
        except Exception as exc:
            print(
                json.dumps(
                    {
                        "type": "relay.failed",
                        "dispatch_id": args.dispatch_id,
                        "error": str(exc),
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )
            raise
        print(
            json.dumps({"type": "relay.sent", **result}, ensure_ascii=False),
            flush=True,
        )


def run_relay(args: argparse.Namespace) -> dict[str, str]:
    message = Path(args.message_file).read_text(encoding="utf-8")
    command = [str(args.codex_binary), "exec"]
    for writable_root in args.add_dir:
        command.extend(["--add-dir", str(Path(writable_root).expanduser().resolve())])
    command.extend(
        [
            "resume",
            "--json",
            "--all",
            "--skip-git-repo-check",
            args.thread_id,
            message,
        ]
    )
    codex = subprocess.run(
        command,
        cwd=os.getcwd(),
        env=os.environ.copy(),
        text=True,
        capture_output=True,
        check=False,
    )
    lines = codex.stdout.splitlines()
    _emit_lines(lines)
    if codex.stderr:
        print(codex.stderr, file=sys.stderr, end="")
    if codex.returncode != 0:
        raise RuntimeError(f"team director resume failed with exit {codex.returncode}")

    final_message = extract_final_agent_message(lines)
    if not final_message:
        raise RuntimeError("team director did not produce a final group reply")

    group_reply = format_group_reply(final_message)
    env = os.environ.copy()
    env.update(
        {
            "LARK_CHANNEL": "1",
            "LARK_CHANNEL_HOME": args.lark_channel_home,
            "LARK_CHANNEL_PROFILE": args.lark_profile,
            "LARKSUITE_CLI_CONFIG_DIR": args.lark_cli_config_dir,
        }
    )
    lark = subprocess.run(
        [
            str(args.lark_binary),
            "im",
            "+messages-send",
            "--as",
            "bot",
            "--chat-id",
            args.chat_id,
            "--markdown",
            group_reply,
            "--json",
        ],
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    if lark.stdout:
        print(lark.stdout, end="")
    if lark.stderr:
        print(lark.stderr, file=sys.stderr, end="")
    if lark.returncode != 0:
        raise RuntimeError(f"Feishu group send failed with exit {lark.returncode}")
    return {
        "dispatch_id": args.dispatch_id,
        "thread_id": args.thread_id,
        "chat_id": args.chat_id,
    }


def _emit_lines(lines: Iterable[str]) -> None:
    for line in lines:
        print(line, flush=True)


if __name__ == "__main__":
    main()
