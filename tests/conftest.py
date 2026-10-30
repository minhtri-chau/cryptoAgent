from dataclasses import replace

import pytest

from cryptoagent import db
from cryptoagent.config import load_settings

DAY = 86_400_000
T0 = 1_700_000_000_000 // DAY * DAY  # 2023-11-14T00:00:00Z


@pytest.fixture
def conn(tmp_path):
    c = db.connect(tmp_path / "test.db")
    db.migrate(c)
    yield c
    c.close()


@pytest.fixture
def settings(tmp_path):
    return replace(load_settings(env_file=tmp_path / "none.env", environ={}), db_path=tmp_path / "test.db")


def candle(ts, close=100.0, volume=1.0):
    return [ts, close, close * 1.01, close * 0.99, close, volume]
