"""Command-line entry points for read-only inventory, translation, and verification."""

from __future__ import annotations

import argparse
import json
import platform
import sys
from dataclasses import asdict, is_dataclass, replace
from enum import Enum
from importlib.util import find_spec
from pathlib import Path
from typing import Any

from . import __version__
from .config import ConfigError, load_config
from .extraction import extract_mnemonic_inventory
from .translation.client import OpenAICompatibleConfig, OpenAICompatibleProvider
from .translation.inventory import with_assessed_risk
from .translation.manifest import TranslationManifest
from .translation.memory import Glossary, TranslationMemory
from .translation.models import InventoryRecord, RiskLevel, TextKind
from .translation.service import TranslationService
from .verification import (
    build_hash_manifest,
    compare_mnemonic_exports,
    decode_text_export,
    scan_remaining_cjk,
)
from .worker.adapter import PywinautoKVStudioAdapter
from .worker.http import WorkerHttpServer
from .worker.models import ActionKind, ActionRequest
from .worker.service import KVStudioWorker


class CliError(RuntimeError):
    """User-facing command error without a traceback."""


def _json_default(value: object) -> object:
    if is_dataclass(value) and not isinstance(value, type):
        return asdict(value)
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Path):
        return str(value)
    raise TypeError(f"cannot serialize {type(value).__name__}")


def _json_text(payload: object) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2, default=_json_default) + "\n"


def _emit(payload: object, output: str | None) -> None:
    rendered = _json_text(payload)
    if output is None:
        sys.stdout.write(rendered)
        return
    path = Path(output)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("x", encoding="utf-8", newline="\n") as stream:
            stream.write(rendered)
    except FileExistsError as exc:
        raise CliError(f"refusing to overwrite existing output: {path}") from exc


def _read_json(path: str | Path) -> Any:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CliError(f"cannot read JSON from {path}: {exc}") from exc


