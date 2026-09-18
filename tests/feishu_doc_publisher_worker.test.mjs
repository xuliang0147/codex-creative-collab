import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import test from "node:test";

import { FeishuDocPublisherWorker } from "../deploy/feishu_creative_team/creative-feishu-doc-publisher-worker.mjs";


test("worker publishes a queued team document and grants group view", () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "creative-doc-worker-"));
  const teamRoot = path.join(root, "team");
  const queueRoot = path.join(teamRoot, "queue");
  const source = path.join(teamRoot, "01_编导", "需求确认.xml");
  fs.mkdirSync(path.dirname(source), { recursive: true });
  fs.mkdirSync(path.join(queueRoot, "pending"), { recursive: true });
  fs.writeFileSync(source, "<title>需求确认</title>", "utf8");
  fs.writeFileSync(
    path.join(queueRoot, "pending", "DOC-001.json"),
    JSON.stringify({
      request_id: "DOC-001",
      source_path: "01_编导/需求确认.xml",
      doc_format: "xml",
      title: "",
    }),
    "utf8",
  );
  const calls = [];
  const worker = new FeishuDocPublisherWorker({
    teamRoot,
    queueRoot,
    chatId: "oc_team",
    runCommand(args, cwd) {
      calls.push({ args, cwd });
      if (args.includes("docs")) {
        return {
          ok: true,
          data: {
            document: {
              document_id: "docx_test",
              url: "https://example.feishu.cn/docx/docx_test",
            },
          },
        };
      }
      return { ok: true, data: { member: { perm: "view" } } };
    },
  });

  const result = worker.processNext();

  assert.equal(result.status, "complete");
  assert.equal(result.document_id, "docx_test");
  assert.equal(calls.length, 2);
  assert.ok(calls[0].args.includes("@需求确认.xml"));
  assert.match(calls[1].args.join(" "), /oc_team/);
  assert.deepEqual(
    JSON.parse(
      fs.readFileSync(path.join(queueRoot, "results", "DOC-001.json"), "utf8"),
    ),
    result,
  );
});


test("worker rejects queued sources outside the team directory", () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "creative-doc-worker-"));
  const teamRoot = path.join(root, "team");
  const queueRoot = path.join(teamRoot, "queue");
  fs.mkdirSync(path.join(queueRoot, "pending"), { recursive: true });
  fs.writeFileSync(path.join(root, "outside.xml"), "<title>x</title>", "utf8");
  fs.writeFileSync(
    path.join(queueRoot, "pending", "DOC-002.json"),
    JSON.stringify({
      request_id: "DOC-002",
      source_path: "../outside.xml",
      doc_format: "xml",
    }),
    "utf8",
  );
  const worker = new FeishuDocPublisherWorker({
    teamRoot,
    queueRoot,
    chatId: "oc_team",
    logger: { error() {} },
    runCommand() {
      throw new Error("must not execute");
    },
  });

  const result = worker.processNext();

  assert.equal(result.status, "failed");
  assert.equal(result.message, "飞书云文档创建失败，已保留源文件，请稍后重试。");
});


test("worker creates one editable project document and stores its binding", () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "creative-doc-worker-"));
  const teamRoot = path.join(root, "team");
  const queueRoot = path.join(teamRoot, "queue");
  const projectManager = path.join(teamRoot, "03_视频剪辑项目", "测试项目", "00_项目管理");
  const source = path.join(projectManager, "项目协作文档.md");
  fs.mkdirSync(path.join(queueRoot, "pending"), { recursive: true });
  fs.mkdirSync(projectManager, { recursive: true });
  fs.writeFileSync(source, "# 测试项目\n", "utf8");
  fs.writeFileSync(
    path.join(queueRoot, "pending", "SYNC-001.json"),
    JSON.stringify({
      operation: "project_sync",
      request_id: "SYNC-001",
      project_id: "TOPIC-20260720-001",
      source_path: path.relative(teamRoot, source),
      doc_format: "markdown",
      title: "测试项目｜创意协作",
    }),
    "utf8",
  );
  const calls = [];
  const worker = new FeishuDocPublisherWorker({
    teamRoot,
    queueRoot,
    chatId: "oc_team",
    runCommand(args) {
      calls.push(args);
      if (args.includes("+create")) {
        return { ok: true, data: { document: { document_id: "docx_project", url: "https://example.feishu.cn/docx/docx_project" } } };
      }
      return { ok: true, data: {} };
    },
  });

  const result = worker.processNext();

  assert.equal(result.status, "complete");
  assert.equal(result.action, "created");
  assert.equal(result.url, "https://example.feishu.cn/docx/docx_project");
  const permissionCall = calls.find((args) => args.includes("permission.members"));
  assert.match(permissionCall.join(" "), /\"perm\":\"edit\"/);
  assert.ok(calls.some((args) => args.includes("+fetch")));
  const binding = JSON.parse(fs.readFileSync(path.join(queueRoot, "bindings", "TOPIC-20260720-001.json"), "utf8"));
  assert.equal(binding.document_id, "docx_project");
  assert.equal(JSON.parse(fs.readFileSync(path.join(projectManager, "飞书协作文档.json"), "utf8")).document_id, "docx_project");
});


