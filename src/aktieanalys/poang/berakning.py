"""Formlerna: från rådata till nyckeltal.

Varje mått i katalogen (poang/matt.py) har sin funktion här. Funktionen
tar ett Bolagsdata och ger ett tal -- eller None när talet skulle bli
meningslöst.

Att ge None är lika viktigt som att räkna rätt. EV/EBIT på ett bolag
med rörelseförlust blir ett negativt tal som ser billigt ut men betyder
motsatsen; P/E på en förlust likaså. Sådana fall ger None, och bolaget
räknas som att det saknar måttet i stället för att få en vilseledande
rangordning.

Historiska jämförelser använder MEDIAN, inte medelvärde: ett enda
extremår ska inte flytta bolagets "normala" värdering.
"""

from __future__ import annotations

import datetime as dt
import statistics
from typing import Callable

import pandas as pd

from aktieanalys.data.bolagsdata import Bolagsdata
from aktieanalys.logg import hamta

logg = hamta(__name__)

HANDELSDAGAR_PER_MANAD = 21


# =====================================================================
#  Hjälpfunktioner
# =====================================================================
def _kvot(taljare: float | None, namnare: float | None,
          kraver_positiv_namnare: bool = True) -> float | None:
    """Division med spärr mot noll och, där det krävs, mot negativ nämnare."""
    if taljare is None or namnare is None:
        return None
    if namnare == 0:
        return None
    if kraver_positiv_namnare and namnare < 0:
        return None
    return taljare / namnare


def _kurs_vid(serie: pd.Series | None, datum: dt.date) -> float | None:
    """Senaste kursen till och med ett datum."""
    if serie is None or len(serie) == 0:
        return None
    fram_till = serie[serie.index <= pd.Timestamp(datum)]
    return float(fram_till.iloc[-1]) if len(fram_till) else None


def _stabilitet(varden: list[float]) -> float | None:
    """Stabilitet som 1 / (1 + spridning). Alltid mellan 0 och 1, högre
    är stabilare. Spridningen mäts som variationskoefficient, alltså
    standardavvikelse delad med medelvärdets storlek, så att bolag av
    olika storlek går att jämföra."""
    if len(varden) < 2:
        return None
    medel = statistics.fmean(varden)
    if medel == 0:
        return None
    spridning = statistics.stdev(varden) / abs(medel)
    return 1.0 / (1.0 + spridning)


def _stabilitet_procentenheter(varden: list[float]) -> float | None:
    """Stabilitet för tal som redan är andelar, t.ex. marginaler.

    Här mäts spridningen i procentenheter i stället för relativt. En
    marginal som pendlar mellan 9 och 11 procent är stabil; att dela med
    medelvärdet skulle göra lågmarginalbolag orättvist ostabila.
    """
    if len(varden) < 2:
        return None
    spridning = statistics.stdev(varden)
    return 1.0 / (1.0 + spridning * 100)


def _tillvaxt(serie: list[tuple[int, float]] | None, min_ar: int) -> float | None:
    """Årlig tillväxttakt (CAGR) från äldsta till nyaste året.

    Returnerar None om startvärdet är noll eller negativt -- en
    tillväxttakt från ett negativt tal betyder ingenting.
    """
    if not serie or len(serie) < min_ar:
        return None
    ordnad = sorted(serie, key=lambda x: x[0])
    (forsta_ar, forsta), (sista_ar, sista) = ordnad[0], ordnad[-1]
    ar = sista_ar - forsta_ar
    if ar <= 0 or forsta <= 0 or sista <= 0:
        return None
    return (sista / forsta) ** (1 / ar) - 1


def _historiska_kvoter(
    bd: Bolagsdata,
    namnare_historik: str,
    per_aktie: bool,
    min_ar: int,
) -> list[float] | None:
    """Bolagets egen värderingshistorik, ett värde per räkenskapsår.

    Kursen tas vid respektive årsskifte. Antalet aktier tas från samma
    år när det finns -- ett bolag som nyemitterat ska inte få sin
    historik räknad på dagens aktieantal.

    Nettoskulden hålls konstant på dagens nivå; vi har ingen
    skuldhistorik. Det är en approximation, och den gör jämförelsen
    något trubbigare för bolag som ändrat sin skuldsättning kraftigt.
    """
    kurser = bd.varde("kurshistorik")
    namnare = bd.varde(namnare_historik)
    if kurser is None or not namnare:
        return None

    aktier_per_ar = dict(bd.varde("antal_aktier_historik") or [])
    aktier_nu = bd.varde("antal_aktier")
    nettoskuld = 0.0
    if not per_aktie:
        borsvarde = bd.varde("borsvarde")
        bolagsvarde = bd.varde("bolagsvarde")
        if borsvarde is None or bolagsvarde is None:
            return None
        nettoskuld = bolagsvarde - borsvarde

    kvoter: list[float] = []
    for ar, varde in namnare:
        if varde is None or varde <= 0:
            continue  # förlustår ger ingen meningsfull värderingskvot
        kurs = _kurs_vid(kurser, dt.date(ar, 12, 31))
        if kurs is None:
            continue
        if per_aktie:
            kvoter.append(kurs / varde)
            continue
        aktier = aktier_per_ar.get(ar) or aktier_nu
        if not aktier:
            continue
        kvoter.append((kurs * aktier + nettoskuld) / varde)

    return kvoter if len(kvoter) >= min_ar else None


