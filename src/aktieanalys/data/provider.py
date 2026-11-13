"""Provider-gränssnittet -- det utbytbara datalagret.

En provider har ett enda jobb: ge tillbaka ett Bolagsdata för en ticker.
Vill du senare byta från yfinance till Börsdata eller ett Google
Sheets-flöde skriver du en ny klass som ärver Provider och fyller samma
fält. Poängmodellen rörs inte.

Tre regler som varje provider måste följa:

1. Höj aldrig ett undantag utåt. Problem skrivs i bolagsdata.fel och
   bolagsdata.saknade. En trasig datakälla får stoppa ett bolag, aldrig
   hela veckokörningen.
2. Gissa aldrig. Saknas ett värde markeras det som saknat med orsak.
3. Ange källa för varje fält som sätts, så att rapporten kan visa
   varifrån ett tal kommer.
"""

from __future__ import annotations

import datetime as dt
from abc import ABC, abstractmethod
from typing import Any

from aktieanalys.data.bolagsdata import Bolagsdata


class Provider(ABC):
    """Basklass för alla datakällor."""

    namn: str = "okänd"

    @abstractmethod
    def hamta(self, ticker: str, idag: dt.date | None = None) -> Bolagsdata:
        """Hämtar allt som går för en ticker. Höjer aldrig undantag."""

    def hamta_manga(
        self, tickers: list[str], idag: dt.date | None = None
    ) -> dict[str, Bolagsdata]:
        """Hämtar flera bolag. Ett bolag som fallerar stoppar inte de andra."""
        return {t: self.hamta(t, idag) for t in tickers}


class Minnesprovider(Provider):
    """Provider som svarar ur en dict i minnet.

    Används av testerna och av backtestmodulen, där data ska komma från
    databasen i stället för från nätet. Finns i produktionskoden med
    flit -- den gör att hela kedjan kan köras utan nätverk.
    """

    namn = "minne"

    def __init__(self, data: dict[str, dict[str, Any]] | None = None) -> None:
        self.data = data or {}

    def satt_bolag(self, ticker: str, varden: dict[str, Any]) -> None:
        self.data[ticker] = varden

    def hamta(self, ticker: str, idag: dt.date | None = None) -> Bolagsdata:
        idag = idag or dt.date.today()
        bd = Bolagsdata(ticker=ticker, hamtad=idag, namn=ticker)
        varden = self.data.get(ticker)
        if varden is None:
            bd.anteckna_fel(f"{ticker} finns inte i minnesprovidern")
            return bd
        for falt, varde in varden.items():
            if falt in ("namn", "valuta"):
                setattr(bd, falt, varde)
                continue
            bd.satt(falt, varde, self.namn)
        return bd
