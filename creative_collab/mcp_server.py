from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from .cli import call_service_tool
from .config import DEFAULT_CREATIVE_ROOT, DEFAULT_DB_PATH
from .service import CreativeCollabService


TOOL_NAMES = [
    "agent_register",
    "agent_bind_thread",
    "agent_add_thread",
    "agent_thread_list",
    "agent_list",
    "director_team_register",
    "director_team_list",
    "project_route_get",
    "project_create",
    "project_get",
    "project_list",
    "requirements_submit",
    "requirements_confirm",
    "requirements_get",
    "task_assign",
    "task_accept",
    "task_update",
    "task_resume_from_report",
    "handoff_submit",
    "dispatch_list",
    "dispatch_prepare",
    "dispatch_mark_sent",
    "dispatch_mark_received",
    "asset_scan",
    "asset_search",
    "asset_reference",
    "asset_request",
    "asset_request_resolve",
    "artifact_submit",
    "review_submit",
    "revision_return",
    "project_continue",
    "project_complete",
    "user_input_request",
    "user_input_resolve",
    "bootstrap_v01",
    "reconcile_v01",
]


def main() -> None:
    db_path = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_DB_PATH
    creative_root = Path(sys.argv[2]) if len(sys.argv) > 2 else DEFAULT_CREATIVE_ROOT
    service = CreativeCollabService(
        db_path=db_path,
        creative_root=creative_root,
        requester_label=os.environ.get("CREATIVE_COLLAB_REQUESTER_LABEL", "任务发起人"),
    )
    for line in sys.stdin:
        if not line.strip():
            continue
        response = handle_message(service, json.loads(line))
        if response is not None:
            sys.stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
            sys.stdout.flush()


def handle_message(
    service: CreativeCollabService, message: Dict[str, Any]
) -> Optional[Dict[str, Any]]:
    request_id = message.get("id")
    method = message.get("method")
    try:
        if method == "initialize":
            result: Any = {
                "protocolVersion": "2024-11-05",
                "serverInfo": {"name": "creative-collab-v01", "version": "0.1.0"},
                "capabilities": {"tools": {}},
            }
        elif method == "notifications/initialized":
            return None
        elif method == "ping":
            result = {}
        elif method == "tools/list":
            result = {"tools": _tool_schemas()}
        elif method == "tools/call":
            params = message.get("params", {})
            name = params["name"]
            arguments = dict(params.get("arguments", {}))
            if name == "project_create":
                arguments["requirements_required"] = True
                current_thread_id = os.environ.get("CODEX_THREAD_ID", "").strip()
                if current_thread_id:
                    active_directors = service.agent_thread_list(role="编导")
                    current_binding = next(
                        (
                            binding
                            for binding in active_directors
                            if binding["thread_id"] == current_thread_id
                            and binding["status"] == "active"
                        ),
                        None,
                    )
                    if current_binding:
                        arguments.setdefault("owner_thread_id", current_thread_id)
                        arguments.setdefault(
                            "owner_host_id", current_binding["host_id"]
                        )
            result = {
                "content": [
                    {
                        "type": "text",
                        "text": json.dumps(
                            call_service_tool(service, name, arguments),
                            ensure_ascii=False,
                            indent=2,
                        ),
                    }
                ],
                "isError": False,
            }
        else:
            raise ValueError(f"unsupported method: {method}")
        return {"jsonrpc": "2.0", "id": request_id, "result": result}
    except Exception as exc:
        if request_id is None:
            return None
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "error": {"code": -32000, "message": str(exc)},
        }


