import sqlite3
import tempfile
import unittest
from pathlib import Path

from creative_collab.service import CreativeCollabService, WorkflowError
from creative_collab.mcp_server import _tool_schemas


class TeamRoutingTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.s = CreativeCollabService(root / "registry.sqlite", root / "creative")
        self.s.bootstrap_v01("boot")
        for owner in ("study", "reott"):
            self.s.agent_add_thread("director-" + owner, "编导", owner)
        self.s.agent_bind_thread("old-editor", "剪辑", "shared-editor")
        self.projects = {
            owner: self.s.project_create(
                "project-" + owner, owner, "编导", owner, [],
                owner_thread_id=owner,
            )["project_id"] for owner in ("study", "reott")
        }

    def tearDown(self):
        self.tmp.cleanup()

    def members(self, owner):
        return {r: {"thread_id": owner + "-" + r, "host_id": "local"}
                for r in ("剪辑", "拍摄", "平面", "即梦")}

    def register(self, owner):
        self.assertTrue(hasattr(self.s, "director_team_register"),
                        "Director-scoped execution routing is missing")
        return self.s.director_team_register(
            "team-" + owner, owner, owner, self.members(owner))

    def task(self, owner, role):
        t = self.s.task_assign("task-" + owner + role, self.projects[owner],
                               "编导", role, "test", [], ["test"])
        return next(d for d in self.s.dispatch_list(self.projects[owner])
                    if d["entity_id"] == t["task_id"])

    def test_routes_each_role_to_own_director_team(self):
        for owner in self.projects:
            self.register(owner)
            for role in self.members(owner):
                d = self.task(owner, role)
                p = self.s.dispatch_prepare("prepare-" + owner + role,
                                            d["dispatch_id"], "编导")
                self.assertEqual(owner + "-" + role, p["thread_id"])
            routes = self.s.project_route_get(self.projects[owner])
            self.assertEqual(owner, routes["owner_thread_id"])
            self.assertEqual(owner, routes["routes"]["编导"]["thread_id"])

    def test_no_global_fallback_for_unregistered_team(self):
        self.register("study")
        d = self.task("reott", "剪辑")
        self.assertIsNone(d["target_thread_id"])
        with self.assertRaises(WorkflowError):
            self.s.dispatch_prepare("bad", d["dispatch_id"], "编导")

    def test_duplicate_executor_across_teams_is_rejected_atomically(self):
        self.register("study")
        members = self.members("reott")
        members["平面"] = self.members("study")["平面"]
        with self.assertRaises(WorkflowError):
            self.s.director_team_register("bad-team", "reott", "reott", members)
        self.assertEqual(1, len(self.s.director_team_list()))

    def test_pending_preparation_invalidated_but_received_history_retained(self):
        d = self.task("study", "剪辑")
        p = self.s.dispatch_prepare("old-prepare", d["dispatch_id"], "编导")
        self.register("study")
        with self.assertRaises(WorkflowError):
            self.s.dispatch_mark_sent("old-send", d["dispatch_id"], "编导",
                                      p["prepare_token"], "not-sent")
        fresh = self.s.dispatch_prepare("new-prepare", d["dispatch_id"], "编导")
        self.assertEqual("study-剪辑", fresh["thread_id"])
        sent = self.s.dispatch_mark_sent("send", d["dispatch_id"], "编导",
                                        fresh["prepare_token"], "fixture-only")
        self.s.dispatch_mark_received("receipt", d["dispatch_id"], "剪辑")
        self.register("reott")
        self.assertEqual(sent["target_thread_id"],
                         self.s.dispatch_list(self.projects["study"])[0]["target_thread_id"])

    def test_legacy_global_rebind_is_rejected_at_database_boundary(self):
        self.register("study")
        with sqlite3.connect(self.s.db_path) as c:
            with self.assertRaises(sqlite3.IntegrityError):
                c.execute("insert into agent_bindings values ('剪辑','wrong','local','n','n')")

    def test_unknown_owner_not_silently_assigned_to_primary(self):
        self.register("study")
        with self.assertRaises(WorkflowError):
            self.s.project_create("no-owner", "ambiguous", "编导", "test", [])

    def test_idempotent_and_no_old_primary(self):
        a = self.register("study")
        self.assertEqual(a, self.register("study"))
        with sqlite3.connect(self.s.db_path) as c:
            self.assertEqual(0, c.execute("select count(*) from agent_bindings where role != '编导'").fetchone()[0])
        old = next(x for x in self.s.agent_thread_list("剪辑") if x["thread_id"] == "shared-editor")
        self.assertEqual("inactive", old["status"])

    def test_mcp_exposes_team_routes_and_dreamina(self):
        schemas = {t["name"]: t for t in _tool_schemas()}
        self.assertIn("director_team_register", schemas)
        self.assertIn("project_route_get", schemas)
        self.assertIn("即梦", schemas["task_assign"]["inputSchema"]["properties"]["to_role"]["enum"])

    def test_cached_prepare_cannot_replay_old_shared_recipient(self):
        d = self.task("study", "剪辑")
        old = self.s.dispatch_prepare("same-key", d["dispatch_id"], "编导")
        self.register("study")
        new = self.s.dispatch_prepare("same-key", d["dispatch_id"], "编导")
        self.assertNotEqual(old["thread_id"], new["thread_id"])
        self.assertEqual("study-剪辑", new["thread_id"])

    def test_old_received_dispatch_is_not_rewritten(self):
        d = self.task("study", "剪辑")
        p = self.s.dispatch_prepare("old-p", d["dispatch_id"], "编导")
        self.s.dispatch_mark_sent("old-s", d["dispatch_id"], "编导", p["prepare_token"], "fixture")
        old = self.s.dispatch_mark_received("old-r", d["dispatch_id"], "剪辑")
        self.register("study")
        self.assertEqual(old, self.s.dispatch_list(self.projects["study"])[0])

    def test_handoff_returns_to_project_owner(self):
        self.register("study")
        self.register("reott")
        for owner in self.projects:
            d = self.task(owner, "拍摄")
            p = self.s.dispatch_prepare("p-" + owner, d["dispatch_id"], "编导")
            self.s.dispatch_mark_sent("s-" + owner, d["dispatch_id"], "编导", p["prepare_token"], "fixture")
            self.s.dispatch_mark_received("r-" + owner, d["dispatch_id"], "拍摄")
            project = self.s.project_get(self.projects[owner])
            artifact_path = Path(project["project_path"]) / "02_拍摄/test.md"
            artifact_path.write_text("fixture only", encoding="utf-8")
            artifact = self.s.artifact_submit("a-" + owner, self.projects[owner], "拍摄",
                                              "document", "02_拍摄/test.md", "fixture")
            self.s.handoff_submit("h-" + owner, d["entity_id"], "拍摄", "编导", "test", [artifact["artifact_id"]])
            h = next(x for x in self.s.dispatch_list(self.projects[owner]) if x["entity_type"] == "handoff")
            self.assertEqual(owner, self.s.dispatch_prepare("hp-" + owner, h["dispatch_id"], "拍摄")["thread_id"])


if __name__ == "__main__":
    unittest.main()
