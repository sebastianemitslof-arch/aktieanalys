"""Valutaomräkning till kronor.

Yahoo är inkonsekvent med valutor för bolag som handlas i Sverige men
redovisar i en annan valuta. Uppmätt på AstraZeneca (SEK/USD) och
Nordea (SEK/EUR):

  kurs, börsvärde, bolagsvärde     -> handelsvalutan (kronor)
  trailingEps                      -> handelsvalutan (kronor)
  bookValue                        -> RAPPORTVALUTAN
  allt ur resultat- och balansräkning -> RAPPORTVALUTAN

Följden är att Yahoos eget priceToBook är fel för dessa bolag:
AstraZeneca visas som P/B 56 i stället för ungefär 5,6, Nordea som 20,6
i stället för ungefär 1,9. Det är därför den här modulen finns -- utan
den hade Nordea alltid hamnat sist i bankkategorin, och felet hade inte
syns någonstans.

Räkenskapssiffror räknas om med valutakursen vid respektive bokslut, så
att historiken blir internt konsekvent. Går kursen inte att hämta
markeras fälten som saknade i stället för att användas oomräknade.
"""

from __future__ import annotations

import datetime as dt
import logging
import time

import pandas as pd

from aktieanalys.logg import hamta

logg = hamta(__name__)
logging.getLogger("yfinance").setLevel(logging.CRITICAL)


def naiva_datum(serie: pd.Series) -> pd.Series:
    """Tar bort tidszon och klockslag ur en tidsseries index.

    Yahoo märker olika serier med olika tidszoner: svenska aktier med
    Europe/Stockholm, valutapar med Europe/London. Pandas vägrar jämföra
    ett tidszonsmärkt index med ett omärkt datum och höjer TypeError --
    vilket tog ner en hel veckokörning innan det här fanns. Allt
    normaliseras därför till datum utan tidszon, vilket också är det
    databasen lagrar.
    """
    if serie is None or len(serie) == 0:
        return serie
    index = pd.DatetimeIndex(serie.index)
    if index.tz is not None:
        # tz_localize(None) behåller den lokala väggklockan. Använder man
        # tz_convert(None) räknas tiden om till UTC, och en svensk
        # midnattsstämpel blir då föregående dag -- alla datum ett dygn fel.
        index = index.tz_localize(None)
    serie = serie.copy()
    serie.index = index.normalize()
    return serie


class Valutaomraknare:
    """Hämtar och cachar valutakurser mot kronan.

    Kurserna sparas i samma kurstabell som aktiekurserna, under namn som
    "USDSEK=X", så att de inte behöver hämtas om varje vecka.

    Ange fasta_kurser för att köra helt utan nät, t.ex. i tester eller
    i backtestet: {"USD": 10.0, "EUR": 11.2}.
    """

    def __init__(
        self,
        lager=None,
        max_forsok: int = 3,
        paus_sek: float = 2.0,
        ar_historik: int = 5,
        fasta_kurser: dict[str, float] | None = None,
    ) -> None:
        self.lager = lager
        self.max_forsok = max_forsok
        self.paus_sek = paus_sek
        self.ar_historik = ar_historik
        self.fasta_kurser = fasta_kurser
        self._serier: dict[str, pd.Series] = {}
        self.fel: list[str] = []

    # =================================================================
    def _serie(self, valuta: str) -> pd.Series | None:
        """Kursserie för valutan mot kronan, nyast sist."""
        if valuta in self._serier:
            return self._serier[valuta]

        namn = f"{valuta}SEK=X"

        if self.lager is not None:
            sparad = self.lager.las_kurser(namn)
            if len(sparad) > 50:
                logg.debug("%s: %d kurser ur databasen", namn, len(sparad))
                self._serier[valuta] = sparad
                return sparad

        serie = self._hamta_fran_yahoo(namn)
        if serie is None:
            return None
        if self.lager is not None:
            self.lager.spara_kurser(namn, serie)
        self._serier[valuta] = serie
        return serie

    def _hamta_fran_yahoo(self, namn: str) -> pd.Series | None:
        try:
            import yfinance as yf
        except ImportError:
            self.fel.append("yfinance saknas -- kan inte hämta valutakurser")
            return None

        for n in range(1, self.max_forsok + 1):
            try:
                df = yf.Ticker(namn).history(
                    period=f"{self.ar_historik + 2}y", auto_adjust=False
                )
            except Exception as e:
                logg.debug("%s försök %d/%d: %s", namn, n, self.max_forsok, e)
                if n == self.max_forsok:
                    self.fel.append(f"{namn}: {type(e).__name__}: {e}")
                    return None
                time.sleep(self.paus_sek)
                continue

            if df is None or df.empty or "Close" not in df:
                if n == self.max_forsok:
                    self.fel.append(f"{namn}: tomt svar från Yahoo")
                    return None
                time.sleep(self.paus_sek)
                continue

            serie = naiva_datum(df["Close"].dropna())
            logg.info("hämtade %d valutakurser för %s", len(serie), namn)
            return serie
        return None

    # =================================================================
    def kurs(self, valuta: str, ar: int | None = None) -> float | None:
        """Valutakursen mot kronan vid slutet av ett år.

        ar=None ger den senaste kända kursen. Finns ingen kurs för året
        används den senaste kursen före årsskiftet.
        """
        if not valuta or valuta == "SEK":
            return 1.0

        if self.fasta_kurser is not None:
            return self.fasta_kurser.get(valuta)

        serie = self._serie(valuta)
        if serie is None or serie.empty:
            return None
        serie = naiva_datum(serie)

        if ar is None:
            return float(serie.iloc[-1])

        fram_till = serie[serie.index <= pd.Timestamp(f"{ar}-12-31")]
        if fram_till.empty:
            # Året ligger före vår kurshistorik -- bättre att säga nej
            # än att använda en kurs från en annan tid.
            return None
        return float(fram_till.iloc[-1])

    def till_sek(
        self, belopp: float | None, valuta: str, ar: int | None = None
    ) -> float | None:
        """Räknar om ett belopp till kronor. None om kursen saknas."""
        if belopp is None:
            return None
        k = self.kurs(valuta, ar)
        return None if k is None else belopp * k

    def serie_till_sek(
        self, serie: list[tuple[int, float]] | None, valuta: str
    ) -> list[tuple[int, float]] | None:
        """Räknar om en årsserie, varje år med sin egen valutakurs.

        Returnerar None om inget år kunde räknas om. År som saknar kurs
        utesluts ur serien -- de får inte bli oomräknade tal.
        """
        if serie is None:
            return None
        if not valuta or valuta == "SEK":
            return serie
        ut = []
        for ar, varde in serie:
            k = self.kurs(valuta, ar)
            if k is not None:
                ut.append((ar, varde * k))
        return ut or None
