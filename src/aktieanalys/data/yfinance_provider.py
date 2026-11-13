"""Datakälla: yfinance (Yahoo Finance).

yfinance är **inofficiellt**. Det finns inget avtal, ingen garanti och
ingen support. Yahoo kan ändra svarsformat, strypa anrop eller svara tomt
utan förvarning. Hela den här modulen är skriven utifrån det:

* varje nätanrop görs i upp till `max_forsok` försök med paus emellan
* varje fält hämtas med en huvudkälla och, där det går, en reservväg
* ingenting höjs utåt -- problem hamnar i bolagsdata.fel och .saknade
* varje fält som sätts får en källetikett, så rapporten kan visa härkomst

Vet du att ett värde saknas är det ett resultat. Gissar vi är det ett fel.
"""

from __future__ import annotations

import datetime as dt
import logging
import time
from typing import Any, Callable

import pandas as pd

from aktieanalys.data.bolagsdata import Bolagsdata
from aktieanalys.data.provider import Provider
from aktieanalys.data.valuta import Valutaomraknare, naiva_datum
from aktieanalys.logg import hamta

logg = hamta(__name__)

# yfinance är pratsam och loggar varje misslyckat anrop som fel. Vi gör
# vår egen felhantering, så dess egen logg dämpas.
logging.getLogger("yfinance").setLevel(logging.CRITICAL)


class _Vaxling:
    """Räknar om räkenskapssiffror till kronor för ett enskilt bolag.

    Håller reda på vilken valuta bolaget redovisar i, och markerar fält
    som saknade om omräkningen inte går -- ett oomräknat tal är värre
    än inget tal.
    """

    def __init__(
        self, bd: Bolagsdata, rapportvaluta: str | None, omraknare: Valutaomraknare
    ) -> None:
        self.bd = bd
        self.valuta = rapportvaluta  # None = OKÄND, inte SEK
        self.omraknare = omraknare

    @property
    def kand(self) -> bool:
        return self.valuta is not None

    @property
    def behovs(self) -> bool:
        return self.valuta != "SEK"

    OKAND_ORSAK = (
        "rapportvalutan är okänd -- Yahoo utelämnade financialCurrency och "
        "ingen tidigare hämtning har den. Utan valuta går det inte att avgöra "
        "om siffran behöver räknas om till kronor."
    )

    def satt(
        self, falt: str, belopp: float | None, kalla: str, ar: int | None = None
    ) -> None:
        """Sätter ett räkenskapsfält, omräknat till kronor."""
        if belopp is None:
            self.bd.markera_saknat(falt, "värdet saknades i källan")
            return
        if not self.kand:
            self.bd.markera_saknat(falt, self.OKAND_ORSAK)
            return
        if not self.behovs:
            self.bd.satt(falt, belopp, kalla)
            return
        i_kronor = self.omraknare.till_sek(belopp, self.valuta, ar)
        if i_kronor is None:
            self.bd.markera_saknat(
                falt,
                f"kunde inte räknas om från {self.valuta} till kronor "
                f"-- valutakursen gick inte att hämta",
            )
            return
        self.bd.satt(falt, i_kronor, f"{kalla}, omräknat från {self.valuta}")

    def satt_serie(
        self, falt: str, serie: list[tuple[int, float]] | None, kalla: str
    ) -> None:
        """Sätter en årsserie, varje år omräknat med sin egen valutakurs."""
        if serie is None:
            return
        if not self.kand:
            self.bd.markera_saknat(falt, self.OKAND_ORSAK)
            return
        if not self.behovs:
            self.bd.satt(falt, serie, kalla)
            return
        i_kronor = self.omraknare.serie_till_sek(serie, self.valuta)
        if not i_kronor:
            self.bd.markera_saknat(
                falt,
                f"kunde inte räknas om från {self.valuta} till kronor "
                f"-- valutakursen gick inte att hämta",
            )
            return
        if len(i_kronor) < len(serie):
            logg.warning(
                "%s: %d av %d år kunde räknas om för %s",
                self.bd.ticker, len(i_kronor), len(serie), falt,
            )
        self.bd.satt(falt, i_kronor, f"{kalla}, omräknat från {self.valuta}")


