import { spawnSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import { pathToFileURL } from "node:url";

const REQUEST_ID_PATTERN = /^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$/;
const DISPATCH_ID_PATTERN = /^DISPATCH-[A-Za-z0-9-]{1,64}$/;
const ROLES = new Set(["编导", "拍摄", "平面", "剪辑"]);
const FAILURE_MESSAGE = "任务暂未成功交给执行角色，请稍后重试。";

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
  const env = { ...process.env };
  delete env.CREATIVE_COLLAB_DISPATCH_QUEUE;
  const completed = spawnSync(args[0], args.slice(1), {
    cwd,
    encoding: "utf8",
    env,
    timeout: 180_000,
  });
  if (completed.error || completed.status !== 0) {
    const detail = String(completed.stderr || completed.error || "dispatch command failed");
    throw new Error(detail);
  }
  return JSON.parse(completed.stdout);
}

export class CreativeDispatchWorker {
  constructor({
    teamRoot,
    queueRoot,
    runner,
    pythonBinary = "/usr/bin/python3",
    runCommand = defaultRunCommand,
    logger = console,
  }) {
    this.teamRoot = fs.realpathSync(path.resolve(teamRoot));
    this.queueRoot = path.resolve(queueRoot);
    this.runner = fs.realpathSync(path.resolve(runner));
    this.pythonBinary = pythonBinary;
    this.runCommand = runCommand;
    this.logger = logger;
  }

  processNext() {
    const pendingDirectory = path.join(this.queueRoot, "pending");
    fs.mkdirSync(pendingDirectory, { recursive: true });
    const requestName = fs.readdirSync(pendingDirectory)
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
      result = this.submit(request);
    } catch (error) {
      this.logger.error(error instanceof Error ? error.stack : String(error));
      result = {
        request_id: String(request.request_id || path.parse(requestName).name),
        dispatch_id: String(request.dispatch_id || ""),
        status: "failed",
        message: FAILURE_MESSAGE,
        completed_at: now(),
      };
    }

    writeJsonAtomic(path.join(this.queueRoot, "results", `${result.request_id}.json`), result);
    const processedPath = path.join(this.queueRoot, "processed", requestName);
    fs.mkdirSync(path.dirname(processedPath), { recursive: true });
    fs.renameSync(processingPath, processedPath);
    return result;
  }

  submit(request) {
    const requestId = String(request.request_id ?? "").trim();
    const dispatchId = String(request.dispatch_id ?? "").trim();
    const role = String(request.role ?? "").trim();
    if (!REQUEST_ID_PATTERN.test(requestId)) {
      throw new Error("request_id format is invalid");
    }
    if (!DISPATCH_ID_PATTERN.test(dispatchId)) {
      throw new Error("dispatch_id format is invalid");
    }
    if (!ROLES.has(role)) {
      throw new Error("role is invalid");
    }

    const delivered = this.runCommand([
      this.pythonBinary,
      this.runner,
      "send-dispatch",
      "--direct",
      "--request-id",
      requestId,
      "--dispatch-id",
      dispatchId,
      "--role",
      role,
      "--startup-timeout",
      "30",
    ], this.teamRoot);
    return {
      request_id: requestId,
      dispatch_id: dispatchId,
      status: "complete",
      dispatch: delivered.dispatch,
      delivery: delivered.delivery,
      already_delivered: Boolean(delivered.already_delivered),
      completed_at: now(),
    };
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
  const queueRoot = process.env.CREATIVE_COLLAB_DISPATCH_QUEUE;
  const runner = process.env.CREATIVE_COLLAB_TEAM_RUNNER;
  if (!teamRoot || !queueRoot || !runner) {
    throw new Error("dispatch worker environment is incomplete");
  }
  const worker = new CreativeDispatchWorker({ teamRoot, queueRoot, runner });
  await worker.serve();
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  main().catch((error) => {
    console.error(error instanceof Error ? error.stack : String(error));
    process.exitCode = 1;
  });
}
