from autocomp.worker.adapter import FakeKVStudioAdapter
from autocomp.worker.models import ActionKind, ActionRequest, WindowSnapshot
from autocomp.worker.service import KVStudioWorker


def test_inventory_is_read_only_and_returns_snapshots() -> None:
    adapter = FakeKVStudioAdapter((WindowSnapshot("KV STUDIO Ver.11.62", 12),))
    result = KVStudioWorker(adapter).execute(ActionRequest(ActionKind.INVENTORY, apply=True))

    assert result.performed is False
    assert result.windows == adapter.windows
    assert result.audit["mode"] == "dry-run"


def test_expand_is_dry_run_unless_apply_is_explicit() -> None:
    adapter = FakeKVStudioAdapter()
    result = KVStudioWorker(adapter).execute(
        ActionRequest(ActionKind.EXPAND_TREE_ITEM, target_path=("Project", "Programs"))
    )

    assert result.performed is False
    assert adapter.expanded_paths == []


def test_expand_requires_checkpoint_and_path_in_apply_mode() -> None:
    worker = KVStudioWorker(FakeKVStudioAdapter(), apply_enabled=True)

    try:
        worker.execute(
            ActionRequest(ActionKind.EXPAND_TREE_ITEM, apply=True, target_path=("Project",))
        )
    except ValueError as exc:
        assert "checkpoint" in str(exc).lower()
    else:
        raise AssertionError("apply mode without checkpoint must be rejected")


def test_expand_apply_is_audited() -> None:
    adapter = FakeKVStudioAdapter()
    result = KVStudioWorker(adapter, apply_enabled=True).execute(
        ActionRequest(
            ActionKind.EXPAND_TREE_ITEM,
            checkpoint="01_inventory_complete",
            target_path=("Project", "Programs"),
            apply=True,
        )
    )

    assert result.performed is True
    assert adapter.expanded_paths == [("Project", "Programs")]
    assert result.audit["checkpoint"] == "01_inventory_complete"


def test_apply_is_blocked_by_global_worker_switch() -> None:
    worker = KVStudioWorker(FakeKVStudioAdapter())

    try:
        worker.execute(
            ActionRequest(
                ActionKind.EXPAND_TREE_ITEM,
                checkpoint="pilot_01",
                target_path=("Project",),
                apply=True,
            )
        )
    except ValueError as exc:
        assert "disabled" in str(exc)
    else:
        raise AssertionError("worker apply safety switch must fail closed")


def test_status_is_read_only() -> None:
    adapter = FakeKVStudioAdapter()

    result = KVStudioWorker(adapter).execute(ActionRequest(ActionKind.STATUS, apply=True))

    assert result.performed is False
    assert result.window_state == adapter.window_state
    assert adapter.rename_calls == []


def test_tree_rename_requires_exact_locator_path_and_source() -> None:
    adapter = FakeKVStudioAdapter()
    adapter.tree_items[(0, 2)] = ("Programs", "中文")
    worker = KVStudioWorker(adapter, apply_enabled=True)

    result = worker.execute(
        ActionRequest(
            ActionKind.RENAME_TREE_ITEM,
            checkpoint="pilot_01",
            locator=(0, 2),
            expected_path=("Programs", "other"),
            expected_source="other",
            target="English",
            apply=True,
        )
    )

    assert result.performed is False
    assert adapter.tree_items[(0, 2)][-1] == "中文"
    assert "precondition" in result.message


def test_tree_rename_apply_and_adapter_rollback_are_reported() -> None:
    adapter = FakeKVStudioAdapter()
    adapter.tree_items[(0, 2)] = ("Programs", "中文")
    adapter.rename_failure_after_write = True
    worker = KVStudioWorker(adapter, apply_enabled=True)

    result = worker.execute(
        ActionRequest(
            ActionKind.RENAME_TREE_ITEM,
            checkpoint="pilot_01",
            locator=(0, 2),
            expected_path=("Programs", "中文"),
            expected_source="中文",
            target="English",
            apply=True,
        )
    )

    assert result.performed is False
    assert result.rollback_attempted is True
    assert result.rollback_succeeded is True
    assert result.after == "中文"


def test_probe_renames_then_restores_exact_source() -> None:
    adapter = FakeKVStudioAdapter()
    adapter.tree_items[(0, 2)] = ("Programs", "中文")
    worker = KVStudioWorker(adapter, apply_enabled=True)

    result = worker.execute(
        ActionRequest(
            ActionKind.PROBE_TREE_ITEM_RENAME,
            checkpoint="name_limit_probe",
            locator=(0, 2),
            expected_path=("Programs", "中文"),
            expected_source="中文",
            target="English Candidate",
            apply=True,
        )
    )

    assert result.performed is True
    assert result.rollback_attempted is True
    assert result.rollback_succeeded is True
    assert result.after == "中文"
    assert adapter.tree_items[(0, 2)][-1] == "中文"


def test_inventory_expansion_requires_global_apply_and_returns_snapshot() -> None:
    adapter = FakeKVStudioAdapter()
    adapter.project_tree_inventory = adapter.project_tree_inventory.__class__(
        "KV STUDIO", 0, "ProjectTreeView", 3, 1, 1, True
    )
    result = KVStudioWorker(adapter, apply_enabled=True).execute(
        ActionRequest(
            ActionKind.INVENTORY_PROJECT_TREE,
            checkpoint="inventory_01",
            expand_all=True,
            restore_state=True,
            apply=True,
        )
    )

    assert result.performed is True
    assert result.project_tree_inventory is adapter.project_tree_inventory
