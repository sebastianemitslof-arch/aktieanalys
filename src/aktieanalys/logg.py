"""Loggning: samma meddelanden till skärmen och till loggfilen.

yfinance är en inofficiell källa som ibland svarar tomt eller långsamt.
Allt sådant hamnar i loggfilen (data/korning.log) så att du i efterhand
kan se varför ett bolag saknade data en viss vecka.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

_UPPSATT = False


def satt_upp(niva: str = "INFO", fil: Path | None = None) -> logging.Logger:
    """Kopplar upp loggningen. Säker att anropa flera gånger."""
    global _UPPSATT
    rot = logging.getLogger("aktieanalys")
    if _UPPSATT:
        return rot

    rot.setLevel(logging.DEBUG)
    rot.handlers.clear()

    skarm = logging.StreamHandler(sys.stderr)
    skarm.setLevel(getattr(logging, niva))
    skarm.setFormatter(logging.Formatter("%(levelname)-7s %(message)s"))
    rot.addHandler(skarm)

    if fil is not None:
        fil.parent.mkdir(parents=True, exist_ok=True)
        till_fil = logging.FileHandler(fil, encoding="utf-8")
        till_fil.setLevel(logging.DEBUG)
        till_fil.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s")
        )
        rot.addHandler(till_fil)

    _UPPSATT = True
    return rot


def hamta(namn: str) -> logging.Logger:
    """Logger för en modul, t.ex. hamta(__name__)."""
    return logging.getLogger(f"aktieanalys.{namn.removeprefix('aktieanalys.')}")
