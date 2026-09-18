from __future__ import annotations

import shutil
import sqlite3
import struct
import subprocess
import zlib
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .service import CreativeCollabService, WorkflowError


def run_mock_topic_flow(
    service: CreativeCollabService,
    source_asset_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """Run one complete V0.1 topic with real files and simulated delivery receipts."""
    service.bootstrap_v01(request_id="SIM-BOOTSTRAP-V01")
    for role, slug in (
        ("编导", "director"),
        ("拍摄", "shooting"),
        ("平面", "graphics"),
        ("剪辑", "editor"),
    ):
        service.agent_bind_thread(
            request_id=f"SIM-BIND-{slug.upper()}",
            role=role,
            thread_id=f"mock-thread-{slug}",
            host_id="mock-local",
        )

    project = service.project_create(
        request_id="SIM-PROJECT-001",
        title="学习机暑期课程演示",
        owner_role="编导",
        brief="面向家长展示暑期课程、拍照批改和课程页面操作，产出两条混剪版本。",
        tags=["作业帮", "学习机", "暑期", "课程演示"],
        requirements_required=True,
    )
    project_path = Path(project["project_path"])
    external_deliveries: List[Dict[str, str]] = []

    service.requirements_submit(
        request_id="SIM-REQUIREMENTS-SUBMIT",
        project_id=project["project_id"],
        role="编导",
        goal="用真实家长压力切入，展示学习机暑期课程和拍照批改能力",
        target_audience="暑期需要辅导孩子的小学家长",
        platform="抖音千川",
        duration_seconds=15,
        deliverables=["A版混剪", "B版混剪"],
        available_assets=["先检索已有学习机课程演示素材"],
        creative_direction="真实压力场景开场，产品能力承接，行动引导收尾",
        constraints=["正式成片只使用真实学习机产品证据"],
        open_questions=["家长真人镜头和产品正面镜头是否齐全"],
    )
    requirements = service.requirements_confirm(
        request_id="SIM-REQUIREMENTS-CONFIRM",
        project_id=project["project_id"],
        role="编导",
        confirmation_note="模拟用户已逐项确认，可以制作脚本并向拍摄、平面派发。",
    )

    script_path = _write_text_artifact(
        project_path,
        "01_编导/脚本-v1.md",
        """# 学习机暑期课程演示脚本 v1

| 时间 | 画面 | 口播/字幕 |
| --- | --- | --- |
| 0-3 秒 | 家长面对暑期安排表 | 暑期没人盯，学习节奏容易断？ |
| 3-9 秒 | 学习机课程页与老师操作 | 按年级规划课程，打开就能学。 |
| 9-13 秒 | 拍照批改结果页 | 拍题、批改、错因反馈一次完成。 |
| 13-15 秒 | 产品正面与行动字幕 | 暑期学习计划，现在就安排。 |
""",
    )
    script = service.artifact_submit(
        request_id="SIM-SCRIPT-V1",
        project_id=project["project_id"],
        role="编导",
        artifact_type="script",
        relative_path=_relative_to_project(project_path, script_path),
        description="15秒短视频脚本：痛点开场，展示暑期课程和拍照批改。",
    )

    shooting_task = service.task_assign(
        request_id="SIM-TASK-SHOOTING",
        project_id=project["project_id"],
        from_role="编导",
        to_role="拍摄",
        summary="拆解脚本素材需求，先检索素材库，不足再列待拍清单。",
        inputs=[script["relative_path"]],
        acceptance_criteria=[
            "先检索素材库",
            "输出已有素材匹配表",
            "不足内容列待拍清单",
        ],
    )
    _deliver_entity(
        service,
        project["project_id"],
        "task",
        shooting_task["task_id"],
        external_deliveries,
        task=shooting_task,
    )

    if source_asset_path is None:
        source_asset_path = (
            service.asset_root / "视频" / "模拟-作业帮学习机暑期课程演示.mp4"
        )
    source_asset_path = Path(source_asset_path)
    _write_mp4(source_asset_path, color="0x2B6CB0", frequency=330)
    asset = service.asset_scan(
        request_id="SIM-ASSET-001",
        file_path=source_asset_path,
        user_title="作业帮学习机暑期课程演示，竖屏近景，老师操作课程页面",
    )
    matches = service.asset_search(
        brand="作业帮",
        product="学习机",
        usage="课程演示素材",
        technical="竖屏",
    )
    service.asset_reference(
        request_id="SIM-ASSET-REF-001",
        project_id=project["project_id"],
        asset_id=asset["asset_id"],
        role="拍摄",
        usage_note="课程演示和老师操作课程页面镜头",
        output_path="04_剪辑/成片/混剪A-v1.mp4",
    )

    match_path = _write_text_artifact(
        project_path,
        "02_拍摄/素材匹配表.md",
        f"""# 素材匹配表

| 脚本段落 | 素材 ID | 已有内容 | 适用镜头 | 结论 |
| --- | --- | --- | --- | --- |
| 3-9 秒 | {asset['asset_id']} | 学习机课程页、老师操作 | 竖屏近景 | 可直接使用 |
| 9-13 秒 | {asset['asset_id']} | 课程页面操作 | 功能过场 | 可裁切使用 |
""",
    )
    match_artifact = service.artifact_submit(
        request_id="SIM-SHOOT-MATCH",
        project_id=project["project_id"],
        role="拍摄",
        artifact_type="asset_match_table",
        relative_path=_relative_to_project(project_path, match_path),
        description="已匹配竖屏课程演示素材，并标注脚本适用段落。",
    )
    missing_path = _write_text_artifact(
        project_path,
        "02_拍摄/待拍清单.md",
        """# 待拍清单

| 优先级 | 缺口 | 规格 | 验收 |
| --- | --- | --- | --- |
| P0 | 家长暑期焦虑反应 | 竖屏近景，3 秒 | 表情清楚、无杂音 |
| P0 | 产品正面开箱 | 竖屏中近景，4 秒 | Logo 清楚、无反光 |
""",
    )
    missing_artifact = service.artifact_submit(
        request_id="SIM-SHOOT-MISSING",
        project_id=project["project_id"],
        role="拍摄",
        artifact_type="shooting_request",
        relative_path=_relative_to_project(project_path, missing_path),
        description="列出家长反馈口播和产品正面开箱两个待拍缺口。",
    )
    shooting_handoff = service.handoff_submit(
        request_id="SIM-HANDOFF-SHOOTING",
        task_id=shooting_task["task_id"],
        from_role="拍摄",
        to_role="编导",
        summary="素材核验完成，现有课程演示可用；家长真人和产品正面镜头需补拍，请编导统一汇总。",
        artifacts=[match_artifact["relative_path"], missing_artifact["relative_path"]],
    )

    asset_request = service.asset_request(
        request_id="SIM-ASSET-REQUEST",
        project_id=project["project_id"],
        role="编导",
        description="补拍家长暑期压力真人镜头和学习机产品正面镜头，补齐后先由拍摄复核。",
        target_role="拍摄",
    )
    _deliver_entity(
        service,
        project["project_id"],
        "handoff",
        shooting_handoff["handoff_id"],
        external_deliveries,
    )

    graphics_task = service.task_assign(
        request_id="SIM-TASK-GRAPHICS",
        project_id=project["project_id"],
        from_role="编导",
        to_role="平面",
        summary="制作封面和直播贴片，突出暑期课程和拍照批改。",
        inputs=[match_artifact["relative_path"]],
        acceptance_criteria=["提交封面-v1", "提交贴片-v1", "说明素材来源"],
    )
    _deliver_entity(
        service,
        project["project_id"],
        "task",
        graphics_task["task_id"],
        external_deliveries,
        task=graphics_task,
    )

    cover_path = project_path / "03_平面/封面-v1.png"
    _write_png(cover_path, background=(18, 94, 79), accent=(255, 214, 102))
    cover = service.artifact_submit(
        request_id="SIM-GRAPHICS-COVER",
        project_id=project["project_id"],
        role="平面",
        artifact_type="image",
        relative_path=_relative_to_project(project_path, cover_path),
        description="暑期课程演示封面：课程页面、学习机主体和高对比卖点区。",
    )
    overlay_path = project_path / "03_平面/贴片-v1.png"
    _write_png(overlay_path, background=(245, 247, 250), accent=(201, 62, 62))
    overlay = service.artifact_submit(
        request_id="SIM-GRAPHICS-OVERLAY",
        project_id=project["project_id"],
        role="平面",
        artifact_type="image",
        relative_path=_relative_to_project(project_path, overlay_path),
        description="拍照批改、课程演示、暑期规划三个卖点贴片。",
    )
    graphics_handoff = service.handoff_submit(
        request_id="SIM-HANDOFF-GRAPHICS",
        task_id=graphics_task["task_id"],
        from_role="平面",
        to_role="编导",
        summary="封面和贴片已提交，请编导汇总并等待补拍素材齐套。",
        artifacts=[cover["relative_path"], overlay["relative_path"]],
    )
    _deliver_entity(
        service,
        project["project_id"],
        "handoff",
        graphics_handoff["handoff_id"],
        external_deliveries,
    )

    editing_blocked_before_assets = False
    try:
        service.task_assign(
            request_id="SIM-TASK-EDITING-BLOCKED",
            project_id=project["project_id"],
            from_role="编导",
            to_role="剪辑",
            summary="素材未补齐前不应启动的剪辑任务",
            inputs=[script["relative_path"]],
            acceptance_criteria=["不得被创建"],
        )
    except WorkflowError:
        editing_blocked_before_assets = True
    if not editing_blocked_before_assets:
        raise WorkflowError("editing must be blocked while asset requests are open")

    supplemented_asset_path = service.asset_root / "视频" / "模拟-补拍家长与产品正面.mp4"
    _write_mp4(supplemented_asset_path, color="0xD69E2E", frequency=392)
    supplemented_asset = service.asset_scan(
        request_id="SIM-ASSET-SUPPLEMENTED",
        file_path=supplemented_asset_path,
        user_title="家长暑期压力真人镜头与学习机产品正面，竖屏清晰",
    )
    service.asset_reference(
        request_id="SIM-ASSET-REF-SUPPLEMENTED",
        project_id=project["project_id"],
        asset_id=supplemented_asset["asset_id"],
        role="拍摄",
        usage_note="补齐家长开场和产品正面收尾镜头，已复核可用于正式成片",
        output_path="04_剪辑/成片/混剪A-v1.mp4",
    )
    service.asset_request_resolve(
        request_id="SIM-ASSET-REQUEST-RESOLVE",
        asset_request_id=asset_request["asset_request_id"],
        role="编导",
        resolution="模拟用户已上传，拍摄复核通过；素材齐套，可以剪辑。",
    )

    editing_task = service.task_assign(
        request_id="SIM-TASK-EDITING",
        project_id=project["project_id"],
        from_role="编导",
        to_role="剪辑",
        summary="基于脚本、素材匹配和平面成品，输出两条不同混剪版本。",
        inputs=[script["relative_path"], match_artifact["relative_path"], cover["relative_path"]],
        acceptance_criteria=["至少两条混剪", "登记差异说明", "保留素材引用"],
    )
    _deliver_entity(
        service,
        project["project_id"],
        "task",
        editing_task["task_id"],
        external_deliveries,
        task=editing_task,
    )

    edit_a_path = project_path / "04_剪辑/成片/混剪A-v1.mp4"
    edit_b_path = project_path / "04_剪辑/成片/混剪B-v1.mp4"
    _write_mp4(edit_a_path, color="0xC53030", frequency=440)
    _write_mp4(edit_b_path, color="0x2F855A", frequency=554)
    edit_a = service.artifact_submit(
        request_id="SIM-EDIT-A-V1",
        project_id=project["project_id"],
        role="剪辑",
        artifact_type="video",
        relative_path=_relative_to_project(project_path, edit_a_path),
        description="版本A：痛点开场，强调暑期课程演示。",
    )
    edit_b = service.artifact_submit(
        request_id="SIM-EDIT-B-V1",
        project_id=project["project_id"],
        role="剪辑",
        artifact_type="video",
        relative_path=_relative_to_project(project_path, edit_b_path),
        description="版本B：功能证明开场，强调拍照批改。",
    )
    editing_handoff = service.handoff_submit(
        request_id="SIM-HANDOFF-EDITING",
        task_id=editing_task["task_id"],
        from_role="剪辑",
        to_role="编导",
        summary="两条差异化混剪已提交，请编导审核。",
        artifacts=[edit_a["relative_path"], edit_b["relative_path"]],
    )
    _deliver_entity(
        service,
        project["project_id"],
        "handoff",
        editing_handoff["handoff_id"],
        external_deliveries,
    )

    review = service.review_submit(
        request_id="SIM-REVIEW-NEEDS-EDIT",
        project_id=project["project_id"],
        reviewer_role="编导",
        result="revision_required",
        issues=[
            {
                "responsible_role": "剪辑",
                "artifact_id": edit_a["artifact_id"],
                "issue_type": "节奏",
                "requirement": "混剪A前3秒信息进入慢，压缩开头并强化字幕卖点。",
            }
        ],
    )
    revision = service.revision_return(
        request_id="SIM-RETURN-EDIT",
        review_id=review["review_id"],
        issue_id=review["issues"][0]["issue_id"],
        from_role="编导",
        to_role="剪辑",
    )
    _deliver_entity(
        service,
        project["project_id"],
        "revision",
        revision["revision_id"],
        external_deliveries,
    )

    edit_a_v2_path = project_path / "04_剪辑/成片/混剪A-v2.mp4"
    _write_mp4(edit_a_v2_path, color="0x805AD5", frequency=659)
    edit_a_v2 = service.artifact_submit(
        request_id="SIM-EDIT-A-V2",
        project_id=project["project_id"],
        role="剪辑",
        artifact_type="video",
        relative_path=_relative_to_project(project_path, edit_a_v2_path),
        description="版本A返工：3秒内出现暑期课程痛点和课程页面，字幕强化。",
        supersedes_artifact_id=edit_a["artifact_id"],
    )
    if edit_a_v2["sha256"] == edit_a["sha256"]:
        raise WorkflowError("revised video must have a new content hash")

    approval = service.review_submit(
        request_id="SIM-REVIEW-APPROVED",
        project_id=project["project_id"],
        reviewer_role="编导",
        result="approved",
        issues=[],
    )
    completed = service.project_complete(
        request_id="SIM-COMPLETE",
        project_id=project["project_id"],
        role="编导",
    )

    latest_revision, latest_issue = _read_revision_and_issue(
        service.db_path,
        revision["revision_id"],
        review["issues"][0]["issue_id"],
    )
    pending_dispatch_count = len(
        service.dispatch_list(project_id=project["project_id"], status="pending")
    )
    sent_dispatch_count = len(
        service.dispatch_list(project_id=project["project_id"], status="sent")
    )
    artifact_ids = {
        "script": script["artifact_id"],
        "asset_match_table": match_artifact["artifact_id"],
        "shooting_request": missing_artifact["artifact_id"],
        "cover": cover["artifact_id"],
        "overlay": overlay["artifact_id"],
        "edit_a_v1": edit_a["artifact_id"],
        "edit_b_v1": edit_b["artifact_id"],
        "edit_a_v2": edit_a_v2["artifact_id"],
    }
    return {
        "project": completed,
        "project_status": completed["status"],
        "requirements_status": requirements["status"],
        "editing_blocked_before_assets": editing_blocked_before_assets,
        "matched_asset_ids": [match["asset_id"] for match in matches],
        "artifacts": artifact_ids,
        "artifact_ids": list(artifact_ids.values()),
        "revision": latest_revision,
        "issue": latest_issue,
        "approval": approval,
        "pending_dispatch_count": pending_dispatch_count,
        "undelivered_dispatch_count": pending_dispatch_count + sent_dispatch_count,
        "external_delivery_count": len(external_deliveries),
        "external_deliveries": external_deliveries,
    }


def _deliver_entity(
    service: CreativeCollabService,
    project_id: str,
    entity_type: str,
    entity_id: str,
    deliveries: List[Dict[str, str]],
    task: Optional[Dict[str, Any]] = None,
) -> None:
    dispatch = next(
        item
        for item in service.dispatch_list(project_id=project_id)
        if item["entity_type"] == entity_type and item["entity_id"] == entity_id
    )
    prepared = service.dispatch_prepare(
        request_id=f"SIM-PREPARE-{dispatch['dispatch_id']}",
        dispatch_id=dispatch["dispatch_id"],
        role=dispatch["from_role"],
    )
    submission_id = _simulate_external_send(prepared, deliveries)
    service.dispatch_mark_sent(
        request_id=f"SIM-MARK-SENT-{dispatch['dispatch_id']}",
        dispatch_id=dispatch["dispatch_id"],
        from_role=dispatch["from_role"],
        prepare_token=prepared["prepare_token"],
        submission_id=submission_id,
    )
    service.dispatch_mark_received(
        request_id=f"SIM-MARK-RECEIVED-{dispatch['dispatch_id']}",
        dispatch_id=dispatch["dispatch_id"],
        role=dispatch["to_role"],
    )
    if task is not None:
        try:
            service.task_accept(
                request_id=f"SIM-ACCEPT-{task['task_id']}",
                task_id=task["task_id"],
                role=task["to_role"],
            )
        except WorkflowError as exc:
            # Current V0.1 receipt handling already accepts the task. Keep the
            # explicit task_accept call so the simulated lifecycle matches MCP use.
            already_accepted = _task_status(service, task["task_id"]) == "accepted"
            if "got received" not in str(exc) or not already_accepted:
                raise


def _simulate_external_send(
    prepared: Dict[str, Any], deliveries: List[Dict[str, str]]
) -> str:
    submission_id = f"mock-submission-{len(deliveries) + 1:03d}"
    deliveries.append(
        {
            "dispatch_id": prepared["dispatch_id"],
            "thread_id": prepared["thread_id"],
            "host_id": prepared["host_id"],
            "submission_id": submission_id,
            "message": prepared["message"],
        }
    )
    return submission_id


def _write_text_artifact(project_path: Path, relative_path: str, content: str) -> Path:
    path = project_path / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text(content, encoding="utf-8")
    return path


def _write_png(
    path: Path,
    background: Tuple[int, int, int],
    accent: Tuple[int, int, int],
    width: int = 360,
    height: int = 640,
) -> None:
    if path.exists():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = bytearray()
    for y in range(height):
        rows.append(0)
        for x in range(width):
            use_accent = height // 3 < y < height // 2 or x < width // 18
            rows.extend(accent if use_accent else background)
    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    payload = b"\x89PNG\r\n\x1a\n"
    payload += _png_chunk(b"IHDR", header)
    payload += _png_chunk(b"IDAT", zlib.compress(bytes(rows), level=9))
    payload += _png_chunk(b"IEND", b"")
    path.write_bytes(payload)


def _png_chunk(chunk_type: bytes, data: bytes) -> bytes:
    checksum = zlib.crc32(chunk_type)
    checksum = zlib.crc32(data, checksum) & 0xFFFFFFFF
    return struct.pack(">I", len(data)) + chunk_type + data + struct.pack(">I", checksum)


def _write_mp4(path: Path, color: str, frequency: int) -> None:
    if path.exists():
        return
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        raise WorkflowError("ffmpeg is required to generate playable simulation videos")
    path.parent.mkdir(parents=True, exist_ok=True)
    command = [
        ffmpeg,
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-f",
        "lavfi",
        "-i",
        f"color=c={color}:s=360x640:r=24:d=1.2",
        "-f",
        "lavfi",
        "-i",
        f"sine=frequency={frequency}:sample_rate=44100:duration=1.2",
        "-shortest",
        "-c:v",
        "mpeg4",
        "-q:v",
        "4",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-b:a",
        "64k",
        "-movflags",
        "+faststart",
        str(path),
    ]
    completed = subprocess.run(command, capture_output=True, text=True, check=False)
    if completed.returncode != 0 or not path.is_file():
        detail = completed.stderr.strip() or f"exit code {completed.returncode}"
        raise WorkflowError(f"ffmpeg failed to generate {path.name}: {detail}")


def _relative_to_project(project_path: Path, path: Path) -> str:
    return path.relative_to(project_path).as_posix()


def _task_status(service: CreativeCollabService, task_id: str) -> str:
    with sqlite3.connect(service.db_path) as conn:
        row = conn.execute(
            "select status from tasks where task_id = ?", (task_id,)
        ).fetchone()
    if row is None:
        raise WorkflowError(f"task not found after receipt: {task_id}")
    return str(row[0])


def _read_revision_and_issue(
    db_path: Path, revision_id: str, issue_id: str
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        revision = conn.execute(
            "select * from revision_returns where revision_id = ?", (revision_id,)
        ).fetchone()
        issue = conn.execute(
            "select * from review_issues where issue_id = ?", (issue_id,)
        ).fetchone()
    if revision is None or issue is None:
        raise WorkflowError("simulation revision state could not be re-read")
    return dict(revision), dict(issue)
