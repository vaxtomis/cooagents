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