class YfinanceProvider(Provider):
    namn = "yfinance"

    def __init__(
        self,
        max_forsok: int = 3,
        paus_sek: float = 2.0,
        handelsdagar_for_omsattning: int = 60,
        ar_historik: int = 5,
        valutaomraknare: Valutaomraknare | None = None,
    ) -> None:
        self.max_forsok = max_forsok
        self.paus_sek = paus_sek
        self.handelsdagar = handelsdagar_for_omsattning
        self.ar_historik = ar_historik
        self.valutaomraknare = valutaomraknare or Valutaomraknare(
            max_forsok=max_forsok, paus_sek=paus_sek, ar_historik=ar_historik
        )

    # =================================================================
    #  Nätanrop med omförsök
    # =================================================================
    def _forsok(
        self, vad: str, anrop: Callable[[], Any], bd: Bolagsdata
    ) -> Any | None:
        """Kör ett nätanrop med omförsök. Returnerar None om allt fallerar."""
        for n in range(1, self.max_forsok + 1):
            try:
                svar = anrop()
            except Exception as e:  # yfinance höjer allt från HTTP- till nyckelfel
                logg.debug("%s: %s försök %d/%d gav fel: %s",
                           bd.ticker, vad, n, self.max_forsok, e)
                if n == self.max_forsok:
                    bd.anteckna_fel(f"{vad}: {type(e).__name__}: {e}")
                    return None
                time.sleep(self.paus_sek)
                continue

            if svar is None or (hasattr(svar, "empty") and svar.empty) or (
                isinstance(svar, dict) and len(svar) <= 1
            ):
                logg.debug("%s: %s försök %d/%d gav tomt svar",
                           bd.ticker, vad, n, self.max_forsok)
                if n == self.max_forsok:
                    bd.anteckna_fel(f"{vad}: tomt svar från Yahoo")
                    return None
                time.sleep(self.paus_sek)
                continue
            return svar
        return None

    # =================================================================
    #  Huvudmetoden
    # =================================================================
    def hamta(
        self,
        ticker: str,
        idag: dt.date | None = None,
        kand_meta: dict[str, str | None] | None = None,
    ) -> Bolagsdata:
        """kand_meta är senast kända namn/valutor ur databasen, som används
        när Yahoos svar är bantat (vilket händer vid strypning)."""
        kand_meta = kand_meta or {}
        bd = Bolagsdata(ticker=ticker, hamtad=idag or dt.date.today())
        try:
            import yfinance as yf
        except ImportError:
            bd.anteckna_fel("yfinance är inte installerat (pip install -e .)")
            self._markera_allt_saknat(bd, "yfinance saknas")
            return bd

        try:
            aktie = yf.Ticker(ticker)
        except Exception as e:
            bd.anteckna_fel(f"kunde inte skapa Ticker: {e}")
            self._markera_allt_saknat(bd, "ingen kontakt med Yahoo")
            return bd

        info: dict[str, Any] = self._forsok("info", lambda: aktie.info, bd) or {}
        historik: pd.DataFrame | None = self._forsok(
            "kurshistorik",
            lambda: aktie.history(period=f"{self.ar_historik + 1}y", auto_adjust=True),
            bd,
        )
        resultat: pd.DataFrame | None = self._forsok(
            "resultatrakning", lambda: aktie.income_stmt, bd
        )
        balans: pd.DataFrame | None = self._forsok(
            "balansrakning", lambda: aktie.balance_sheet, bd
        )

        if not info and historik is None:
            bd.anteckna_fel(
                "Yahoo svarade inte alls för den här tickern. Kontrollera "
                "stavningen -- den kan vara felstavad eller avnoterad."
            )
            self._markera_allt_saknat(bd, "tickern gav inget svar")
            return bd

        bd.namn = info.get("longName") or info.get("shortName") or kand_meta.get("namn")
        bd.valuta = info.get("currency") or kand_meta.get("valuta")
        bd.rapportvaluta = info.get("financialCurrency") or kand_meta.get("rapportvaluta")
        if info.get("financialCurrency") is None and bd.rapportvaluta:
            logg.info(
                "%s: Yahoo utelämnade financialCurrency, använder senast kända (%s)",
                ticker, bd.rapportvaluta,
            )
        if bd.rapportvaluta is None:
            bd.anteckna_fel(
                "rapportvalutan är okänd: räkenskapssiffrorna kan inte "
                "valutasäkras och utesluts därför"
            )
        self._kontrollera_valuta(bd)

        # Regel 1 i provider-kontraktet: höj aldrig ett undantag utåt.
        # Ett oväntat fel i tolkningen av Yahoos svar får stoppa ETT
        # bolag, aldrig hela veckokörningen. Det som hunnit sättas
        # behålls; resten markeras saknat av _markera_resten_saknat.
        vaxling = _Vaxling(bd, bd.rapportvaluta, self.valutaomraknare)
        for namn, steg in (
            ("marknadsdata", lambda: self._marknadsdata(bd, info, historik)),
            ("resultaträkning", lambda: self._resultatdata(bd, info, resultat, vaxling)),
            ("balansräkning", lambda: self._balansdata(bd, info, balans, vaxling)),
        ):
            try:
                steg()
            except Exception as e:
                logg.exception("%s: oväntat fel vid tolkning av %s", ticker, namn)
                bd.anteckna_fel(
                    f"oväntat fel vid tolkning av {namn}: {type(e).__name__}: {e}"
                )
        self._markera_resten_saknat(bd)
        return bd

    # =================================================================
    #  Valutakontroll
    # =================================================================
    def _kontrollera_valuta(self, bd: Bolagsdata) -> None:
        """Varnar när aktien handlas i en annan valuta än den redovisas i.

        AstraZeneca handlas i kronor men redovisar i dollar, Nordea i
        kronor men redovisar i euro. Blandar man ihop det blir P/E och
        EV/EBIT fel med en faktor tio. Vi flaggar det i stället för att
        räkna vidare som om ingenting hänt.
        """
        if bd.valuta and bd.valuta != "SEK":
            bd.anteckna_fel(
                f"aktien handlas i {bd.valuta}, inte SEK -- storleksgränserna "
                f"i universum.yaml är satta i kronor"
            )
        if bd.rapportvaluta and bd.valuta and bd.rapportvaluta != bd.valuta:
            bd.anteckna_fel(
                f"kursen är i {bd.valuta} men räkenskaperna i "
                f"{bd.rapportvaluta}: räkenskapssiffrorna räknas om till kronor"
            )
            logg.warning("%s: valutakrock %s/%s", bd.ticker, bd.valuta, bd.rapportvaluta)

    # =================================================================
    #  Marknadsdata
    # =================================================================
    def _marknadsdata(
        self, bd: Bolagsdata, info: dict[str, Any], historik: pd.DataFrame | None
    ) -> None:
        kalla = "yfinance:info"

        kurs = _tal(info, "currentPrice", "regularMarketPrice", "previousClose")
        if kurs is None and historik is not None and "Close" in historik:
            serie = historik["Close"].dropna()
            if len(serie):
                kurs = float(serie.iloc[-1])
                kalla = "yfinance:kurshistorik"
        if kurs is None:
            bd.markera_saknat("kurs", "ingen kurs i Yahoos svar")
        else:
            bd.satt("kurs", kurs, kalla)

        bd.satt("borsvarde", _tal(info, "marketCap"), "yfinance:info")
        bd.satt("saljkurs", _tal(info, "bid"), "yfinance:info")
        bd.satt("kopkurs", _tal(info, "ask"), "yfinance:info")

        if historik is None or "Close" not in historik:
            bd.markera_saknat("kurshistorik", "Yahoo gav ingen kurshistorik")
            bd.markera_saknat("daglig_omsattning", "kräver kurshistorik")
            return

        stangning = naiva_datum(historik["Close"].dropna())
        if len(stangning) < 30:
            bd.markera_saknat(
                "kurshistorik",
                f"bara {len(stangning)} handelsdagar -- för kort för momentum "
                f"(12 månader krävs)",
            )
        else:
            bd.satt("kurshistorik", stangning, "yfinance:history")

        # Omsättning i kronor per dag, inte antal aktier. Yahoos
        # averageVolume är styckantal och duger inte som likviditetsmått.
        if "Volume" in historik:
            sista = historik.tail(self.handelsdagar)
            belopp = (sista["Close"] * sista["Volume"]).dropna()
            if len(belopp) >= 10:
                bd.satt(
                    "daglig_omsattning",
                    float(belopp.mean()),
                    f"yfinance:history ({len(belopp)} dagar)",
                )
            else:
                bd.markera_saknat(
                    "daglig_omsattning",
                    f"bara {len(belopp)} dagar med volymdata",
                )
        else:
            bd.markera_saknat("daglig_omsattning", "Yahoo gav ingen volymdata")

    # =================================================================
    #  Resultaträkning  (siffrorna är i rapportvalutan)
    # =================================================================
    def _resultatdata(
        self,
        bd: Bolagsdata,
        info: dict[str, Any],
        resultat: pd.DataFrame | None,
        vaxling: _Vaxling,
    ) -> None:
        if resultat is None:
            for falt in (
                "omsattning", "rorelseresultat", "nettoresultat",
                "omsattning_historik", "rorelseresultat_historik",
                "nettoresultat_historik", "vinst_per_aktie_historik",
            ):
                bd.markera_saknat(falt, "Yahoo gav ingen resultaträkning")
            vaxling.satt("ebitda", _tal(info, "ebitda"), "yfinance:info")
            self._vinst_per_aktie(bd, info)
            return

        kalla = "yfinance:income_stmt"
        par = {
            "omsattning_historik": ("Total Revenue", "Operating Revenue"),
            "rorelseresultat_historik": ("Operating Income", "EBIT"),
            "nettoresultat_historik": (
                "Net Income",
                "Net Income Common Stockholders",
                "Net Income Continuous Operations",
            ),
            "vinst_per_aktie_historik": ("Diluted EPS", "Basic EPS"),
        }
        for falt, kandidater in par.items():
            serie = _arsserie(resultat, *kandidater)
            if serie is None:
                bd.markera_saknat(
                    falt,
                    f"ingen av raderna {', '.join(kandidater)} fanns i "
                    f"resultaträkningen",
                )
                continue
            serie = serie[: self.ar_historik]
            vaxling.satt_serie(falt, serie, f"{kalla} ({len(serie)} år)")
            if len(serie) < self.ar_historik:
                logg.debug(
                    "%s: %s har bara %d av %d år",
                    bd.ticker, falt, len(serie), self.ar_historik,
                )

        # Senaste året som enskilt tal. Tas ur den redan omräknade
        # historiken, så att ingenting kan bli omräknat två gånger.
        for falt, historikfalt in [
            ("omsattning", "omsattning_historik"),
            ("rorelseresultat", "rorelseresultat_historik"),
            ("nettoresultat", "nettoresultat_historik"),
        ]:
            serie = bd.varde(historikfalt)
            if serie:
                bd.satt(falt, serie[0][1], f"{bd.kallor[historikfalt]} ({serie[0][0]})")
            else:
                bd.markera_saknat(falt, "saknas i resultaträkningen")

        ebitda = _arsserie(resultat, "EBITDA", "Normalized EBITDA")
        if ebitda:
            vaxling.satt("ebitda", ebitda[0][1], f"{kalla} ({ebitda[0][0]})", ebitda[0][0])
        else:
            vaxling.satt("ebitda", _tal(info, "ebitda"), "yfinance:info")

        self._vinst_per_aktie(bd, info)

    @staticmethod
    def _vinst_per_aktie(bd: Bolagsdata, info: dict[str, Any]) -> None:
        """trailingEps är i HANDELSVALUTAN och ska inte räknas om.

        Uppmätt på AstraZeneca: trailingEps 66.43 mot ett nettoresultat på
        10.2 miljarder dollar och 1.55 miljarder aktier -- alltså kronor.
        Därför ger Yahoos trailingPE rätt svar även för dessa bolag,
        medan deras priceToBook inte gör det.
        """
        vpa = _tal(info, "trailingEps")
        if vpa is not None:
            bd.satt("vinst_per_aktie", vpa, "yfinance:info (rullande 12 mån, i kronor)")
            return
        serie = bd.varde("vinst_per_aktie_historik")
        if serie:
            bd.satt("vinst_per_aktie", serie[0][1],
                    f"{bd.kallor['vinst_per_aktie_historik']} ({serie[0][0]})")
        else:
            bd.markera_saknat("vinst_per_aktie", "saknas både i info och rapport")

    # =================================================================
    #  Balansräkning  (siffrorna är i rapportvalutan)
    # =================================================================
    def _balansdata(
        self,
        bd: Bolagsdata,
        info: dict[str, Any],
        balans: pd.DataFrame | None,
        vaxling: _Vaxling,
    ) -> None:
        kalla = "yfinance:balance_sheet"

        ar_eget, eget = _senaste_med_ar(
            balans, "Stockholders Equity", "Total Equity Gross Minority Interest"
        )
        ar_skuld, skulder = _senaste_med_ar(balans, "Total Debt")
        _, kassa = _senaste_med_ar(
            balans,
            "Cash And Cash Equivalents",
            "Cash Cash Equivalents And Short Term Investments",
        )
        _, aktier = _senaste_med_ar(balans, "Ordinary Shares Number", "Share Issued")
        ar_inv, investerat = _senaste_med_ar(balans, "Invested Capital")
        ar_netto, nettoskuld = _senaste_med_ar(balans, "Net Debt")

        # Antal aktier är ett styckantal och räknas aldrig om.
        # Info är färskare än balansräkningen.
        fran_info = _tal(info, "sharesOutstanding")
        bd.satt("antal_aktier", fran_info or aktier,
                "yfinance:info" if fran_info else kalla)

        if eget is not None:
            vaxling.satt("eget_kapital", eget, kalla, ar_eget)
        else:
            bd.markera_saknat("eget_kapital", "raden Stockholders Equity saknades")

        # bookValue är i RAPPORTVALUTAN -- det är felet som gör Yahoos
        # eget priceToBook obrukbart för AstraZeneca och Nordea. Det är
        # ett färskare kvartalsvärde än balansräkningen, så det räknas om
        # med senaste valutakursen.
        ekpa = _tal(info, "bookValue")
        if ekpa is not None:
            vaxling.satt("eget_kapital_per_aktie", ekpa, "yfinance:info")
        elif bd.har("eget_kapital") and bd.varde("antal_aktier"):
            bd.satt(
                "eget_kapital_per_aktie",
                bd.varde("eget_kapital") / bd.varde("antal_aktier"),
                "beräknat: eget kapital / antal aktier",
            )
        else:
            bd.markera_saknat(
                "eget_kapital_per_aktie", "saknar eget kapital eller antal aktier"
            )

        if nettoskuld is not None:
            vaxling.satt("nettoskuld", nettoskuld, kalla, ar_netto)
        elif skulder is not None:
            vaxling.satt(
                "nettoskuld", skulder - (kassa or 0.0),
                f"beräknat: totala skulder - kassa ({kalla})", ar_skuld,
            )
        elif _tal(info, "totalDebt") is not None:
            vaxling.satt(
                "nettoskuld",
                _tal(info, "totalDebt") - (_tal(info, "totalCash") or 0.0),
                "beräknat: totalDebt - totalCash (yfinance:info)",
            )
        else:
            bd.markera_saknat(
                "nettoskuld", "varken nettoskuld eller skulder/kassa fanns"
            )

        if investerat is not None:
            vaxling.satt("investerat_kapital", investerat, kalla, ar_inv)
        elif eget is not None and skulder is not None:
            vaxling.satt(
                "investerat_kapital", eget + skulder,
                f"beräknat: eget kapital + skulder ({kalla})", ar_eget,
            )
        else:
            bd.markera_saknat(
                "investerat_kapital",
                "raden Invested Capital saknades och gick inte att räkna",
            )

        # enterpriseValue är redan i HANDELSVALUTAN (uppmätt: det stämmer
        # med börsvärdet, som är kurs gånger antal aktier). Räknas inte om.
        ev = _tal(info, "enterpriseValue")
        if ev is not None:
            bd.satt("bolagsvarde", ev, "yfinance:info (i kronor)")
        elif bd.har("borsvarde") and bd.har("nettoskuld"):
            # Båda är kronor här: börsvärdet från början, nettoskulden
            # efter omräkningen ovan.
            bd.satt(
                "bolagsvarde",
                bd.varde("borsvarde") + bd.varde("nettoskuld"),
                "beräknat: börsvärde + nettoskuld",
            )
        else:
            bd.markera_saknat("bolagsvarde", "saknar börsvärde eller nettoskuld")

    # =================================================================
    #  Resten
    # =================================================================
    @staticmethod
    def _markera_resten_saknat(bd: Bolagsdata) -> None:
        """Manuella fält kan yfinance aldrig ge. CSV-lagret fyller dem sen."""
        from aktieanalys.data.bolagsdata import MANUELLA_FALT

        for falt in MANUELLA_FALT:
            bd.markera_saknat(falt, "finns inte i yfinance -- kräver CSV-komplettering")

    @staticmethod
    def _markera_allt_saknat(bd: Bolagsdata, orsak: str) -> None:
        from aktieanalys.data.bolagsdata import FALT

        for falt in FALT:
            bd.markera_saknat(falt, orsak)


