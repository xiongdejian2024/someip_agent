"""基线比较及完整证据包；校验输入/结果/记录，不执行历史配置。"""

from __future__ import annotations

import hashlib
import json
import logging
import platform
import shutil
import sys
import tempfile
import zipfile
from pathlib import Path
from typing import TYPE_CHECKING, Any
from uuid import UUID

from someip_agent.config import Settings

from .projects import ProjectView
from .recordings import RecordingView
from .scenario_models import RunView, ScenarioDefinition, canonical

if TYPE_CHECKING:
    from someip_agent.state import ApplicationState

logger = logging.getLogger(__name__)


class ResultConflict(ValueError):
    pass


def file_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def runtime_identity(settings: Settings) -> dict[str, str]:
    identity = {"python_version": platform.python_version()}
    if getattr(sys, "frozen", False):
        identity["backend_sha256"] = file_digest(Path(sys.executable))
    else:
        root = Path(__file__).parent.parent
        digest = hashlib.sha256()
        for path in sorted(root.rglob("*.py")):
            digest.update(str(path.relative_to(root)).encode())
            digest.update(file_digest(path).encode())
        identity["backend_sha256"] = digest.hexdigest()
    native = shutil.which(settings.native_binary)
    if native:
        identity["native_sha256"] = file_digest(Path(native))
    return identity