test("worker updates the existing project document instead of creating another", () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "creative-doc-worker-"));
  const teamRoot = path.join(root, "team");
  const queueRoot = path.join(teamRoot, "queue");
  const source = path.join(teamRoot, "项目", "00_项目管理", "项目协作文档.md");
  fs.mkdirSync(path.dirname(source), { recursive: true });
  fs.mkdirSync(path.join(queueRoot, "pending"), { recursive: true });
  fs.mkdirSync(path.join(queueRoot, "bindings"), { recursive: true });
  fs.writeFileSync(source, "# 更新版\n", "utf8");
  fs.writeFileSync(path.join(queueRoot, "bindings", "TOPIC-20260720-002.json"), JSON.stringify({
    project_id: "TOPIC-20260720-002",
    document_id: "docx_existing",
    url: "https://example.feishu.cn/docx/docx_existing",
    project_manager_path: path.relative(teamRoot, path.dirname(source)),
  }));
  fs.writeFileSync(path.join(queueRoot, "pending", "SYNC-002.json"), JSON.stringify({
    operation: "project_sync",
    request_id: "SYNC-002",
    project_id: "TOPIC-20260720-002",
    source_path: path.relative(teamRoot, source),
    doc_format: "markdown",
    title: "更新版",
  }));
  const calls = [];
  const worker = new FeishuDocPublisherWorker({
    teamRoot,
    queueRoot,
    chatId: "oc_team",
    runCommand(args) {
      calls.push(args);
      return { ok: true, data: {} };
    },
  });

  const result = worker.processNext();

  assert.equal(result.action, "updated");
  assert.ok(calls.some((args) => args.includes("+update") && args.includes("overwrite")));
  assert.equal(calls.some((args) => args.includes("+create")), false);
});


test("worker pulls project content and unresolved comments for agents", () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "creative-doc-worker-"));
  const teamRoot = path.join(root, "team");
  const queueRoot = path.join(teamRoot, "queue");
  const projectManager = path.join(teamRoot, "项目", "00_项目管理");
  fs.mkdirSync(path.join(queueRoot, "pending"), { recursive: true });
  fs.mkdirSync(path.join(queueRoot, "bindings"), { recursive: true });
  fs.mkdirSync(projectManager, { recursive: true });
  fs.writeFileSync(path.join(queueRoot, "bindings", "TOPIC-20260720-003.json"), JSON.stringify({
    project_id: "TOPIC-20260720-003",
    document_id: "docx_pull",
    url: "https://example.feishu.cn/docx/docx_pull",
    project_manager_path: path.relative(teamRoot, projectManager),
  }));
  fs.writeFileSync(path.join(queueRoot, "pending", "PULL-003.json"), JSON.stringify({
    operation: "project_pull",
    request_id: "PULL-003",
    project_id: "TOPIC-20260720-003",
  }));
  const worker = new FeishuDocPublisherWorker({
    teamRoot,
    queueRoot,
    chatId: "oc_team",
    runCommand(args) {
      if (args.includes("file.comments")) {
        return { ok: true, data: { items: [{ comment_id: "comment-1" }] } };
      }
      return { ok: true, data: { document: { content: "<title>测试</title>" } } };
    },
  });

  const result = worker.processNext();

  assert.equal(result.comment_count, 1);
  assert.ok(fs.existsSync(path.join(projectManager, "飞书文档最新内容.json")));
  assert.ok(fs.existsSync(path.join(projectManager, "飞书未解决评论.json")));
});
