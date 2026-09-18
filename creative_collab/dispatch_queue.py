from __future__ import annotations

import json
import os
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict


REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$")
DISPATCH_ID_PATTERN = re.compile(r"^DISPATCH-[A-Za-z0-9-]{1,64}$")
ROLES = {"编导", "拍摄", "平面", "剪辑"}


class CreativeDispatchQueue:
    def __init__(self, queue_root: Path) -> None:
        self.queue_root = Path(queue_root).expanduser().resolve()

    def enqueue(
        self, request_id: str, dispatch_id: str, role: str
    ) -> Dict[str, Any]:
        request_id = self._validate_request_id(request_id)
        dispatch_id = self._validate_dispatch_id(dispatch_id)
        role = self._validate_role(role)
        result_path = self._result_path(request_id)
        if result_path.exists():
            return json.loads(result_path.read_text(encoding="utf-8"))

        pending_path = self.queue_root / "pending" / f"{request_id}.json"
        processing_path = self.queue_root / "processing" / f"{request_id}.json"
        if pending_path.exists() or processing_path.exists():
            return {
                "request_id": request_id,
                "dispatch_id": dispatch_id,
                "status": "queued",
            }

        payload = {
            "request_id": request_id,
            "dispatch_id": dispatch_id,
            "role": role,
            "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        }
        self._write_json_atomic(pending_path, payload)
        return {
            "request_id": request_id,
            "dispatch_id": dispatch_id,
            "status": "queued",
        }

    def wait_for_result(
        self, request_id: str, timeout_seconds: float = 120
    ) -> Dict[str, Any]:
        request_id = self._validate_request_id(request_id)
        result_path = self._result_path(request_id)
        deadline = time.monotonic() + max(0, timeout_seconds)
        while time.monotonic() <= deadline:
            if result_path.exists():
                return json.loads(result_path.read_text(encoding="utf-8"))
            time.sleep(0.2)
        return {
            "request_id": request_id,
            "status": "queued",
            "message": "任务正在交给执行角色，请稍后查看进度。",
        }

    @staticmethod
    def _validate_request_id(value: str) -> str:
        request_id = str(value).strip()
        if not REQUEST_ID_PATTERN.fullmatch(request_id):
            raise ValueError("request_id format is invalid")
        return request_id

    @staticmethod
    def _validate_dispatch_id(value: str) -> str:
        dispatch_id = str(value).strip()
        if not DISPATCH_ID_PATTERN.fullmatch(dispatch_id):
            raise ValueError("dispatch_id format is invalid")
        return dispatch_id

    @staticmethod
    def _validate_role(value: str) -> str:
        role = str(value).strip()
        if role not in ROLES:
            raise ValueError("role is invalid")
        return role

    def _result_path(self, request_id: str) -> Path:
        return self.queue_root / "results" / f"{request_id}.json"

    @staticmethod
    def _write_json_atomic(path: Path, payload: Dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        os.replace(str(temporary), str(path))
