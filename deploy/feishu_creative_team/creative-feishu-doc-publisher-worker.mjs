import { spawnSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import { pathToFileURL } from "node:url";

const REQUEST_ID_PATTERN = /^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$/;
const PROJECT_ID_PATTERN = /^TOPIC-[A-Za-z0-9-]{1,64}$/;
const MAX_SOURCE_BYTES = 2 * 1024 * 1024;
const FAILURE_MESSAGE = "飞书云文档创建失败，已保留源文件，请稍后重试。";

function now() {
  return new Date().toISOString();
}

function writeJsonAtomic(target, payload) {
  fs.mkdirSync(path.dirname(target), { recursive: true });
  const temporary = `${target}.tmp`;
  fs.writeFileSync(temporary, `${JSON.stringify(payload, null, 2)}\n`, "utf8");
  fs.renameSync(temporary, target);
}

function defaultRunCommand(args, cwd) {
  const completed = spawnSync(args[0], args.slice(1), {
    cwd,
    encoding: "utf8",
    env: process.env,
    timeout: 180_000,
  });
  if (completed.error || completed.status !== 0) {
    throw completed.error ?? new Error("lark-cli command failed");
  }
  const payload = JSON.parse(completed.stdout);
  if (!payload.ok) {
    throw new Error("lark-cli API request failed");
  }
  return payload;
}

export class FeishuDocPublisherWorker {
  constructor({
    teamRoot,
    queueRoot,
    chatId,
    larkBinary = "/usr/local/bin/lark-cli",
    runCommand = defaultRunCommand,
    logger = console,
  }) {
    this.teamRoot = fs.realpathSync(path.resolve(teamRoot));
    this.queueRoot = path.resolve(queueRoot);
    this.chatId = String(chatId ?? "").trim();
    this.larkBinary = larkBinary;
    this.runCommand = runCommand;
    this.logger = logger;
    if (!this.chatId) {
      throw new Error("chatId cannot be blank");
    }
  }

  processNext() {
    const pendingDirectory = path.join(this.queueRoot, "pending");
    fs.mkdirSync(pendingDirectory, { recursive: true });
    const requestName = fs
      .readdirSync(pendingDirectory)
      .filter((name) => name.endsWith(".json"))
      .sort()[0];
    if (!requestName) {
      return null;
    }

    const pendingPath = path.join(pendingDirectory, requestName);
    const processingPath = path.join(this.queueRoot, "processing", requestName);
    fs.mkdirSync(path.dirname(processingPath), { recursive: true });
    try {
      fs.renameSync(pendingPath, processingPath);
    } catch (error) {
      if (error.code === "ENOENT") {
        return null;
      }
      throw error;
    }

    let request = {};
    let result;
    try {
      request = JSON.parse(fs.readFileSync(processingPath, "utf8"));
      result = this.processRequest(request);
    } catch (error) {
      this.logger.error(error instanceof Error ? error.stack : String(error));
      result = {
        request_id: String(request.request_id || path.parse(requestName).name),
        status: "failed",
        message: FAILURE_MESSAGE,
        completed_at: now(),
      };
    }

    writeJsonAtomic(
      path.join(this.queueRoot, "results", `${result.request_id}.json`),
      result,
    );
    const processedPath = path.join(this.queueRoot, "processed", requestName);
    fs.mkdirSync(path.dirname(processedPath), { recursive: true });
    fs.renameSync(processingPath, processedPath);
    return result;
  }

  processRequest(request) {
    if (request.operation === "project_sync") {
      return this.syncProject(request);
    }
    if (request.operation === "project_pull") {
      return this.pullProject(request);
    }
    return this.publish(request);
  }

  publish(request) {
    const requestId = this.validateRequestId(request.request_id);
    const documentFormat = String(request.doc_format ?? "").trim().toLowerCase();
    const source = this.validateSource(request.source_path, documentFormat);
    const createArguments = [
      this.larkBinary,
      "docs",
      "+create",
      "--api-version",
      "v2",
      "--as",
      "user",
      "--parent-position",
      "my_library",
      "--doc-format",
      documentFormat,
    ];
    const title = String(request.title ?? "").trim();
    if (documentFormat === "markdown" && title) {
      createArguments.push("--title", title);
    }
    createArguments.push("--content", `@${path.basename(source)}`, "--json");

    const created = this.runCommand(createArguments, path.dirname(source));
    const document = created?.data?.document ?? {};
    const documentId = String(document.document_id ?? "").trim();
    const url = String(document.url ?? "").trim();
    if (!documentId || !url) {
      throw new Error("document create response is incomplete");
    }

    this.runCommand(
      [
        this.larkBinary,
        "drive",
        "permission.members",
        "create",
        "--as",
        "user",
        "--params",
        JSON.stringify({
          token: documentId,
          type: "docx",
          need_notification: false,
        }),
        "--data",
        JSON.stringify({
          member_type: "openchat",
          member_id: this.chatId,
          perm: "view",
          type: "chat",
        }),
        "--yes",
        "--json",
      ],
      this.teamRoot,
    );

    return {
      request_id: requestId,
      status: "complete",
      document_id: documentId,
      url,
      completed_at: now(),
    };
  }

  syncProject(request) {
    const requestId = this.validateRequestId(request.request_id);
    const projectId = this.validateProjectId(request.project_id);
    const documentFormat = String(request.doc_format ?? "").trim().toLowerCase();
    const source = this.validateSource(request.source_path, documentFormat);
    const title = String(request.title ?? "").trim();
    if (!title) {
      throw new Error("project document title cannot be blank");
    }

    const bindingPath = this.bindingPath(projectId);
    const existing = fs.existsSync(bindingPath)
      ? JSON.parse(fs.readFileSync(bindingPath, "utf8"))
      : null;
    let documentId;
    let url;
    let action;
    if (existing) {
      documentId = String(existing.document_id ?? "").trim();
      url = String(existing.url ?? "").trim();
      if (!documentId || !url) {
        throw new Error("project document binding is incomplete");
      }
      this.runCommand([
        this.larkBinary,
        "docs",
        "+update",
        "--api-version",
        "v2",
        "--as",
        "user",
        "--doc",
        documentId,
        "--command",
        "overwrite",
        "--doc-format",
        documentFormat,
        "--content",
        `@${path.basename(source)}`,
        "--json",
      ], path.dirname(source));
      action = "updated";
    } else {
      const createArguments = [
        this.larkBinary,
        "docs",
        "+create",
        "--api-version",
        "v2",
        "--as",
        "user",
        "--parent-position",
        "my_library",
        "--doc-format",
        documentFormat,
      ];
      if (documentFormat === "markdown") {
        createArguments.push("--title", title);
      }
      createArguments.push("--content", `@${path.basename(source)}`, "--json");
      const created = this.runCommand(createArguments, path.dirname(source));
      const document = created?.data?.document ?? {};
      documentId = String(document.document_id ?? "").trim();
      url = String(document.url ?? "").trim();
      if (!documentId || !url) {
        throw new Error("project document create response is incomplete");
      }
      this.grantGroupPermission(documentId, "edit");
      action = "created";
    }

    this.runCommand([
      this.larkBinary,
      "docs",
      "+fetch",
      "--api-version",
      "v2",
      "--as",
      "user",
      "--doc",
      documentId,
      "--detail",
      "with-ids",
      "--json",
    ], this.teamRoot);

    const projectManager = path.dirname(source);
    const binding = {
      project_id: projectId,
      document_id: documentId,
      url,
      title,
      project_manager_path: path.relative(this.teamRoot, projectManager),
      updated_at: now(),
    };
    writeJsonAtomic(bindingPath, binding);
    writeJsonAtomic(path.join(projectManager, "飞书协作文档.json"), binding);
    return {
      request_id: requestId,
      project_id: projectId,
      status: "complete",
      action,
      document_id: documentId,
      url,
      completed_at: now(),
    };
  }

  pullProject(request) {
    const requestId = this.validateRequestId(request.request_id);
    const projectId = this.validateProjectId(request.project_id);
    const bindingPath = this.bindingPath(projectId);
    if (!fs.existsSync(bindingPath)) {
      throw new Error("project document binding does not exist");
    }
    const binding = JSON.parse(fs.readFileSync(bindingPath, "utf8"));
    const documentId = String(binding.document_id ?? "").trim();
    const projectManager = this.validateProjectManager(binding.project_manager_path);
    if (!documentId) {
      throw new Error("project document binding is incomplete");
    }

    const document = this.runCommand([
      this.larkBinary,
      "docs",
      "+fetch",
      "--api-version",
      "v2",
      "--as",
      "user",
      "--doc",
      documentId,
      "--detail",
      "with-ids",
      "--json",
    ], this.teamRoot);
    const comments = this.runCommand([
      this.larkBinary,
      "drive",
      "file.comments",
      "list",
      "--as",
      "user",
      "--params",
      JSON.stringify({
        file_token: documentId,
        file_type: "docx",
        is_solved: false,
        page_size: 50,
      }),
      "--json",
    ], this.teamRoot);
    writeJsonAtomic(path.join(projectManager, "飞书文档最新内容.json"), document);
    writeJsonAtomic(path.join(projectManager, "飞书未解决评论.json"), comments);
    const items = Array.isArray(comments?.data?.items) ? comments.data.items : [];
    return {
      request_id: requestId,
      project_id: projectId,
      status: "complete",
      document_id: documentId,
      url: String(binding.url ?? ""),
      comment_count: items.length,
      completed_at: now(),
    };
  }

  grantGroupPermission(documentId, permission) {
    return this.runCommand([
      this.larkBinary,
      "drive",
      "permission.members",
      "create",
      "--as",
      "user",
      "--params",
      JSON.stringify({ token: documentId, type: "docx", need_notification: false }),
      "--data",
      JSON.stringify({
        member_type: "openchat",
        member_id: this.chatId,
        perm: permission,
        type: "chat",
      }),
      "--yes",
      "--json",
    ], this.teamRoot);
  }

  validateRequestId(value) {
    const requestId = String(value ?? "").trim();
    if (!REQUEST_ID_PATTERN.test(requestId)) {
      throw new Error("request_id format is invalid");
    }
    return requestId;
  }

  validateProjectId(value) {
    const projectId = String(value ?? "").trim();
    if (!PROJECT_ID_PATTERN.test(projectId)) {
      throw new Error("project_id format is invalid");
    }
    return projectId;
  }

  bindingPath(projectId) {
    return path.join(this.queueRoot, "bindings", `${projectId}.json`);
  }

  validateProjectManager(value) {
    const requested = path.resolve(this.teamRoot, String(value ?? ""));
    const manager = fs.realpathSync(requested);
    const relative = path.relative(this.teamRoot, manager);
    if (relative.startsWith("..") || path.isAbsolute(relative)) {
      throw new Error("project manager path must remain inside team root");
    }
    return manager;
  }

  validateSource(value, documentFormat) {
    if (!new Set(["xml", "markdown"]).has(documentFormat)) {
      throw new Error("doc_format must be xml or markdown");
    }
    const requested = path.resolve(this.teamRoot, String(value ?? ""));
    const source = fs.realpathSync(requested);
    const relative = path.relative(this.teamRoot, source);
    if (relative.startsWith("..") || path.isAbsolute(relative)) {
      throw new Error("document source must remain inside team root");
    }
    const expectedExtension = documentFormat === "xml" ? ".xml" : ".md";
    if (path.extname(source).toLowerCase() !== expectedExtension) {
      throw new Error("document source extension does not match format");
    }
    const stats = fs.statSync(source);
    if (!stats.isFile() || stats.size > MAX_SOURCE_BYTES) {
      throw new Error("document source is invalid");
    }
    return source;
  }

  async serve(pollMilliseconds = 500) {
    while (true) {
      const result = this.processNext();
      if (result === null) {
        await new Promise((resolve) => setTimeout(resolve, pollMilliseconds));
      }
    }
  }
}

async function main() {
  const teamRoot = process.env.CREATIVE_COLLAB_ROOT;
  const queueRoot = process.env.CREATIVE_COLLAB_FEISHU_DOC_QUEUE;
  const chatId = process.env.CREATIVE_COLLAB_FEISHU_CHAT_ID;
  if (!teamRoot || !queueRoot || !chatId) {
    throw new Error("publisher environment is incomplete");
  }
  const worker = new FeishuDocPublisherWorker({ teamRoot, queueRoot, chatId });
  await worker.serve();
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  main().catch((error) => {
    console.error(error instanceof Error ? error.stack : String(error));
    process.exitCode = 1;
  });
}
