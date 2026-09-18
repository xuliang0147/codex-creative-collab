from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from creative_collab.dispatch_queue import CreativeDispatchQueue


class CreativeDispatchQueueTests(unittest.TestCase):
    def test_launch_agent_executes_node_worker_directly(self) -> None:
        plist = (
            Path(__file__).resolve().parents[1]
            / "deploy"
            / "feishu_creative_team"
            / "com.example.creative-dispatch-worker.example.plist"
        ).read_text(encoding="utf-8")

        self.assertIn("creative-dispatch-worker.mjs", plist)
        self.assertIn("com.example.creative-dispatch-worker", plist)
        self.assertIn("CREATIVE_COLLAB_DISPATCH_QUEUE", plist)

    def test_enqueue_writes_one_validated_dispatch_request(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            queue = CreativeDispatchQueue(queue_root=root / "queue")

            result = queue.enqueue(
                request_id="SEND-TASK-001",
                dispatch_id="DISPATCH-010",
                role="编导",
            )

            self.assertEqual("queued", result["status"])
            payload = json.loads(
                (root / "queue" / "pending" / "SEND-TASK-001.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual("DISPATCH-010", payload["dispatch_id"])
            self.assertEqual("编导", payload["role"])

    def test_duplicate_request_returns_existing_result(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            queue = CreativeDispatchQueue(queue_root=root / "queue")
            result_path = root / "queue" / "results" / "SEND-TASK-002.json"
            result_path.parent.mkdir(parents=True)
            expected = {
                "request_id": "SEND-TASK-002",
                "dispatch_id": "DISPATCH-011",
                "status": "complete",
            }
            result_path.write_text(json.dumps(expected), encoding="utf-8")

            result = queue.enqueue(
                request_id="SEND-TASK-002",
                dispatch_id="DISPATCH-011",
                role="编导",
            )

            self.assertEqual(expected, result)
            self.assertFalse(
                (root / "queue" / "pending" / "SEND-TASK-002.json").exists()
            )

    def test_rejects_invalid_dispatch_and_role(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            queue = CreativeDispatchQueue(queue_root=Path(temp_dir) / "queue")

            with self.assertRaisesRegex(ValueError, "dispatch_id"):
                queue.enqueue("SEND-003", "../escape", "编导")
            with self.assertRaisesRegex(ValueError, "role"):
                queue.enqueue("SEND-004", "DISPATCH-012", "管理员")


if __name__ == "__main__":
    unittest.main()
