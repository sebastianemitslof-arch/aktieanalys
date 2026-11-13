"""Gemensamma hjälpmedel för testerna.

Testerna läser de verkliga filerna i config/ och gör kopior som de
förvanskar på olika sätt. Då testas två saker samtidigt: att den
medföljande konfigurationen är giltig, och att varje typ av misstag
faktiskt upptäcks.
"""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import pytest
import yaml

ROT = Path(__file__).resolve().parent.parent
KONFIGKATALOG = ROT / "config"


def _las(namn: str) -> dict[str, Any]:
    return yaml.safe_load((KONFIGKATALOG / namn).read_text(encoding="utf-8"))


@pytest.fixture
def config_dict() -> dict[str, Any]:
    return copy.deepcopy(_las("config.yaml"))


@pytest.fixture
def universum_dict() -> dict[str, Any]:
    return copy.deepcopy(_las("universum.yaml"))


@pytest.fixture
def portfolj_dict() -> dict[str, Any]:
    return copy.deepcopy(_las("portfolj.yaml"))
