"""Hittar bolag automatiskt i stället för att de ska skrivas in för hand.

Den ursprungliga bolagslistan skrevs ur minnet, vilket betyder att ett
bolag ingen tänkte på aldrig kunde väljas. Yahoos screener kan däremot
räkna upp börsen: med gränserna i config.yaml ger den ungefär 175
svenska bolag mot handlistans 70.

Modulen FÖRESLÅR, den ändrar ingenting. Kategoriindelningen är en
bedömning -- Yahoos "Financial Services" rymmer både storbanker,
försäkringsbolag och investmentbolag, som ska bedömas på helt olika
mått. Förslaget måste läsas igenom innan det används.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

from aktieanalys.konfig import Installningar
from aktieanalys.logg import hamta

logg = hamta(__name__)
logging.getLogger("yfinance").setLevel(logging.CRITICAL)

#: Yahoos sektorer översatta till våra kategorier. Flera sektorer kan
#: peka på samma kategori; de som är tveksamma flaggas i stället.
SEKTORKARTA = {
    "Industrials": "Industri",
    "Basic Materials": "Industri",
    "Energy": "Industri",
    "Utilities": "Industri",
    "Healthcare": "Hälsovård",
    "Consumer Cyclical": "Konsument",
    "Consumer Defensive": "Konsument",
    "Technology": "Teknik",
    "Communication Services": "Teknik",
    "Real Estate": "Fastighet",
}

#: Svenska investmentbolag, med ticker. Listan underhålls för hand, och
#: det är med flit.
#:
#: Yahoos sektorindelning fångar dem inte. Investor och Industrivärden
#: hamnar under "Financial Services", men Lundbergföretagen, Latour och
#: Ratos klassas som "Industrials" -- och skulle därmed glida in i
#: Industri med standardmåtten utan att någon flagga gick upp.
#:
#: Det vore ett systematiskt fel, inte ett brus. Latours bolagsvärde
#: rymmer hela börsportföljen medan rörelseresultatet bara kommer från
#: industrirörelsen, så EV/EBIT blir groteskt högt och bolaget rankas
#: alltid sist. Samma mekanism som gjorde att substansvärde aldrig
#: härleds för investmentbolag (se data/harledda.py).
#:
#: Det finns ingen datakälla som gör den här indelningen åt oss. Saknas
#: ett bolag i listan: lägg till det.
KANDA_INVESTMENTBOLAG = {
    "INVE-A.ST", "INVE-B.ST",      # Investor
    "INDU-A.ST", "INDU-C.ST",      # Industrivärden
    "LATO-B.ST",                   # Latour
    "LUND-B.ST",                   # Lundbergföretagen
    "KINV-B.ST",                   # Kinnevik
    "SVOL-B.ST",                   # Svolder
    "BURE.ST",                     # Bure Equity
    "RATO-B.ST",                   # Ratos
    "CRED-A.ST",                   # Creades
    "TRAC-B.ST",                   # Traction
    "ORES.ST",                     # Investment AB Öresund
    "VEFAB.ST", "VNV.ST",          # VEF, VNV Global
    "HAV-B.ST",                    # Havsfrun
}

INVESTMENTBOLAGSVARNING = (
    "är ett investmentbolag. Yahoos sektor fångar inte det. Varken "
    "standarduppsättningen eller bankuppsättningen passar: bolagsvärdet "
    "rymmer hela portföljen medan rörelseresultatet bara kommer från "
    "rörelsen, så EV/EBIT och P/B blir systematiskt fel. Kategorin "
    "investmentbolag är avstängd i universum.yaml"
)

#: Sektorer där Yahoos indelning är för grov för våra måttuppsättningar.
KRAVER_BEDOMNING = {
    "Financial Services": (
        "Bank/finans",
        "rymmer storbanker, nischbanker, försäkringsbolag, "
        "investmentbolag och kapitalförvaltare. Bara banker ska ha "
        "bankuppsättningen (ROE, P/B, kapitaltäckning) -- kontrollera "
        "varje bolag",
    ),
}


def aktieslagsstam(ticker: str) -> str:
    """Bolagets "stam" utan aktieslag.

    ATCO-A.ST och ATCO-B.ST är samma bolag i två aktieslag. Har båda
    kommit med i universumet konkurrerar bolaget med sig självt, och
    percentilrankningen blir snedvriden.
    """
    import re

    stam = ticker.removesuffix(".ST")
    return re.sub(r"-(A|B|C|D|SDB|SDB-A|SDB-B)$", "", stam)


@dataclass
class Kandidat:
    ticker: str
    namn: str
    borsvarde: float
    daglig_omsattning: float
    sektor: str | None = None
    kategori: str | None = None
    anmarkning: str | None = None
    redan_med: bool = False
    aktieslagskrock: str | None = None
    """Annan ticker för samma bolag, om en sådan finns."""

    @property
    def borsvarde_mdr(self) -> float:
        return self.borsvarde / 1e9

    @property
    def omsattning_mkr(self) -> float:
        return self.daglig_omsattning / 1e6


@dataclass
class Sokresultat:
    traffar: int = 0
    kandidater: list[Kandidat] = field(default_factory=list)
    fel: str | None = None

    @property
    def nya(self) -> list[Kandidat]:
        return [k for k in self.kandidater if not k.redan_med]

    @property
    def befintliga(self) -> list[Kandidat]:
        return [k for k in self.kandidater if k.redan_med]

    def per_kategori(self) -> dict[str, list[Kandidat]]:
        ut: dict[str, list[Kandidat]] = {}
        for k in sorted(self.nya, key=lambda x: -x.borsvarde):
            ut.setdefault(k.kategori or "Okänd kategori", []).append(k)
        return ut


def sok_bolag(
    inst: Installningar,
    hamta_sektor: bool = True,
    paus_sek: float = 0.4,
    max_antal: int = 500,
) -> Sokresultat:
    """Söker upp svenska bolag som klarar gränserna i config.yaml."""
    import yfinance as yf

    c = inst.config.universum
    resultat = Sokresultat()

    try:
        fraga = yf.EquityQuery("and", [
            yf.EquityQuery("eq", ["region", "se"]),
            yf.EquityQuery("gt", ["intradaymarketcap", c.min_borsvarde_mdr * 1e9]),
        ])
        rader = []
        for start in range(0, max_antal, 50):
            svar = yf.screen(fraga, size=50, offset=start)
            rader.extend(svar.get("quotes", []))
            if start + 50 >= svar.get("total", 0):
                break
    except Exception as e:
        resultat.fel = f"kunde inte söka hos Yahoo: {type(e).__name__}: {e}"
        logg.warning("%s", resultat.fel)
        return resultat

    resultat.traffar = len(rader)
    logg.info("screenern gav %d bolag över %s mdr kr",
              len(rader), c.min_borsvarde_mdr)

    redan = set(inst.alla_tickers())
    for rad in rader:
        ticker = rad.get("symbol")
        kurs = rad.get("regularMarketPrice")
        volym = rad.get("averageDailyVolume3Month")
        if not ticker or not kurs or not volym:
            continue
        if rad.get("fullExchangeName") != "Stockholm":
            continue
        if rad.get("currency") != "SEK":
            continue

        omsattning = kurs * volym
        if omsattning < c.min_daglig_omsattning_mkr * 1e6:
            continue

        resultat.kandidater.append(Kandidat(
            ticker=ticker,
            namn=rad.get("longName") or rad.get("shortName") or ticker,
            borsvarde=rad.get("marketCap") or 0.0,
            daglig_omsattning=omsattning,
            redan_med=ticker in redan,
        ))

    _flagga_aktieslag(resultat, redan)
    logg.info("%d klarar båda gränserna, varav %d är nya",
              len(resultat.kandidater), len(resultat.nya))

    if hamta_sektor and resultat.nya:
        _fyll_sektorer(resultat.nya, paus_sek)
    return resultat


def _flagga_aktieslag(resultat: Sokresultat, redan: set[str]) -> None:
    """Markerar bolag som redan finns i universumet i ett annat aktieslag,
    och par av aktieslag inom förslaget självt."""
    befintliga_stammar: dict[str, str] = {}
    for ticker in redan:
        befintliga_stammar.setdefault(aktieslagsstam(ticker), ticker)

    per_stam: dict[str, list[Kandidat]] = {}
    for k in resultat.nya:
        per_stam.setdefault(aktieslagsstam(k.ticker), []).append(k)

    for stam, bolag in per_stam.items():
        befintlig = befintliga_stammar.get(stam)
        if befintlig:
            for k in bolag:
                k.aktieslagskrock = befintlig
        if len(bolag) > 1:
            # Behåll det mest omsatta, flagga de andra
            bolag.sort(key=lambda x: -x.daglig_omsattning)
            for k in bolag[1:]:
                k.aktieslagskrock = bolag[0].ticker


def _fyll_sektorer(kandidater: list[Kandidat], paus_sek: float) -> None:
    """Hämtar sektor per bolag och föreslår kategori."""
    import yfinance as yf

    for nr, k in enumerate(kandidater, start=1):
        if nr % 25 == 0:
            logg.info("  sektor %d/%d", nr, len(kandidater))
        try:
            info = yf.Ticker(k.ticker).info or {}
            k.sektor = info.get("sector")
        except Exception as e:
            logg.debug("%s: ingen sektor (%s)", k.ticker, e)

        if k.ticker in KANDA_INVESTMENTBOLAG:
            k.kategori = None
            k.anmarkning = INVESTMENTBOLAGSVARNING
        elif k.sektor in KRAVER_BEDOMNING:
            k.kategori, k.anmarkning = KRAVER_BEDOMNING[k.sektor]
        elif k.sektor in SEKTORKARTA:
            k.kategori = SEKTORKARTA[k.sektor]
        else:
            k.kategori = None
            k.anmarkning = (
                f"Yahoo anger sektorn {k.sektor!r} som vi inte har någon "
                f"kategori för" if k.sektor else "Yahoo anger ingen sektor"
            )
        if paus_sek:
            time.sleep(paus_sek)


def yaml_forslag(resultat: Sokresultat) -> str:
    """Ett färdigt block att klistra in i universum.yaml."""
    rader = [
        "# Förslag från: python -m aktieanalys hitta-universum",
        "# Läs igenom innan du använder det. Kategoriindelningen är en",
        "# bedömning, inte ett faktum.",
        "",
    ]
    for kategori, bolag in sorted(resultat.per_kategori().items()):
        rader.append(f"  # --- {kategori} ---")
        for k in bolag:
            kommentar = f"{k.namn[:34]}  {k.borsvarde_mdr:.0f} mdr, {k.omsattning_mkr:.0f} mkr/dag"
            markering = "  # KONTROLLERA" if k.anmarkning else ""
            rader.append(f"      - {k.ticker:16}# {kommentar}{markering}")
        rader.append("")
    return "\n".join(rader)
