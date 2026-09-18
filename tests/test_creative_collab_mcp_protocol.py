from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from creative_collab.mcp_server import handle_message
from creative_collab.service import CreativeCollabService


class CreativeCollabMcpProtocolTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        self.service = CreativeCollabService(
            db_path=root / "registry.sqlite",
            creative_root=root / "creative",
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_initialized_notification_does_not_emit_a_response(self) -> None:
        response = handle_message(
            self.service,
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
        )

        self.assertIsNone(response)

    def test_ping_returns_empty_result(self) -> None:
        response = handle_message(
            self.service,
            {"jsonrpc": "2.0", "id": 7, "method": "ping"},
        )

        self.assertEqual(
            {"jsonrpc": "2.0", "id": 7, "result": {}},
            response,
        )

    def test_mcp_projects_require_user_confirmation_before_role_dispatch(self) -> None:
        self.service.bootstrap_v01(request_id="REQ-bootstrap")
        created = handle_message(
            self.service,
            {
                "jsonrpc": "2.0",
                "id": 8,
                "method": "tools/call",
                "params": {
                    "name": "project_create",
                    "arguments": {
                        "request_id": "REQ-mcp-project",
                        "title": "MCP需求确认门槛",
                        "owner_role": "编导",
                        "brief": "先确认再派发",
                        "tags": [],
                    },
                },
            },
        )
        project = json.loads(created["result"]["content"][0]["text"])
        self.assertTrue(project["requirements_required"])

        assigned = handle_message(
            self.service,
            {
                "jsonrpc": "2.0",
                "id": 9,
                "method": "tools/call",
                "params": {
                    "name": "task_assign",
                    "arguments": {
                        "request_id": "REQ-mcp-task-before-confirm",
                        "project_id": project["project_id"],
                        "from_role": "编导",
                        "to_role": "拍摄",
                        "summary": "不应提前派发",
                        "inputs": [],
                        "acceptance_criteria": [],
                    },
                },
            },
        )
        self.assertIn("需求尚未与任务发起人逐项确认", assigned["error"]["message"])

    def test_mcp_user_requests_are_director_only_and_publish_resolution_tools(self) -> None:
        listed = handle_message(
            self.service,
            {"jsonrpc": "2.0", "id": 10, "method": "tools/list", "params": {}},
        )
        tools = {tool["name"]: tool for tool in listed["result"]["tools"]}

        self.assertEqual(
            ["编导"],
            tools["user_input_request"]["inputSchema"]["properties"]["role"]["enum"],
        )
        self.assertEqual(
            ["编导"],
            tools["asset_request"]["inputSchema"]["properties"]["role"]["enum"],
        )
        self.assertIn("requirements_submit", tools)
        self.assertIn("requirements_confirm", tools)
        self.assertIn("asset_request_resolve", tools)


if __name__ == "__main__":
    unittest.main()
