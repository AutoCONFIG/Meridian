"""pytest 共享 fixture：离线合成数据 + 哑模型。

测试不依赖网络（PLAN.md Step 7：tests 用本地 CSV）。
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from meridian.data.base import DataSource, FetchRequest

ROOT = Path(__file__).resolve().parents[1]


def make_uptrend_frame(n: int = 130, start_price: float = 100.0) -> pd.DataFrame:
    """合成"量价齐升"日K序列（加速上涨，指标形态健康）。"""
    rows = []
    prev_close = start_price - 1.0
    for i in range(n):
        close = start_price + 0.5 * i * i / 10.0 + 0.8 * i  # 二次 + 线性混合
        rows.append(
            {
                "date": pd.Timestamp("2026-01-05").date() + pd.Timedelta(days=i),
                "open": prev_close,
                "high": close + 0.5,
                "low": prev_close - 0.5,
                "close": close,
                "volume": 1000.0 + i,
                "amount": (1000.0 + i) * close,
            }
        )
        prev_close = close
    return pd.DataFrame(rows)[["date", "open", "high", "low", "close", "volume", "amount"]]


class CsvSource(DataSource):
    """从本地 CSV 读取（模拟数据文件路径，测试用）。"""

    name = "csv"

    def __init__(self, df: pd.DataFrame, csv_path):
        self.df = df
        self.csv_path = csv_path
        # 落一份 CSV 再读回：验证"本地 CSV → 统一 schema"链路
        self.df.to_csv(csv_path, index=False)
        self._frame = pd.read_csv(csv_path, parse_dates=["date"])
        self._frame["date"] = self._frame["date"].dt.date

    def fetch_daily(self, request: FetchRequest) -> pd.DataFrame:
        mask = (self._frame["date"] >= pd.Timestamp(request.start).date()) & (
            self._frame["date"] <= pd.Timestamp(request.end).date()
        )
        return self._frame.loc[mask].reset_index(drop=True)


class DummyModel:
    """Python 哑模型：固定输出 score=55（验收标准 4：桥接闭环）。"""

    def analyze(self, payload: dict) -> dict:
        # payload 应包含资产与指标视图（协议见 crates/pybind/src/py_model.rs）
        assert "asset" in payload and "indicators" in payload
        return {"score": 55.0, "direction": "neutral", "confidence": 0.8}


@pytest.fixture()
def uptrend_csv(tmp_path: Path) -> Path:
    csv_path = tmp_path / "600519_uptrend.csv"
    make_uptrend_frame().to_csv(csv_path, index=False)
    return csv_path


@pytest.fixture()
def dummy_model() -> DummyModel:
    return DummyModel()