def verify_evidence(path: Path) -> dict[str, Any]:
    """离线只读校验，不解压到文件系统、不执行内容；SHA 不是发布者认证。"""
    allowed = {
        "result.json",
        "inputs.json",
        "model.json",
        "junit.xml",
        "report.html",
        "audit.json",
        "recording.zip",
        "source.arxml",
    }
    if path.stat().st_size > 1024 * 1024 * 1024:
        raise ValueError("证据 ZIP 超过 1 GiB 校验上限")
    with zipfile.ZipFile(path) as archive:
        members = archive.infolist()
        if len(members) > 9 or len({item.filename for item in members}) != len(members):
            raise ValueError("证据 ZIP 成员过多或含重复名称")
        if any(item.filename not in allowed | {"manifest.json"} for item in members):
            raise ValueError("证据 ZIP 含非法名称，不接受路径或未知附件")
        if sum(item.file_size for item in members) > 1024 * 1024 * 1024:
            raise ValueError("证据 ZIP 解码内容超过 1 GiB")
        if archive.getinfo("manifest.json").file_size > 1024 * 1024:
            raise ValueError("证据清单超过 1 MiB")
        manifest = json.loads(archive.read("manifest.json"))
        if (
            not isinstance(manifest, dict)
            or manifest.get("format") != "someip-agent-evidence"
            or type(manifest.get("format_version")) is not int
            or manifest["format_version"] != 1
        ):
            raise ValueError("证据格式或版本不支持")
        for field in (
            "source_arxml_included",
            "model_projection_included",
            "record_complete",
            "cleanup_complete",
            "audit_complete",
            "complete",
        ):
            if type(manifest.get(field)) is not bool:
                raise ValueError("证据完整性声明必须为布尔值")
        files = manifest["files"]
        if not isinstance(files, dict) or set(files) | {"manifest.json"} != {
            item.filename for item in members
        }:
            raise ValueError("证据清单附件集合不符")
        if not allowed.difference({"recording.zip", "source.arxml"}).issubset(files):
            raise ValueError("证据缺少必需附件")
        for name, expected in files.items():
            if (
                not isinstance(expected, dict)
                or type(expected.get("bytes")) is not int
                or expected["bytes"] != archive.getinfo(name).file_size
            ):
                raise ValueError("证据附件大小不符")
            digest = hashlib.sha256()
            with archive.open(name) as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(chunk)
            if digest.hexdigest() != expected.get("sha256"):
                raise ValueError("证据附件 SHA-256 不符")
        for name, limit in (("inputs.json", 12), ("result.json", 8), ("model.json", 8)):
            if archive.getinfo(name).file_size > limit * 1024 * 1024:
                raise ValueError("证据结构化输入或结果超过上限")
        view = RunView.model_validate_json(archive.read("result.json"))
        if view.status == "running" or view.finished_at is None:
            raise ValueError("运行尚未结束，不能作为封存证据")
        inputs = json.loads(archive.read("inputs.json"))
        project = ProjectView.model_validate(inputs["project"])
        ScenarioDefinition.model_validate(inputs["definition"])
        model = project.document.model
        if (
            manifest.get("model_source_sha256") != view.model_source_sha256
            or (model.source_sha256 if model else None) != view.model_source_sha256
            or not manifest["model_projection_included"]
        ):
            raise ValueError("模型源或投影声明与运行结果不符")
        if (
            hashlib.sha256(canonical(inputs["definition"]).encode()).hexdigest()
            != view.definition_sha256
        ):
            raise ValueError("场景定义哈希与运行结果不符")
        if canonical(json.loads(archive.read("model.json"))) != canonical(
            inputs["project"]["document"]["model"]
        ):
            raise ValueError("模型投影与工程快照不符")
        if manifest["run_id"] != str(view.id) or manifest["request_id"] != view.request_id:
            raise ValueError("运行或审计请求关联不符")
        if view.project_id != project.id or view.project_revision != project.revision:
            raise ValueError("工程修订关联不符")
        if (
            manifest["application_version"] != view.application_version
            or manifest["runtime_identity"] != view.runtime_identity
        ):
            raise ValueError("程序或原生执行身份不符")
        if manifest["cleanup_complete"] != view.cleanup_complete:
            raise ValueError("资源清理声明不符")
        if archive.getinfo("audit.json").file_size > 16 * 1024 * 1024:
            raise ValueError("审计附件超过 16 MiB")
        audit = json.loads(archive.read("audit.json"))
        if not isinstance(audit, list) or len(audit) > 10000:
            raise ValueError("审计附件数量非法")
        if any(
            row["target"] != str(view.id) or not row["action"].startswith("scenario.")
            for row in audit
        ):
            raise ValueError("审计混入其他运行或无关操作")
        audit_complete = all(
            any(
                row["action"] == action and row["detail"].get("request_id") == view.request_id
                for row in audit
            )
            for action in ("scenario.start", "scenario.finish")
        )
        if manifest["audit_complete"] != audit_complete:
            raise ValueError("运行审计完整性声明不符")
        if manifest["source_arxml_included"] != ("source.arxml" in files):
            raise ValueError("原始 ARXML 附件声明不符")
        if "source.arxml" in files and files["source.arxml"]["sha256"] != view.model_source_sha256:
            raise ValueError("原始 ARXML 与模型源 SHA-256 不符")
        if manifest["recording_id"] != (str(view.recording_id) if view.recording_id else None):
            raise ValueError("原始记录关联不符")
        record_complete = False
        if "recording.zip" in files:
            with archive.open("recording.zip") as stream, zipfile.ZipFile(stream) as recorded:
                entries = recorded.infolist()
                if (
                    len(entries) > 8193
                    or len({item.filename for item in entries}) != len(entries)
                    or sum(item.file_size for item in entries) > 513 * 1024 * 1024
                ):
                    raise ValueError("嵌套原始记录超限或存在重复成员")
                if recorded.getinfo("manifest.json").file_size > 4 * 1024 * 1024:
                    raise ValueError("原始记录清单过大")
                record = RecordingView.model_validate_json(recorded.read("manifest.json"))
                if str(record.id) != manifest["recording_id"]:
                    raise ValueError("嵌套原始记录 ID 不符")
                if {item.filename for item in entries} != {"manifest.json"} | {
                    f"segment-{item.number:05d}.jsonl" for item in record.segments
                }:
                    raise ValueError("嵌套原始记录附件集合不符")
                frame_count, byte_count = 0, 0
                for number, segment in enumerate(record.segments):
                    name = f"segment-{segment.number:05d}.jsonl"
                    if (
                        segment.number != number
                        or segment.first_index != frame_count
                        or recorded.getinfo(name).file_size != segment.bytes
                    ):
                        raise ValueError("原始记录分段顺序或大小不符")
                    digest, lines = hashlib.sha256(), 0
                    with recorded.open(name) as content:
                        for chunk in iter(lambda: content.read(1024 * 1024), b""):
                            digest.update(chunk)
                            lines += chunk.count(b"\n")
                    if digest.hexdigest() != segment.sha256 or lines != segment.frame_count:
                        raise ValueError("原始记录分段 SHA-256 或帧数不符")
                    frame_count += segment.frame_count
                    byte_count += segment.bytes
                if frame_count != record.frame_count or byte_count != record.bytes:
                    raise ValueError("原始记录累计帧数或大小不符")
                record_complete = (
                    record.state == "stopped"
                    and record.finished_at is not None
                    and not record.queue_discarded
                    and not record.buffered_not_recorded
                    and not record.unsealed_frames
                    and not record.last_error
                )
        if manifest["record_complete"] != record_complete:
            raise ValueError("原始记录完整性声明不符")
        if manifest["complete"] and not (
            manifest["source_arxml_included"]
            and manifest["record_complete"]
            and manifest["cleanup_complete"]
            and manifest["audit_complete"]
            and not view.cleanup_errors
            and view.status != "interrupted"
        ):
            raise ValueError("缺失证据却声明完整")
        return manifest


