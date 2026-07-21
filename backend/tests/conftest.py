"""pytest 公共配置:慢速用例(真 ASR)默认跳过,--runslow 开启(规约 §6)。"""

import pytest


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption("--runslow", action="store_true", default=False, help="运行慢速用例")


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if config.getoption("--runslow"):
        return
    skip_slow = pytest.mark.skip(reason="慢速用例,需 --runslow 开启")
    for item in items:
        if "slow" in item.keywords:
            item.add_marker(skip_slow)
