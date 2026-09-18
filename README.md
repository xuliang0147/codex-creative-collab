# Codex 创意多 Agent 协作

将多个真实 Codex 任务组织成“编导 -> 拍摄 / 平面 / 剪辑 / 即梦 -> 原编导审核”的本地协作系统。

这是 2026-09-18 整理的代码备份与可配置版本，默认保存在私有仓库。它不是聊天记录备份，不是视频剪辑软件，也不包含已经登录的账号、真实团队绑定或素材。

## 本次同步内容

- 多编导并行：不同产品使用独立编导及四个专属执行任务，同一执行任务不能跨团队复用。
- 项目归属路由：根据项目的编导任务和主机定位执行者，反馈返回原编导；缺少绑定时拒绝派发，不回退到旧公共角色。
- 任务、产物、交接、真实回执、审核、返修、版本和审计记录。
- 有约束的进度报告误交付恢复，避免仅交了状态说明就把剪辑任务当成已完成。
- SQLite 权威账本、Markdown 状态镜像、CLI 和 MCP stdio 接口。
- 可选的飞书编导中继、派发队列和“一项目一文档”更新与评论拉取。
- 拍摄、平面、编导、剪辑的交付模板和中文工作流。

## 与已有仓库的关系

| 仓库 | 职责 |
| --- | --- |
| 本仓库 | 创意协作状态、角色路由、交接与审核 |
| [feishu-codex-bridge-private](https://github.com/xuliang0147/feishu-codex-bridge-private) | 飞书消息与 Codex 的桥接入口，需单独部署 |
| [codex-video-editing-skills](https://github.com/xuliang0147/codex-video-editing-skills) | 剪辑质量标准与验收方法，不是执行引擎 |

这次没有修改另两个仓库，也没有把本机正在运行的系统替换成此副本。私有链接需要有权限的 GitHub 账号登录。

## 架构

```text
用户 / 飞书入口
       |
       v
编导 A -- 项目 A -- 专属拍摄 A / 平面 A / 剪辑 A / 即梦 A
编导 B -- 项目 B -- 专属拍摄 B / 平面 B / 剪辑 B / 即梦 B
       |
       v
CLI / MCP -> creative_collab.service -> SQLite + 项目文件
       |
       v
dispatch_prepare -> 真实任务消息 -> mark_sent -> 真实回执 -> mark_received
       |
       v
编导审核 -> 指定责任角色返修 -> 保留旧版本 -> 验收完成
```

**“已排队”“已发送”“已接收”“文件技术验证通过”“编导审核通过”是不同状态。** 测试通过不代表真实任务已经交接或视频已经交付。

## 本地验证

建议 macOS + Python 3.12 + Node.js 24。核心使用 Python 标准库和 Node 内置模块；媒体相关测试需要 `ffmpeg`、`ffprobe` 在 PATH 中。实际编辑还需要独立安装、授权相应工具。

在仓库根目录执行：

```bash
python3 -m unittest discover -s tests -p 'test_*.py'
node --test tests/creative_dispatch_worker.test.mjs tests/feishu_doc_publisher_worker.test.mjs
```

只初始化一个隔离的演示账本，不派发真实任务、不调用模型、不发送飞书：

```bash
python3 -m creative_collab.cli \
  --db-path "$PWD/.runtime/demo/registry.sqlite" \
  --creative-root "$PWD/.runtime/demo/workspace" bootstrap
```

没有指定配置时，普通入口默认使用 `~/codex-creative-workspace`，飞书团队 runner 使用 `~/codex-creative-team`。与现有个人 Codex 协作数据库分离。所有本地演示路径应保持一致，不要混用多个账本。

## 使用与配置

- [配置、注册和启动说明](docs/部署与配置.md)
- [团队隔离与报告恢复](docs/团队隔离与任务恢复.md)
- [角色工作流](creative_collab/ROLE_WORKFLOW.md)
- [剪辑标准接入](docs/剪辑标准接入.md)
- [安全与已知限制](docs/安全与限制.md)
- [更新记录与验证结果](CHANGELOG.md)

仓库中的 `deploy/feishu_creative_team/AGENTS.md` 等文件是飞书团队的部署规则模板，不是要求当前开发任务自动注册、派发或发群消息。

## 不随仓库上传

不包含生产数据库、私有群号、真实任务绑定、聊天历史、登录态、Token、用户素材、成片、日志和缓存。恢复业务运行还需要自行恢复受控保存的数据库和素材，重新核对本机工具与账号授权。**代码备份不能代替业务数据备份。**

## 已知边界

- 团队隔离是路由和账本约束，不是不同系统账号、容器或文件系统沙箱。
- 多个任务操作同一个剪映或 ChatCut 窗口时仍需互斥，不能同时抢占鼠标和工程。
- 当前旧版业务验收仍要求至少两条有效成片；如业务只要单条，应另行修改验收契约，不能靠占位视频或手改数据库绕过。
- 新版团队路由支持即梦，旧的可选派发队列仍只接受编导、拍摄、平面、剪辑四类发送角色。即梦回传优先走宿主原生任务消息和完整回执流程。
- 飞书项目同步与评论拉取应使用 Node 文档 worker；Python `once/serve` 是旧的一次性发布路径，不等价于项目同步服务。
- 不自动创建 Codex 可见任务、不自动发布抖音、不自动开通付费工具、不自动启用每日会议。
- 私有个人部署版本，尚未作为面向不可信多用户的产品进行安全加固。
