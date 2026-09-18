from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any, Dict, Optional

from .config import DEFAULT_CREATIVE_ROOT, DEFAULT_DB_PATH
from .dispatch_queue import CreativeDispatchQueue
from .feishu_relay import (
    FeishuDirectorRelayBridge,
    FeishuRelayConfig,
    RoutedThreadBridge,
    ThreadBridge,
)
from .service import CreativeCollabService, PermissionError
from .simulation import run_mock_topic_flow
from .thread_bridge import DEFAULT_CODEX_BINARY, DEFAULT_LOG_DIR, CodexThreadBridge


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Creative collaboration V0.1")
    parser.add_argument(
        "--db-path",
        default=os.environ.get("CREATIVE_COLLAB_DB_PATH", str(DEFAULT_DB_PATH)),
    )
    parser.add_argument(
        "--creative-root",
        default=os.environ.get("CREATIVE_COLLAB_ROOT", str(DEFAULT_CREATIVE_ROOT)),
    )
    parser.add_argument(
        "--requester-label",
        default=os.environ.get("CREATIVE_COLLAB_REQUESTER_LABEL", "任务发起人"),
    )
    parser.add_argument(
        "--relay-config",
        default=os.environ.get("CREATIVE_COLLAB_FEISHU_RELAY_CONFIG"),
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("bootstrap", help="register four fixed roles and create ledgers")
    subparsers.add_parser("agent-list", help="list registered agents")

    register_current = subparsers.add_parser(
        "register-current",
        help="bind the current Codex thread to one fixed creative role",
    )
    register_current.add_argument(
        "--role",
        required=True,
        choices=["编导", "拍摄", "平面", "剪辑", "即梦"],
    )
    register_current.add_argument("--host-id", default="local")
    register_current.add_argument(
        "--replace",
        action="store_true",
        help="replace the existing primary role thread instead of adding another",
    )

    project_list = subparsers.add_parser("project-list", help="list projects")
    project_list.add_argument("--status")

    asset_search = subparsers.add_parser("asset-search", help="search indexed assets")
    asset_search.add_argument("--brand")
    asset_search.add_argument("--product")
    asset_search.add_argument("--content")
    asset_search.add_argument("--usage")
    asset_search.add_argument("--technical")

    call_tool = subparsers.add_parser("call-tool", help="call a service tool with JSON arguments")
    call_tool.add_argument("tool")
    call_tool.add_argument("arguments_json")

    send_dispatch = subparsers.add_parser(
        "send-dispatch", help="prepare and submit one dispatch to its bound Codex thread"
    )
    send_dispatch.add_argument("--request-id", required=True)
    send_dispatch.add_argument("--dispatch-id", required=True)
    send_dispatch.add_argument("--role", required=True)
    send_dispatch.add_argument("--codex-binary")
    send_dispatch.add_argument("--startup-timeout", type=float, default=15)
    send_dispatch.add_argument(
        "--direct",
        action="store_true",
        help="submit from the trusted host worker instead of the controlled queue",
    )
    send_dispatch.add_argument("--queue-timeout", type=float, default=120)

    simulate = subparsers.add_parser("simulate", help="run the V0.1 mock topic flow")
    simulate.add_argument("--source-asset")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    service = CreativeCollabService(
        db_path=Path(args.db_path),
        creative_root=Path(args.creative_root),
        requester_label=args.requester_label,
    )
    if args.command == "bootstrap":
        result = service.bootstrap_v01(request_id="CLI-BOOTSTRAP-V01")
    elif args.command == "agent-list":
        result = service.agent_list()
    elif args.command == "register-current":
        result = register_current_thread(
            service,
            role=args.role,
            host_id=args.host_id,
            replace_existing=args.replace,
        )
    elif args.command == "project-list":
        result = service.project_list(status=args.status)
    elif args.command == "asset-search":
        result = service.asset_search(
            brand=args.brand,
            product=args.product,
            content=args.content,
            usage=args.usage,
            technical=args.technical,
        )
    elif args.command == "call-tool":
        payload = json.loads(args.arguments_json)
        result = call_service_tool(service, args.tool, payload)
    elif args.command == "send-dispatch":
        queue_root = os.environ.get("CREATIVE_COLLAB_DISPATCH_QUEUE", "").strip()
        if queue_root and not args.direct:
            queue = CreativeDispatchQueue(Path(queue_root))
            result = queue.enqueue(
                request_id=args.request_id,
                dispatch_id=args.dispatch_id,
                role=args.role,
            )
            if result.get("status") == "queued" and args.queue_timeout > 0:
                result = queue.wait_for_result(args.request_id, args.queue_timeout)
        else:
            bridge = build_delivery_bridge(
                service=service,
                relay_config_path=Path(args.relay_config) if args.relay_config else None,
                codex_binary=Path(args.codex_binary) if args.codex_binary else None,
                startup_timeout_seconds=args.startup_timeout,
            )
            result = deliver_dispatch(
                service,
                request_id=args.request_id,
                dispatch_id=args.dispatch_id,
                role=args.role,
                bridge=bridge,
            )
    elif args.command == "simulate":
        source = Path(args.source_asset) if args.source_asset else None
        result = run_mock_topic_flow(service, source_asset_path=source)
    else:
        raise SystemExit(f"unknown command: {args.command}")
    print(json.dumps(result, ensure_ascii=False, indent=2))


def register_current_thread(
    service: CreativeCollabService,
    role: str,
    host_id: str = "local",
    replace_existing: bool = False,
) -> Dict[str, Any]:
    thread_id = os.environ.get("CODEX_THREAD_ID", "").strip()
    if not thread_id:
        raise RuntimeError(
            "无法识别当前 Codex 任务，请在要注册的创意部任务中执行"
        )
    if replace_existing:
        result = service.agent_bind_thread(
            request_id=f"REPLACE-CURRENT-{role}-{thread_id}-{host_id}",
            role=role,
            thread_id=thread_id,
            host_id=host_id,
        )
    else:
        result = service.agent_add_thread(
            request_id=f"ADD-CURRENT-{role}-{thread_id}-{host_id}",
            role=role,
            thread_id=thread_id,
            host_id=host_id,
        )
    return {
        "status": "registered",
        "role": result["role"],
        "thread_id": result["thread_id"],
        "host_id": result["host_id"],
        "replaced_existing": replace_existing,
        "message": (
            f"当前任务已注册为{role}，并已替代旧的主{role}任务。"
            if replace_existing
            else f"当前任务已新增为{role}，原有{role}任务继续保留。"
        ),
    }


def call_service_tool(
    service: CreativeCollabService, tool: str, arguments: Dict[str, Any]
) -> Any:
    if tool in {"_connect", "_init_db"} or tool.startswith("_"):
        raise ValueError("private tool is not callable")
    if tool == "task_resume_from_report":
        current_thread_id = os.environ.get("CODEX_THREAD_ID", "").strip()
        if not current_thread_id or arguments.get("director_thread_id") != current_thread_id:
            raise PermissionError("report recovery must run in the owning director's current thread")
    method = getattr(service, tool, None)
    if method is None:
        raise ValueError(f"unknown tool: {tool}")
    return method(**_coerce_path_arguments(arguments))


def deliver_dispatch(
    service: CreativeCollabService,
    request_id: str,
    dispatch_id: str,
    role: str,
    bridge: Optional[ThreadBridge] = None,
) -> Dict[str, Any]:
    matches = [
        item for item in service.dispatch_list() if item["dispatch_id"] == dispatch_id
    ]
    if not matches:
        raise ValueError(f"dispatch not found: {dispatch_id}")
    current = matches[0]
    if current["status"] in {"sent", "received"}:
        return {
            "dispatch": current,
            "delivery": {
                "submission_id": current["submission_id"],
                "thread_id": current["target_thread_id"],
                "host_id": current["target_host_id"],
                "dispatch_id": dispatch_id,
            },
            "already_delivered": True,
        }

    prepared = service.dispatch_prepare(
        request_id=f"{request_id}-PREPARE",
        dispatch_id=dispatch_id,
        role=role,
    )
    delivery = (
        bridge or CodexThreadBridge(working_directory=service.creative_root)
    ).send(
        thread_id=prepared["thread_id"],
        host_id=prepared["host_id"],
        message=prepared["message"],
        dispatch_id=dispatch_id,
    )
    sent = service.dispatch_mark_sent(
        request_id=f"{request_id}-MARK-SENT",
        dispatch_id=dispatch_id,
        from_role=role,
        prepare_token=prepared["prepare_token"],
        submission_id=delivery["submission_id"],
    )
    return {
        "dispatch": sent,
        "delivery": delivery,
        "already_delivered": False,
    }


def build_delivery_bridge(
    service: CreativeCollabService,
    relay_config_path: Optional[Path] = None,
    codex_binary: Optional[Path] = None,
    startup_timeout_seconds: float = 15,
) -> ThreadBridge:
    binary = Path(codex_binary) if codex_binary else DEFAULT_CODEX_BINARY
    operations_root = service.creative_root / "00_协作账本" / "运行记录"
    default_bridge = CodexThreadBridge(
        codex_binary=binary,
        log_dir=(
            operations_root / "thread-bridge"
            if relay_config_path is not None
            else DEFAULT_LOG_DIR
        ),
        startup_timeout_seconds=startup_timeout_seconds,
        working_directory=service.creative_root,
        writable_roots=[service.db_path.parent],
    )
    if relay_config_path is None:
        return default_bridge

    config = FeishuRelayConfig.load(Path(relay_config_path))
    director_bridge = FeishuDirectorRelayBridge(
        config=config,
        codex_binary=binary,
        spool_root=operations_root / "relay",
        startup_timeout_seconds=startup_timeout_seconds,
        working_directory=service.creative_root,
        writable_roots=[service.db_path.parent],
    )
    return RoutedThreadBridge(
        default_bridge=default_bridge,
        director_bridge=director_bridge,
        director_thread_id=config.director_thread_id,
        director_host_id=config.director_host_id,
    )


def _coerce_path_arguments(arguments: Dict[str, Any]) -> Dict[str, Any]:
    result = dict(arguments)
    for key in ("file_path",):
        if key in result and result[key] is not None:
            result[key] = Path(result[key])
    return result


if __name__ == "__main__":
    main()