def _tool_schemas() -> List[Dict[str, Any]]:
    roles = ["编导", "拍摄", "平面", "剪辑", "即梦"]
    project_statuses = [
        "draft",
        "scripting",
        "asset_planning",
        "graphics_in_progress",
        "ready_for_edit",
        "editing",
        "director_review",
        "revision_required",
        "approved",
        "completed",
    ]
    string = {"type": "string"}
    nullable_string = {"type": ["string", "null"]}
    string_array = {"type": "array", "items": {"type": "string"}}

    def schema(
        properties: Dict[str, Any], required: List[str]
    ) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": properties,
            "required": required,
            "additionalProperties": False,
        }

    issue_schema = {
        "type": "object",
        "properties": {
            "responsible_role": {"type": "string", "enum": roles},
            "artifact_id": string,
            "issue_type": string,
            "requirement": string,
        },
        "required": ["responsible_role", "artifact_id", "requirement"],
        "additionalProperties": False,
    }
    schemas = {
        "director_team_register": schema(
            {"request_id": string, "owner_thread_id": string,
             "owner_host_id": {"type": "string", "default": "local"}, "label": string,
             "bindings": schema({r: schema({"thread_id": string, "host_id": string},
                                           ["thread_id", "host_id"])
                                 for r in roles if r != "编导"}, roles[1:])},
            ["request_id", "owner_thread_id", "label", "bindings"],
        ),
        "director_team_list": schema({}, []),
        "project_route_get": schema({"project_id": string}, ["project_id"]),
        "agent_register": schema(
            {
                "request_id": string,
                "role": {"type": "string", "enum": roles},
                "agent_id": string,
                "agent_type": {"type": "string", "const": "fixed"},
                "active_task_id": string,
                "capabilities": string_array,
                "write_scope": string,
            },
            [
                "request_id",
                "role",
                "agent_id",
                "agent_type",
                "active_task_id",
                "capabilities",
                "write_scope",
            ],
        ),
        "agent_bind_thread": schema(
            {
                "request_id": string,
                "role": {"type": "string", "enum": roles},
                "thread_id": string,
                "host_id": {"type": "string", "default": "local"},
            },
            ["request_id", "role", "thread_id"],
        ),
        "agent_add_thread": schema(
            {
                "request_id": string,
                "role": {"type": "string", "enum": roles},
                "thread_id": string,
                "host_id": {"type": "string", "default": "local"},
            },
            ["request_id", "role", "thread_id"],
        ),
        "agent_thread_list": schema(
            {
                "role": {
                    "type": ["string", "null"],
                    "enum": roles + [None],
                }
            },
            [],
        ),
        "agent_list": schema({}, []),
        "project_create": schema(
            {
                "request_id": string,
                "title": string,
                "owner_role": {"type": "string", "enum": ["编导"]},
                "brief": string,
                "tags": string_array,
                "requirements_required": {
                    "type": "boolean",
                    "const": True,
                    "default": True,
                },
                "owner_thread_id": nullable_string,
                "owner_host_id": nullable_string,
            },
            ["request_id", "title", "owner_role", "brief", "tags"],
        ),
        "project_get": schema({"project_id": string}, ["project_id"]),
        "project_list": schema(
            {"status": {"type": ["string", "null"], "enum": project_statuses + [None]}},
            [],
        ),
        "requirements_submit": schema(
            {
                "request_id": string,
                "project_id": string,
                "role": {"type": "string", "enum": ["编导"]},
                "goal": string,
                "target_audience": string,
                "platform": string,
                "duration_seconds": {"type": "integer", "minimum": 1},
                "deliverables": string_array,
                "available_assets": string_array,
                "creative_direction": string,
                "constraints": string_array,
                "open_questions": string_array,
            },
            [
                "request_id",
                "project_id",
                "role",
                "goal",
                "target_audience",
                "platform",
                "duration_seconds",
                "deliverables",
                "available_assets",
                "creative_direction",
                "constraints",
                "open_questions",
            ],
        ),
        "requirements_confirm": schema(
            {
                "request_id": string,
                "project_id": string,
                "role": {"type": "string", "enum": ["编导"]},
                "confirmation_note": string,
            },
            ["request_id", "project_id", "role", "confirmation_note"],
        ),
        "requirements_get": schema({"project_id": string}, ["project_id"]),
        "task_assign": schema(
            {
                "request_id": string,
                "project_id": string,
                "from_role": {"type": "string", "enum": roles},
                "to_role": {"type": "string", "enum": roles},
                "summary": string,
                "inputs": string_array,
                "acceptance_criteria": string_array,
            },
            [
                "request_id",
                "project_id",
                "from_role",
                "to_role",
                "summary",
                "inputs",
                "acceptance_criteria",
            ],
        ),
        "task_accept": schema(
            {
                "request_id": string,
                "task_id": string,
                "role": {"type": "string", "enum": roles},
            },
            ["request_id", "task_id", "role"],
        ),
        "task_update": schema(
            {
                "request_id": string,
                "task_id": string,
                "role": {"type": "string", "enum": roles},
                "status": {
                    "type": "string",
                    "enum": ["accepted", "in_progress", "blocked"],
                },
                "blocker": nullable_string,
                "next_step": nullable_string,
            },
            ["request_id", "task_id", "role", "status", "blocker", "next_step"],
        ),
        "task_resume_from_report": schema(
            {
                "request_id": string,
                "task_id": string,
                "report_handoff_id": string,
                "role": {"type": "string", "enum": ["编导"]},
                "director_thread_id": string,
                "reason": string,
                "next_step": string,
            },
            ["request_id", "task_id", "report_handoff_id", "role", "director_thread_id", "reason", "next_step"],
        ),
        "handoff_submit": schema(
            {
                "request_id": string,
                "task_id": string,
                "from_role": {"type": "string", "enum": roles},
                "to_role": {"type": "string", "enum": roles},
                "summary": string,
                "artifacts": string_array,
            },
            ["request_id", "task_id", "from_role", "to_role", "summary", "artifacts"],
        ),
        "dispatch_list": schema(
            {
                "project_id": nullable_string,
                "status": {
                    "type": ["string", "null"],
                    "enum": ["pending", "sent", "received", "closed", None],
                },
            },
            [],
        ),
        "dispatch_prepare": schema(
            {
                "request_id": string,
                "dispatch_id": string,
                "role": {"type": "string", "enum": roles},
            },
            ["request_id", "dispatch_id", "role"],
        ),
        "dispatch_mark_sent": schema(
            {
                "request_id": string,
                "dispatch_id": string,
                "from_role": {"type": "string", "enum": roles},
                "prepare_token": string,
                "submission_id": string,
            },
            ["request_id", "dispatch_id", "from_role", "prepare_token", "submission_id"],
        ),
        "dispatch_mark_received": schema(
            {
                "request_id": string,
                "dispatch_id": string,
                "role": {"type": "string", "enum": roles},
            },
            ["request_id", "dispatch_id", "role"],
        ),
        "asset_scan": schema(
            {"request_id": string, "file_path": string, "user_title": string},
            ["request_id", "file_path", "user_title"],
        ),
        "asset_search": schema(
            {
                "brand": nullable_string,
                "product": nullable_string,
                "content": nullable_string,
                "usage": nullable_string,
                "technical": nullable_string,
            },
            [],
        ),
        "asset_reference": schema(
            {
                "request_id": string,
                "project_id": string,
                "asset_id": string,
                "role": {"type": "string", "enum": roles},
                "usage_note": string,
                "output_path": nullable_string,
            },
            ["request_id", "project_id", "asset_id", "role", "usage_note"],
        ),
        "asset_request": schema(
            {
                "request_id": string,
                "project_id": string,
                "role": {"type": "string", "enum": ["编导"]},
                "description": string,
                "target_role": {"type": "string", "enum": roles, "default": "拍摄"},
            },
            ["request_id", "project_id", "role", "description"],
        ),
        "asset_request_resolve": schema(
            {
                "request_id": string,
                "asset_request_id": string,
                "role": {"type": "string", "enum": ["编导"]},
                "resolution": string,
            },
            ["request_id", "asset_request_id", "role", "resolution"],
        ),
        "artifact_submit": schema(
            {
                "request_id": string,
                "project_id": string,
                "role": {"type": "string", "enum": roles},
                "artifact_type": string,
                "relative_path": string,
                "description": string,
                "supersedes_artifact_id": nullable_string,
            },
            ["request_id", "project_id", "role", "artifact_type", "relative_path", "description"],
        ),
        "review_submit": schema(
            {
                "request_id": string,
                "project_id": string,
                "reviewer_role": {"type": "string", "enum": ["编导"]},
                "result": {
                    "type": "string",
                    "enum": ["approved", "revision_required"],
                },
                "issues": {"type": "array", "items": issue_schema},
            },
            ["request_id", "project_id", "reviewer_role", "result", "issues"],
        ),
        "revision_return": schema(
            {
                "request_id": string,
                "review_id": string,
                "issue_id": string,
                "from_role": {"type": "string", "enum": ["编导"]},
                "to_role": {"type": "string", "enum": roles},
            },
            ["request_id", "review_id", "issue_id", "from_role", "to_role"],
        ),
        "project_continue": schema(
            {
                "request_id": string,
                "project_id": string,
                "role": {"type": "string", "enum": ["编导"]},
                "continuation_note": string,
            },
            ["request_id", "project_id", "role", "continuation_note"],
        ),
        "project_complete": schema(
            {
                "request_id": string,
                "project_id": string,
                "role": {"type": "string", "enum": ["编导"]},
            },
            ["request_id", "project_id", "role"],
        ),
        "user_input_request": schema(
            {
                "request_id": string,
                "project_id": string,
                "role": {"type": "string", "enum": ["编导"]},
                "prompt": string,
            },
            ["request_id", "project_id", "role", "prompt"],
        ),
        "user_input_resolve": schema(
            {"request_id": string, "user_input_id": string, "response": string},
            ["request_id", "user_input_id", "response"],
        ),
        "bootstrap_v01": schema({"request_id": string}, ["request_id"]),
        "reconcile_v01": schema({"request_id": string}, ["request_id"]),
    }
    return [
        {
            "name": name,
            "description": f"Creative collaboration V0.1 tool: {name}",
            "inputSchema": schemas[name],
        }
        for name in TOOL_NAMES
    ]


if __name__ == "__main__":
    main()
