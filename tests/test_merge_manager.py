import pytest
from unittest.mock import AsyncMock, patch
from src.database import Database
from src.merge_manager import MergeManager

@pytest.fixture
async def db(tmp_path):
    d = Database(db_path=tmp_path / "test.db", schema_path="db/schema.sql")
    await d.connect()
    # Insert dummy runs for FK
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    await d.execute(
        "INSERT INTO runs(id,ticket,repo_path,status,current_stage,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
        ("r1", "T-1", "/repo", "running", "MERGE_QUEUED", now, now)
    )
    await d.execute(
        "INSERT INTO runs(id,ticket,repo_path,status,current_stage,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
        ("r2", "T-2", "/repo", "running", "MERGE_QUEUED", now, now)
    )
    yield d
    await d.close()

@pytest.fixture
async def mm(db):
    webhook = AsyncMock()
    webhook.notify = AsyncMock()
    return MergeManager(db, webhook)

async def test_enqueue(mm):
    await mm.enqueue("r1", "feat/T-1-dev")
    items = await mm.list_queue()
    assert len(items) == 1
    assert items[0]["status"] == "waiting"

async def test_queue_order_fifo(mm):
    await mm.enqueue("r1", "feat/T-1-dev")
    await mm.enqueue("r2", "feat/T-2-dev")
    items = await mm.list_queue()
    assert items[0]["run_id"] == "r1"

async def test_queue_order_priority(mm):
    await mm.enqueue("r1", "feat/T-1-dev", priority=0)
    await mm.enqueue("r2", "feat/T-2-dev", priority=10)
    items = await mm.list_queue()
    # Higher priority first
    assert items[0]["run_id"] == "r2"

async def test_get_status(mm):
    await mm.enqueue("r1", "feat/T-1-dev")
    status = await mm.get_status("r1")
    assert status == "waiting"

async def test_skip_item(mm):
    await mm.enqueue("r1", "feat/T-1-dev")
    await mm.skip("r1")
    status = await mm.get_status("r1")
    assert status == "skipped"

async def test_process_next_no_items(mm):
    result = await mm.process_next()
    assert result is None

async def test_only_one_merging(mm, db):
    await mm.enqueue("r1", "feat/T-1-dev")
    await mm.enqueue("r2", "feat/T-2-dev")
    # Simulate r1 in merging state
    await db.execute("UPDATE merge_queue SET status='merging' WHERE run_id=?", ("r1",))
    result = await mm.process_next()
    assert result is None  # Can't start another while one is merging


async def test_process_next_crash_resets_to_conflict(mm, db):
    """If _execute_merge crashes, queue item must not stay stuck at 'merging'."""
    await mm.enqueue("r1", "feat/T-1-dev")

    async def _boom(item):
        raise RuntimeError("git exploded")

    mm._execute_merge = _boom

    with pytest.raises(RuntimeError):
        await mm.process_next()

    status = await mm.get_status("r1")
    assert status == "conflict"  # not stuck at 'merging'

    # Queue is unblocked — r2 can be picked up
    await mm.enqueue("r2", "feat/T-2-dev")
    waiting = await db.fetchone("SELECT * FROM merge_queue WHERE run_id='r2' AND status='waiting'")
    assert waiting is not None