# =====================================================================
#  Kvalitet
# =====================================================================
def roic(bd: Bolagsdata, min_ar: int) -> float | None:
    return _kvot(bd.varde("rorelseresultat"), bd.varde("investerat_kapital"))


def roe(bd: Bolagsdata, min_ar: int) -> float | None:
    return _kvot(bd.varde("nettoresultat"), bd.varde("eget_kapital"))


def rorelsemarginal_stabilitet_5ar(bd: Bolagsdata, min_ar: int) -> float | None:
    resultat = dict(bd.varde("rorelseresultat_historik") or [])
    omsattning = dict(bd.varde("omsattning_historik") or [])
    marginaler = [
        resultat[ar] / omsattning[ar]
        for ar in resultat
        if omsattning.get(ar) not in (None, 0) and omsattning[ar] > 0
    ]
    if len(marginaler) < min_ar:
        return None
    return _stabilitet_procentenheter(marginaler)


def vinststabilitet_5ar(bd: Bolagsdata, min_ar: int) -> float | None:
    serie = bd.varde("nettoresultat_historik")
    if not serie or len(serie) < min_ar:
        return None
    return _stabilitet([v for _, v in serie])


def forvaltningsresultat_per_aktie_tillvaxt(bd: Bolagsdata, min_ar: int) -> float | None:
    return _tillvaxt(bd.varde("forvaltningsresultat_per_aktie_historik"), min_ar)


def substansvarde_tillvaxt(bd: Bolagsdata, min_ar: int) -> float | None:
    return _tillvaxt(bd.varde("substansvarde_per_aktie_historik"), min_ar)


# =====================================================================
#  Värdering
# =====================================================================
def ev_ebit(bd: Bolagsdata, min_ar: int) -> float | None:
    """Negativt rörelseresultat ger None: en negativ EV/EBIT ser låg ut
    men betyder att bolaget går med förlust."""
    return _kvot(bd.varde("bolagsvarde"), bd.varde("rorelseresultat"))


def pe(bd: Bolagsdata, min_ar: int) -> float | None:
    return _kvot(bd.varde("kurs"), bd.varde("vinst_per_aktie"))


def p_b(bd: Bolagsdata, min_ar: int) -> float | None:
    return _kvot(bd.varde("kurs"), bd.varde("eget_kapital_per_aktie"))


def kurs_mot_substans(bd: Bolagsdata, min_ar: int) -> float | None:
    return _kvot(bd.varde("kurs"), bd.varde("substansvarde_per_aktie"))


def kurs_mot_forvaltningsresultat(bd: Bolagsdata, min_ar: int) -> float | None:
    return _kvot(bd.varde("kurs"), bd.varde("forvaltningsresultat_per_aktie"))


def ev_ebit_mot_egen_historik(bd: Bolagsdata, min_ar: int) -> float | None:
    """Dagens EV/EBIT delat med bolagets egen historiska median.

    Under 1,0 betyder billigare än bolaget brukar vara.
    """
    nu = ev_ebit(bd, min_ar)
    historik = _historiska_kvoter(bd, "rorelseresultat_historik", False, min_ar)
    if nu is None or not historik:
        return None
    return _kvot(nu, statistics.median(historik))


def pe_mot_egen_historik(bd: Bolagsdata, min_ar: int) -> float | None:
    nu = pe(bd, min_ar)
    historik = _historiska_kvoter(bd, "vinst_per_aktie_historik", True, min_ar)
    if nu is None or not historik:
        return None
    return _kvot(nu, statistics.median(historik))


