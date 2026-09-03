"""配置层测试：真实 config/*.yaml（离线）。"""

from __future__ import annotations

import pytest

from meridian.config import AppConfig, ConfigError, DataSourceConfig, MarketsConfig

from conftest import ROOT


def test_app_config_loads_real_yaml():
    app = AppConfig.load(ROOT)
    assert app.name == "Meridian"
    assert app.data_dir.name == "data"
    assert app.report_dir.name == "reports"


def test_markets_config_first_batch():
    cfg = MarketsConfig.load(ROOT)
    assert len(cfg.markets) >= 1
    cn = cfg.markets[0]
    assert cn.market == "cn"
    assert cn.asset_type == "stock"
    symbols = {s.symbol for s in cn.symbols}
    assert {"600519", "300750", "600547"} <= symbols


def test_find_known_and_unknown_symbol():
    cfg = MarketsConfig.load(ROOT)
    entry = cfg.find("600519")
    assert entry.scoring_config() == "stock.yaml"
    with pytest.raises(ConfigError):
        cfg.find("999999")


def test_data_sources_retry_defaults():
    src = DataSourceConfig.load(ROOT)
    assert src.default == "akshare"
    retry = src.retry_for("akshare")
    assert retry.max_attempts == 3
    assert retry.backoff_seconds == 2.0