# =====================================================================
#  Hjälpfunktioner
# =====================================================================
def _tal(info: dict[str, Any], *nycklar: str) -> float | None:
    """Första nyckeln i info som har ett användbart tal."""
    for n in nycklar:
        v = info.get(n)
        if v is None or isinstance(v, bool):
            continue
        try:
            tal = float(v)
        except (TypeError, ValueError):
            continue
        if tal != tal:  # NaN
            continue
        return tal
    return None


def _arsserie(df: pd.DataFrame | None, *rader: str) -> list[tuple[int, float]] | None:
    """Plockar en rad ur en årsrapport och ger [(år, värde), ...], nyast först."""
    if df is None or df.empty:
        return None
    for namn in rader:
        if namn not in df.index:
            continue
        rad = df.loc[namn]
        if isinstance(rad, pd.DataFrame):  # dubblerad radrubrik hos Yahoo
            rad = rad.iloc[0]
        ut: list[tuple[int, float]] = []
        for kolumn, varde in rad.items():
            if pd.isna(varde):
                continue
            try:
                ut.append((pd.Timestamp(kolumn).year, float(varde)))
            except (TypeError, ValueError):
                continue
        if ut:
            return sorted(ut, key=lambda x: x[0], reverse=True)
    return None


def _senaste(df: pd.DataFrame | None, *rader: str) -> float | None:
    """Senaste årets värde för en rad i en årsrapport."""
    serie = _arsserie(df, *rader)
    return serie[0][1] if serie else None


def _senaste_med_ar(
    df: pd.DataFrame | None, *rader: str
) -> tuple[int | None, float | None]:
    """Senaste årets värde OCH vilket år det gäller.

    Året behövs för att kunna räkna om beloppet med valutakursen som
    gällde vid det bokslutet.
    """
    serie = _arsserie(df, *rader)
    return (serie[0][0], serie[0][1]) if serie else (None, None)
