import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from creative_collab.cli import call_service_tool
from creative_collab.mcp_server import _tool_schemas
from creative_collab.service import CreativeCollabService, PermissionError, WorkflowError


class ReportRecoveryTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.s = CreativeCollabService(root / "registry.sqlite", root / "creative")
        self.s.bootstrap_v01("boot")
        self.s.agent_add_thread("owner", "编导", "study")
        self.s.agent_add_thread("other", "编导", "reott")
        self.s.agent_bind_thread("editor", "剪辑", "study-editor")
        self.p = self.s.project_create("project", "report recovery fixture", "编导", "test", [],
                                       owner_thread_id="study")
        self.t = self.s.task_assign("task", self.p["project_id"], "编导", "剪辑", "edit", [], ["MP4"])
        self.receive("task", self.t["task_id"], "编导", "剪辑")
        self.a = self.artifact("report", "剪辑反馈")
        self.h = self.s.handoff_submit("handoff", self.t["task_id"], "剪辑", "编导",
                                       "Control blocker only; no finished video", [self.a["artifact_id"]])
        self.receive("handoff", self.h["handoff_id"], "剪辑", "编导")
        self.args = dict(request_id="recover", task_id=self.t["task_id"],
                         report_handoff_id=self.h["handoff_id"], role="编导",
                         director_thread_id="study", reason="Confirmed control report, not final delivery",
                         next_step="Continue native edit and export complete MP4")

    def tearDown(self):
        self.tmp.cleanup()

    def receive(self, kind, entity_id, sender, recipient):
        d = next(x for x in self.s.dispatch_list(self.p["project_id"])
                 if x["entity_type"] == kind and x["entity_id"] == entity_id)
        token = self.s.dispatch_prepare("prepare-" + entity_id, d["dispatch_id"], sender)
        self.s.dispatch_mark_sent("send-" + entity_id, d["dispatch_id"], sender,
                                  token["prepare_token"], "fixture-only")
        self.s.dispatch_mark_received("receive-" + entity_id, d["dispatch_id"], recipient)

    def artifact(self, name, kind):
        rel = "04_剪辑/" + name + ".md"
        (Path(self.p["project_path"]) / rel).write_text(name, encoding="utf-8")
        return self.s.artifact_submit("artifact-" + name, self.p["project_id"], "剪辑", kind, rel, name)

    def sql(self, query, params=()):
        with sqlite3.connect(self.s.db_path) as c:
            c.row_factory = sqlite3.Row
            return [dict(row) for row in c.execute(query, params).fetchall()]

    def resume(self, **overrides):
        self.assertTrue(hasattr(self.s, "task_resume_from_report"),
                        "Missing audited recovery for a non-final control report")
        return self.s.task_resume_from_report(**dict(self.args, **overrides))

    def test_recovers_same_task_preserves_history_and_allows_final_handoff(self):
        old_handoffs = self.sql("select * from handoffs")
        old_dispatches = self.sql("select * from dispatches")
        out = self.resume()
        self.assertEqual("in_progress", out["task"]["status"])
        self.assertEqual("editing", out["project_status"])
        self.assertEqual(old_handoffs, self.sql("select * from handoffs"))
        self.assertEqual(old_dispatches, self.sql("select * from dispatches"))
        self.assertEqual(1, len(self.sql("select * from tasks")))
        self.assertEqual([], self.sql("select * from reviews"))
        audit = self.sql("select * from task_report_recoveries")[0]
        self.assertEqual("submitted", json.loads(audit["before_json"])["task"]["status"])
        self.assertEqual("study", audit["director_thread_id"])
        history = (Path(self.p["project_path"]) / "00_项目管理/交接与退回记录.md").read_text()
        self.assertIn(out["recovery_id"], history)
        final = self.artifact("delivery-fixture", "document")
        handoff = self.s.handoff_submit("actual-final", self.t["task_id"], "剪辑", "编导",
                                       "Delivery workflow fixture, not a real video", [final["artifact_id"]])
        self.assertNotEqual(self.h["handoff_id"], handoff["handoff_id"])
        self.assertEqual(2, len(self.sql("select * from handoffs")))

    def test_idempotent_but_cannot_reuse_report_to_reopen_final_delivery(self):
        first = self.resume()
        self.assertEqual(first, self.resume())
        with self.assertRaises(WorkflowError):
            self.resume(request_id="second")
        final = self.artifact("delivery", "document")
        self.s.handoff_submit("final", self.t["task_id"], "剪辑", "编导", "final", [final["artifact_id"]])
        with self.assertRaises(WorkflowError):
            self.resume(request_id="stale")
        self.assertEqual(1, len(self.sql("select * from task_report_recoveries")))

    def test_owner_role_and_active_registration_required(self):
        for changed in ({"director_thread_id": "reott"}, {"role": "剪辑"}):
            with self.subTest(changed=changed), self.assertRaises(PermissionError):
                self.resume(**changed)
        self.sql("update agent_thread_bindings set status='inactive' where thread_id='study'")
        with self.assertRaises(PermissionError):
            self.resume()

    def test_cli_requires_matching_current_director_not_supplied_impersonation(self):
        for current in ("", "reott", "study-editor"):
            with patch.dict("os.environ", {"CODEX_THREAD_ID": current}), self.assertRaises(PermissionError):
                call_service_tool(self.s, "task_resume_from_report", self.args)
        with patch.dict("os.environ", {"CODEX_THREAD_ID": "study"}):
            out = call_service_tool(self.s, "task_resume_from_report", self.args)
        self.assertEqual("in_progress", out["task"]["status"])

    def test_cannot_recover_unreceived_report_or_wrong_target(self):
        for status, target in (("sent", "study"), ("received", "reott")):
            self.sql("update dispatches set status=?,target_thread_id=? where entity_type='handoff'",
                     (status, target))
            with self.subTest(status=status, target=target), self.assertRaises(WorkflowError):
                self.resume()

    def test_rejects_non_report_artifact_and_changed_report_file(self):
        self.sql("update artifacts set artifact_type='document'")
        with self.assertRaises(WorkflowError):
            self.resume()
        self.sql("update artifacts set artifact_type='剪辑反馈'")
        (Path(self.p["project_path"]) / self.a["relative_path"]).write_text("changed", encoding="utf-8")
        with self.assertRaises(WorkflowError):
            self.resume()

    def test_rejects_formal_reviews_even_if_project_status_was_reset(self):
        self.sql("insert into reviews values ('REV-test', ?, '编导', 'revision_required', 'now')",
                 (self.p["project_id"],))
        with self.assertRaises(WorkflowError):
            self.resume()

    def test_rejects_completed_or_not_submitted_tasks_and_terminal_projects(self):
        for task_status, project_status in (("completed", "director_review"), ("blocked", "director_review"),
                                            ("submitted", "approved"), ("submitted", "completed"),
                                            ("submitted", "revision_required")):
            self.sql("update tasks set status=?", (task_status,))
            self.sql("update projects set status=?", (project_status,))
            with self.subTest(task_status=task_status, project_status=project_status), self.assertRaises(WorkflowError):
                self.resume()

    def test_no_blank_justification_or_next_step(self):
        for name in ("reason", "next_step"):
            with self.subTest(name=name), self.assertRaises(WorkflowError):
                self.resume(**{name: " "})

    def test_audit_is_append_only_and_sync_failure_rolls_back(self):
        with patch.object(self.s, "_sync_project_files", side_effect=OSError("fixture disk failure")):
            with self.assertRaises(OSError):
                self.resume()
        self.assertEqual("submitted", self.sql("select status from tasks")[0]["status"])
        self.assertEqual([], self.sql("select * from task_report_recoveries"))
        self.resume()
        for query in ("delete from task_report_recoveries", "update task_report_recoveries set reason='changed'"):
            with self.assertRaises(sqlite3.IntegrityError):
                self.sql(query)

    def test_does_not_relax_regular_task_update(self):
        with self.assertRaises(WorkflowError):
            self.s.task_update("not-recovery", self.t["task_id"], "剪辑", "in_progress", None, "next")

    def test_mcp_exposes_explicit_director_only_operation(self):
        schemas = {tool["name"]: tool["inputSchema"] for tool in _tool_schemas()}
        self.assertIn("task_resume_from_report", schemas)
        self.assertEqual(["编导"], schemas["task_resume_from_report"]["properties"]["role"]["enum"])


if __name__ == "__main__":
    unittest.main()
