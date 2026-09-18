import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import test from "node:test";

import { CreativeDispatchWorker } from "../deploy/feishu_creative_team/creative-dispatch-worker.mjs";


test("worker submits a queued dispatch through the direct host runner", () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "creative-dispatch-worker-"));
  const teamRoot = path.join(root, "team");
  const queueRoot = path.join(teamRoot, "queue");
  const runner = path.join(root, "team-runner.py");
  fs.mkdirSync(path.join(queueRoot, "pending"), { recursive: true });
  fs.writeFileSync(runner, "# test runner\n", "utf8");
  fs.writeFileSync(
    path.join(queueRoot, "pending", "SEND-001.json"),
    JSON.stringify({
      request_id: "SEND-001",
      dispatch_id: "DISPATCH-010",
      role: "编导",
    }),
    "utf8",
  );
  const calls = [];
  const worker = new CreativeDispatchWorker({
    teamRoot,
    queueRoot,
    runner,
    runCommand(args, cwd) {
      calls.push({ args, cwd });
      return {
        dispatch: { dispatch_id: "DISPATCH-010", status: "sent" },
        delivery: { submission_id: "submission-real" },
        already_delivered: false,
      };
    },
  });

  const result = worker.processNext();

  assert.equal(result.status, "complete");
  assert.equal(result.dispatch_id, "DISPATCH-010");
  assert.equal(result.delivery.submission_id, "submission-real");
  assert.ok(calls[0].args.includes("--direct"));
  assert.ok(calls[0].args.includes("DISPATCH-010"));
  assert.deepEqual(
    JSON.parse(fs.readFileSync(path.join(queueRoot, "results", "SEND-001.json"), "utf8")),
    result,
  );
});


test("worker rejects malformed queue input without running a command", () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "creative-dispatch-worker-"));
  const teamRoot = path.join(root, "team");
  const queueRoot = path.join(teamRoot, "queue");
  const runner = path.join(root, "team-runner.py");
  fs.mkdirSync(path.join(queueRoot, "pending"), { recursive: true });
  fs.writeFileSync(runner, "# test runner\n", "utf8");
  fs.writeFileSync(
    path.join(queueRoot, "pending", "SEND-002.json"),
    JSON.stringify({
      request_id: "SEND-002",
      dispatch_id: "../../bad",
      role: "编导",
    }),
    "utf8",
  );
  let called = false;
  const worker = new CreativeDispatchWorker({
    teamRoot,
    queueRoot,
    runner,
    logger: { error() {} },
    runCommand() {
      called = true;
    },
  });

  const result = worker.processNext();

  assert.equal(result.status, "failed");
  assert.equal(result.message, "任务暂未成功交给执行角色，请稍后重试。");
  assert.equal(called, false);
});
