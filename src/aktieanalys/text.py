"""Svenska datumnamn.

Pythons strftime följer systemets språkinställning, som i en
GitHub Actions-container är engelsk. Rapporten ska vara svensk oavsett
var den körs, så månadsnamnen står här i stället.
"""

from __future__ import annotations

import datetime as dt

MANADER = (
    "januari", "februari", "mars", "april", "maj", "juni",
    "juli", "augusti", "september", "oktober", "november", "december",
)
KORTA = ("jan", "feb", "mar", "apr", "maj", "jun",
         "jul", "aug", "sep", "okt", "nov", "dec")


def datum_text(datum: dt.date) -> str:
    """4 maj 2026"""
    return f"{datum.day} {MANADER[datum.month - 1]} {datum.year}"


def kort_datum(datum: dt.date) -> str:
    """4 maj"""
    return f"{datum.day} {KORTA[datum.month - 1]}"


def manad_dag(mmdd: str) -> str:
    """"05-15" blir "15 maj"."""
    manad, dag = int(mmdd[:2]), int(mmdd[3:])
    return f"{dag} {MANADER[manad - 1]}"
