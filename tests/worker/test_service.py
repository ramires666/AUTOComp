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
    worker = KVStudioWorker(FakeKVStudioAdapter())

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
    result = KVStudioWorker(adapter).execute(
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
