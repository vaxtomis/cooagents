import pytest
from unittest.mock import AsyncMock
from src.scheduler import Scheduler

@pytest.fixture
def sched():
    db = AsyncMock()
    hm = AsyncMock()
    jm = AsyncMock()
    ae = AsyncMock()
    wh = AsyncMock()
    wh.notify = AsyncMock()

    class FakeConfig:
        class health_check:
            interval = 60
            ssh_timeout = 5
        class timeouts:
            dispatch_startup = 300
            design_execution = 1800
            dev_execution = 3600
            review_reminder = 86400

    return Scheduler(db, hm, jm, ae, wh, FakeConfig())

async def test_start_stop(sched):
    await sched.start()
    assert len(sched._tasks) == 3
    await sched.stop()
    assert len(sched._tasks) == 0


async def test_tick_runnable_runs_includes_dispatched_and_running_stages():
    db = AsyncMock()
    hm = AsyncMock()
    jm = AsyncMock()
    ae = AsyncMock()
    wh = AsyncMock()
    wh.notify = AsyncMock()
    sm = AsyncMock()
    sm.tick = AsyncMock()

    class FakeConfig:
        class health_check:
            interval = 60
            ssh_timeout = 5
        class timeouts:
            dispatch_startup = 300
            design_execution = 1800
            dev_execution = 3600
            review_reminder = 86400

    db.fetchall = AsyncMock(return_value=[
        {"id": "run-design-queued"},
        {"id": "run-design-dispatched"},
        {"id": "run-design-running"},
        {"id": "run-dev-queued"},
        {"id": "run-dev-dispatched"},
        {"id": "run-dev-running"},
    ])

    sched = Scheduler(db, hm, jm, ae, wh, FakeConfig(), state_machine=sm)
    await sched._tick_runnable_runs()

    fetched_sql = db.fetchall.await_args.args[0]
    assert "DESIGN_DISPATCHED" in fetched_sql
    assert "DESIGN_RUNNING" in fetched_sql
    assert "DEV_DISPATCHED" in fetched_sql
    assert "DEV_RUNNING" in fetched_sql
    assert sm.tick.await_count == 6