class ResultManager:
    def __init__(self, state: ApplicationState) -> None:
        self.state = state

    def _terminal(self, identifier: UUID):
        view = self.state.runs.get(identifier)
        if view.status == "running":
            raise ResultConflict("运行尚未结束或尚未封存，不能作为基线或导出证据包")
        return view

    def compare(self, current: UUID, baseline: UUID) -> dict[str, Any]:
        left, right = self._terminal(baseline), self._terminal(current)
        left_inputs, right_inputs = (
            self.state.runs.inputs(baseline),
            self.state.runs.inputs(current),
        )
        changes = []
        for field in (
            "application_version",
            "definition_sha256",
            "model_source_sha256",
            "runtime_identity",
        ):
            before, after = getattr(left, field), getattr(right, field)
            if canonical(before) != canonical(after):
                changes.append({"field": field, "baseline": before, "current": after})
        # 工作区、种子、参数与完整模型也保留在配置比较内，不能默默忽略变化。
        before, after = left_inputs["project"]["document"], right_inputs["project"]["document"]
        if canonical(before) != canonical(after):
            changes.append(
                {
                    "field": "project_configuration_sha256",
                    "baseline": hashlib.sha256(canonical(before).encode()).hexdigest(),
                    "current": hashlib.sha256(canonical(after).encode()).hexdigest(),
                }
            )
        left_assertions = {(s.case, s.index): s for s in left.steps if s.kind == "assert"}
        right_assertions = {(s.case, s.index): s for s in right.steps if s.kind == "assert"}
        assertions = []
        for key in sorted(left_assertions.keys() | right_assertions.keys()):
            a, b = left_assertions.get(key), right_assertions.get(key)
            values = {
                "case": key[0],
                "index": key[1],
                "baseline": a.model_dump(mode="json") if a else None,
                "current": b.model_dump(mode="json") if b else None,
            }
            values["changed"] = (
                a is None
                or b is None
                or a.status != b.status
                or a.error != b.error
                or canonical(a.result) != canonical(b.result)
            )
            assertions.append(values)
        comparison = {
            "baseline_id": str(baseline),
            "current_id": str(current),
            "comparable": not changes,
            "metadata_changes": changes,
            "status": {"baseline": left.status, "current": right.status},
            "cleanup_complete": {
                "baseline": left.cleanup_complete,
                "current": right.cleanup_complete,
            },
            "assertions": assertions,
            "duration_ms": {
                "baseline": sum(s.duration_ms for s in left.steps),
                "current": sum(s.duration_ms for s in right.steps),
            },
            "note": (
                "配置/模型/执行代码变化时只展示差异，不自动判为同条件回归通过；耗时不是硬实时判据。"
            ),
        }
        logger.info(
            "产品基线比较已完成", extra={"operation": "scenario.compare", "run_id": str(current)}
        )
        return comparison

    def export(self, identifier: UUID) -> Path:
        view = self._terminal(identifier)
        inputs = self.state.runs.inputs(identifier)
        audit = self.state.audit.for_scenario(str(identifier))
        audit_complete = all(
            any(
                row["action"] == action and row["detail"].get("request_id") == view.request_id
                for row in audit
            )
            for action in ("scenario.start", "scenario.finish")
        )
        bodies = {
            "result.json": view.model_dump_json(indent=2).encode(),
            "inputs.json": canonical(inputs).encode(),
            "model.json": canonical(inputs["project"]["document"]["model"]).encode(),
            "junit.xml": self.state.runs.junit(identifier),
            "report.html": self.state.runs.report(identifier).encode(),
            "audit.json": canonical(audit).encode(),
        }
        model = inputs["project"]["document"]["model"]
        source: Path | None = None
        source_included = False
        digest = model.get("source_sha256") if model else None
        if (
            isinstance(digest, str)
            and len(digest) == 64
            and all(c in "0123456789abcdef" for c in digest)
        ):
            source = self.state.imported_file_path(digest, model["source_name"])
            if source.is_file() and not source.is_symlink():
                if source.stat().st_size > self.state.settings.max_upload_bytes:
                    raise ValueError("原 ARXML 超过导入上限")
                if file_digest(source) != digest:
                    raise ValueError("原 ARXML 文件哈希不符，拒绝导出变更证据")
                source_included = True
            else:
                source = None
        recorded = None
        path = None
        try:
            record_view = None
            if view.recording_id:
                record_view = self.state.recordings.get(view.recording_id)
                recorded = self.state.recordings.export(view.recording_id)
            with tempfile.NamedTemporaryFile(
                prefix="someip-scenario-evidence-", suffix=".zip", delete=False
            ) as file:
                path = Path(file.name)
            entries = {
                name: {"bytes": len(body), "sha256": hashlib.sha256(body).hexdigest()}
                for name, body in bodies.items()
            }
            if recorded:
                entries["recording.zip"] = {
                    "bytes": recorded.stat().st_size,
                    "sha256": file_digest(recorded),
                }
            if source:
                entries["source.arxml"] = {"bytes": source.stat().st_size, "sha256": digest}
            record_complete = bool(
                record_view
                and record_view.state == "stopped"
                and record_view.finished_at is not None
                and not record_view.queue_discarded
                and not record_view.buffered_not_recorded
                and not record_view.unsealed_frames
                and not record_view.last_error
            )
            manifest = {
                "format": "someip-agent-evidence",
                "format_version": 1,
                "run_id": str(identifier),
                "request_id": view.request_id,
                "application_version": view.application_version,
                "runtime_identity": view.runtime_identity,
                "recording_id": str(view.recording_id) if view.recording_id else None,
                "model_source_sha256": view.model_source_sha256,
                "source_arxml_included": source_included,
                "model_projection_included": True,
                "record_complete": record_complete,
                "cleanup_complete": view.cleanup_complete,
                "audit_complete": audit_complete,
                "complete": source_included
                and record_complete
                and view.cleanup_complete
                and audit_complete
                and not view.cleanup_errors
                and view.status != "interrupted",
                "files": entries,
                "note": (
                    "SHA-256 检测内容变更，不是签名或对抗篡改认证。"
                    "缺失原 ARXML 或不完整运行不会冒充完整包；"
                    "不导出本机设置/应用密钥，原始业务载荷仍可能含业务敏感信息。"
                ),
            }
            with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_STORED) as archive:
                archive.writestr("manifest.json", canonical(manifest))
                for name, body in bodies.items():
                    archive.writestr(name, body)
                if recorded:
                    archive.write(recorded, "recording.zip")
                if source:
                    archive.write(source, "source.arxml")
            # 再读实际写成的附件，关闭哈希计算到 ZIP 写入之间的变更窗口。
            verify_evidence(path)
            logger.info(
                "产品证据包已生成并校验",
                extra={"operation": "scenario.evidence", "run_id": str(identifier)},
            )
            return path
        except BaseException:
            logger.exception("产品证据包生成失败", extra={"operation": "scenario.evidence"})
            if path:
                path.unlink(missing_ok=True)
            raise
        finally:
            if recorded:
                try:
                    recorded.unlink(missing_ok=True)
                except Exception:
                    logger.exception(
                        "原始记录临时包清理失败", extra={"operation": "scenario.evidence.cleanup"}
                    )
                    if path:
                        path.unlink(missing_ok=True)
                    raise