def _load_inventory(path: str | Path) -> list[InventoryRecord]:
    payload = _read_json(path)
    if not isinstance(payload, list):
        raise CliError("inventory root must be a JSON array")
    records: list[InventoryRecord] = []
    seen_ids: set[str] = set()
    risk_order = {RiskLevel.LOW: 0, RiskLevel.MEDIUM: 1, RiskLevel.HIGH: 2}
    for index, item in enumerate(payload):
        if not isinstance(item, dict):
            raise CliError(f"inventory item {index} must be an object")
        record_id = item.get("record_id")
        source_text = item.get("source_text")
        kind = item.get("kind")
        hierarchy = item.get("hierarchy", [])
        if not all(isinstance(value, str) for value in (record_id, source_text, kind)):
            raise CliError(f"inventory item {index} requires string record_id/source_text/kind")
        if not isinstance(hierarchy, list) or not all(
            isinstance(value, str) for value in hierarchy
        ):
            raise CliError(f"inventory item {index} hierarchy must be an array of strings")
        try:
            record = InventoryRecord(
                record_id=record_id,
                source_text=source_text,
                kind=TextKind(kind),
                hierarchy=tuple(hierarchy),
                context=str(item.get("context", "")),
                location=str(item.get("location", "")),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise CliError(f"invalid inventory item {index}: {exc}") from exc
        if not record.record_id.strip() or record.record_id in seen_ids:
            raise CliError(f"inventory item {index} has a blank or duplicate record_id")
        seen_ids.add(record.record_id)
        assessed = with_assessed_risk(record)
        try:
            supplied_risk = RiskLevel(str(item.get("risk", RiskLevel.LOW.value)))
        except ValueError as exc:
            raise CliError(f"inventory item {index} has invalid risk") from exc
        risk = max((assessed.risk, supplied_risk), key=risk_order.__getitem__)
        records.append(
            replace(
                assessed,
                risk=risk,
                requires_review=assessed.requires_review
                or bool(item.get("requires_review", False)),
            )
        )
    return records


def _load_string_map(path: str | None, label: str) -> dict[str, str]:
    if path is None:
        return {}
    payload = _read_json(path)
    if not isinstance(payload, dict) or not all(
        isinstance(key, str) and isinstance(value, str) for key, value in payload.items()
    ):
        raise CliError(f"{label} must be a JSON object containing string pairs")
    return payload


def _require_export_text(path: str | Path) -> str:
    decoded = decode_text_export(path)
    if decoded is None:
        raise CliError(f"file does not look like a supported text export: {path}")
    return decoded


def _cmd_doctor(args: argparse.Namespace) -> int:
    config = load_config(args.config, args.env_file)
    report: dict[str, object] = {
        "autocomp_version": __version__,
        "python": platform.python_version(),
        "platform": platform.platform(),
        "dry_run": not config.safety.apply_enabled,
        "online_operations_forbidden": config.safety.forbid_online_operations,
        "expected_kv_studio_version": config.kv_studio.expected_version,
        "pywinauto_installed": find_spec("pywinauto") is not None,
        "llm_endpoint": config.llm.endpoint,
        "llm_model": config.llm.model,
        "llm_api_key_configured": config.llm.api_key is not None,
        "worker_token_configured": config.worker_token is not None,
    }
    if args.probe_ui:
        adapter = PywinautoKVStudioAdapter(config.kv_studio.window_title_pattern)
        windows = adapter.discover()
        report["kv_studio_windows"] = windows
        report["kv_studio_window_count"] = len(windows)
    _emit(report, args.output)
    return 0


def _cmd_inventory_ui(args: argparse.Namespace) -> int:
    config = load_config(args.config, args.env_file)
    adapter = PywinautoKVStudioAdapter(config.kv_studio.window_title_pattern)
    result = KVStudioWorker(adapter).execute(ActionRequest(ActionKind.INVENTORY))
    _emit(result, args.output)
    return 0 if result.windows else 1


def _cmd_scan_cjk(args: argparse.Namespace) -> int:
    report = scan_remaining_cjk(args.directory)
    _emit(report, args.output)
    return 1 if args.fail_on_cjk and report.has_cjk else 0


def _cmd_hash_project(args: argparse.Namespace) -> int:
    manifest = build_hash_manifest(args.directory)
    _emit(manifest.as_dict(), args.output)
    return 0


def _cmd_compare_mnemonic(args: argparse.Namespace) -> int:
    comparison = compare_mnemonic_exports(
        _require_export_text(args.baseline),
        _require_export_text(args.candidate),
        semicolon_comments=args.semicolon_comments,
    )
    _emit(comparison, args.output)
    return 0 if comparison.identical else 1


def _cmd_extract_mnemonic(args: argparse.Namespace) -> int:
    path = Path(args.export)
    records = extract_mnemonic_inventory(
        _require_export_text(path),
        source_name=args.source_name or path.name,
    )
    _emit([record.to_dict() for record in records], args.output)
    return 0


def _cmd_translate(args: argparse.Namespace) -> int:
    config = load_config(args.config, args.env_file)
    glossary = Glossary(_load_string_map(args.glossary, "glossary"))
    memory = TranslationMemory(_load_string_map(args.memory, "translation memory"))
    provider = OpenAICompatibleProvider(
        OpenAICompatibleConfig(
            base_url=config.llm.endpoint,
            model=config.llm.model,
            api_key=config.llm.api_key or "",
            timeout_seconds=config.llm.timeout_seconds,
            temperature=0.0,
        )
    )
    service = TranslationService(
        provider,
        glossary=glossary,
        memory=memory,
        batch_size=config.safety.batch_size,
    )
    manifest = TranslationManifest(checkpoint=args.checkpoint, dry_run=True)
    for decision in service.propose_batch(_load_inventory(args.inventory)):
        manifest.add(decision)
    _emit(manifest.to_dict(), args.output)
    if args.memory_output:
        _emit(memory.entries, args.memory_output)
    return 0


def _cmd_worker_serve(args: argparse.Namespace) -> int:
    config = load_config(args.config, args.env_file)
    adapter = PywinautoKVStudioAdapter(config.kv_studio.window_title_pattern)
    server = WorkerHttpServer(
        KVStudioWorker(adapter),
        token=config.worker_token,
        host="127.0.0.1",
        port=args.port,
    )
    address, port = server.server_address
    sys.stdout.write(f"AUTOComp read-only worker listening on http://{address}:{port}\n")
    sys.stdout.flush()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="autocomp",
        description="Safe, local-first KV STUDIO translation automation",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    subparsers = parser.add_subparsers(dest="command", required=True)

    doctor = subparsers.add_parser("doctor", help="validate configuration and dependencies")
    doctor.add_argument("--config")
    doctor.add_argument("--env-file")
    doctor.add_argument("--probe-ui", action="store_true")
    doctor.add_argument("--output")
    doctor.set_defaults(handler=_cmd_doctor)

    inventory = subparsers.add_parser("inventory-ui", help="read the KV STUDIO UIA tree")
    inventory.add_argument("--config")
    inventory.add_argument("--env-file")
    inventory.add_argument("--output")
    inventory.set_defaults(handler=_cmd_inventory_ui)

    scan = subparsers.add_parser("scan-cjk", help="scan safe text exports for CJK text")
    scan.add_argument("directory")
    scan.add_argument("--output")
    scan.add_argument("--fail-on-cjk", action="store_true")
    scan.set_defaults(handler=_cmd_scan_cjk)

    project_hash = subparsers.add_parser("hash-project", help="hash a project copy")
    project_hash.add_argument("directory")
    project_hash.add_argument("--output")
    project_hash.set_defaults(handler=_cmd_hash_project)

    mnemonic = subparsers.add_parser(
        "compare-mnemonic", help="verify normalized ladder logic is unchanged"
    )
    mnemonic.add_argument("baseline")
    mnemonic.add_argument("candidate")
    mnemonic.add_argument("--output")
    mnemonic.add_argument(
        "--semicolon-comments",
        action="store_true",
        help="ignore semicolon suffixes only after confirming the export format",
    )
    mnemonic.set_defaults(handler=_cmd_compare_mnemonic)

    extraction = subparsers.add_parser(
        "extract-mnemonic", help="extract translatable text from a mnemonic export"
    )
    extraction.add_argument("export")
    extraction.add_argument("--source-name")
    extraction.add_argument("--output", required=True)
    extraction.set_defaults(handler=_cmd_extract_mnemonic)

    translate = subparsers.add_parser(
        "translate", help="create a dry-run translation manifest from JSON inventory"
    )
    translate.add_argument("inventory")
    translate.add_argument("--config")
    translate.add_argument("--env-file")
    translate.add_argument("--glossary")
    translate.add_argument("--memory")
    translate.add_argument("--memory-output")
    translate.add_argument("--checkpoint", required=True)
    translate.add_argument("--output", required=True)
    translate.set_defaults(handler=_cmd_translate)

    worker_serve = subparsers.add_parser(
        "worker-serve", help="serve authenticated read-only UI inventory on loopback"
    )
    worker_serve.add_argument("--config")
    worker_serve.add_argument("--env-file")
    worker_serve.add_argument("--port", type=int, default=8765)
    worker_serve.set_defaults(handler=_cmd_worker_serve)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    try:
        return int(args.handler(args))
    except (CliError, ConfigError, RuntimeError, ValueError) as exc:
        parser.exit(2, f"error: {exc}\n")
    return 2