def substansrabatt_mot_historik(bd: Bolagsdata, min_ar: int) -> float | None:
    """Dagens substansrabatt minus bolagets historiska median.

    Positivt tal betyder större rabatt än vanligt, alltså billigare.
    """
    kurs = bd.varde("kurs")
    substans = bd.varde("substansvarde_per_aktie")
    if kurs is None or not substans or substans <= 0:
        return None
    nu = 1 - kurs / substans

    kurser = bd.varde("kurshistorik")
    historik = bd.varde("substansvarde_per_aktie_historik")
    if kurser is None or not historik:
        return None
    tidigare = []
    for ar, varde in historik:
        if not varde or varde <= 0:
            continue
        gammal_kurs = _kurs_vid(kurser, dt.date(ar, 12, 31))
        if gammal_kurs is not None:
            tidigare.append(1 - gammal_kurs / varde)
    if len(tidigare) < min_ar:
        return None
    return nu - statistics.median(tidigare)


# =====================================================================
#  Finansiell styrka
# =====================================================================
def nettoskuld_ebitda(bd: Bolagsdata, min_ar: int) -> float | None:
    """Nettokassa (negativ nettoskuld) ger ett negativt tal, vilket är
    bra -- måttet är märkt så att lägre är bättre."""
    return _kvot(bd.varde("nettoskuld"), bd.varde("ebitda"))


def kapitalstyrka(bd: Bolagsdata, min_ar: int) -> float | None:
    return bd.varde("cet1_kvot")


def belaningsgrad(bd: Bolagsdata, min_ar: int) -> float | None:
    return bd.varde("belaningsgrad")


def rantetackningsgrad(bd: Bolagsdata, min_ar: int) -> float | None:
    return bd.varde("rantetackningsgrad")


def skuldsattning_mot_substans(bd: Bolagsdata, min_ar: int) -> float | None:
    return _kvot(bd.varde("nettoskuld"), bd.varde("substansvarde_totalt"))


# =====================================================================
#  Momentum
# =====================================================================
def momentum_12m_exkl_1m(bd: Bolagsdata, min_ar: int) -> float | None:
    """Kursutveckling 12 månader bakåt, utan den senaste månaden.

    Den senaste månaden tas bort eftersom kortsiktiga rörelser
    historiskt tenderar att vända. Mätpunkterna är ungefär 1 och 13
    månader tillbaka från seriens sista dag.
    """
    serie = bd.varde("kurshistorik")
    if serie is None or len(serie) < 250:
        return None
    sista = serie.index[-1].date()
    slut = _kurs_vid(serie, sista - dt.timedelta(days=30))
    start = _kurs_vid(serie, sista - dt.timedelta(days=395))
    if start is None or slut is None or start <= 0:
        return None
    if (sista - serie.index[0].date()).days < 370:
        return None  # serien är för kort för att mätpunkterna ska vara äkta
    return slut / start - 1


# =====================================================================
#  Registret
# =====================================================================
BERAKNINGAR: dict[str, Callable[[Bolagsdata, int], float | None]] = {
    "roic": roic,
    "roe": roe,
    "rorelsemarginal_stabilitet_5ar": rorelsemarginal_stabilitet_5ar,
    "vinststabilitet_5ar": vinststabilitet_5ar,
    "forvaltningsresultat_per_aktie_tillvaxt": forvaltningsresultat_per_aktie_tillvaxt,
    "substansvarde_tillvaxt": substansvarde_tillvaxt,
    "ev_ebit": ev_ebit,
    "pe": pe,
    "p_b": p_b,
    "kurs_mot_substans": kurs_mot_substans,
    "kurs_mot_forvaltningsresultat": kurs_mot_forvaltningsresultat,
    "ev_ebit_mot_egen_historik": ev_ebit_mot_egen_historik,
    "pe_mot_egen_historik": pe_mot_egen_historik,
    "substansrabatt_mot_historik": substansrabatt_mot_historik,
    "nettoskuld_ebitda": nettoskuld_ebitda,
    "kapitalstyrka": kapitalstyrka,
    "belaningsgrad": belaningsgrad,
    "rantetackningsgrad": rantetackningsgrad,
    "skuldsattning_mot_substans": skuldsattning_mot_substans,
    "momentum_12m_exkl_1m": momentum_12m_exkl_1m,
}


def berakna(mattnamn: str, bd: Bolagsdata, min_ar: int) -> float | None:
    """Räknar ut ett mått. None när talet skulle bli meningslöst."""
    funktion = BERAKNINGAR.get(mattnamn)
    if funktion is None:
        raise KeyError(f"ingen beräkning för måttet {mattnamn!r}")
    try:
        return funktion(bd, min_ar)
    except Exception as e:
        # Ett oväntat fel i en formel får inte stoppa hela veckokörningen.
        logg.exception("%s: fel vid beräkning av %s", bd.ticker, mattnamn)
        return None
