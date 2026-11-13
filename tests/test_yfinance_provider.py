"""Tester för yfinance-providerns tolkning av Yahoos svar.

Inga nätanrop. Yahoos svar matas in som konstruerade tabeller, så att
tolkningen kan testas utan att testerna beror på vad Yahoo råkar svara
en viss dag. Själva nätanropen testas genom att köra kommandot
hamta-data mot ett par verkliga bolag.
"""

from __future__ import annotations

import datetime as dt

import numpy as np
import pandas as pd

from aktieanalys.data.bolagsdata import Bolagsdata
from aktieanalys.data.yfinance_provider import (
    YfinanceProvider,
    _arsserie,
    _senaste,
    _tal,
)

IDAG = dt.date(2026, 11, 13)


def arsrapport(rader: dict[str, list[float]], ar=(2025, 2024, 2023, 2022)):
    return pd.DataFrame(rader, index=[pd.Timestamp(f"{a}-12-31") for a in ar]).T


# =====================================================================
#  _tal: plocka tal ur info-dicten
# =====================================================================
def test_tal_tar_forsta_anvandbara_nyckeln():
    info = {"currentPrice": None, "regularMarketPrice": 323.4}
    assert _tal(info, "currentPrice", "regularMarketPrice") == 323.4


def test_tal_hoppar_over_nan_och_text():
    assert _tal({"a": float("nan"), "b": "inte ett tal", "c": 5}, "a", "b", "c") == 5


def test_tal_avvisar_boolean():
    """Yahoo skickar ibland true/false där ett tal väntas."""
    assert _tal({"a": True, "b": 7.0}, "a", "b") == 7.0


def test_tal_ger_none_nar_inget_finns():
    assert _tal({}, "marketCap") is None


# =====================================================================
#  _arsserie: plocka en rad ur en årsrapport
# =====================================================================
def test_arsserie_ger_nyast_forst():
    df = arsrapport({"Operating Income": [49.0, 69.0, 77.0, 61.0]})
    assert _arsserie(df, "Operating Income") == [
        (2025, 49.0), (2024, 69.0), (2023, 77.0), (2022, 61.0)
    ]


def test_arsserie_provar_flera_radnamn():
    """Yahoo kallar samma sak olika saker för olika bolag."""
    df = arsrapport({"EBIT": [10.0, 9.0, 8.0, 7.0]})
    assert _arsserie(df, "Operating Income", "EBIT")[0] == (2025, 10.0)


def test_arsserie_hoppar_over_tomma_ar():
    df = arsrapport({"Net Income": [34.0, np.nan, 50.0, 40.0]})
    assert [a for a, _ in _arsserie(df, "Net Income")] == [2025, 2023, 2022]


def test_arsserie_klarar_dubblerad_radrubrik():
    df = arsrapport({"Net Income": [34.0, 50.0, 60.0, 40.0]})
    df = pd.concat([df, df])  # Yahoo levererar ibland raden två gånger
    assert _arsserie(df, "Net Income")[0] == (2025, 34.0)


def test_arsserie_ger_none_for_saknad_rad():
    assert _arsserie(arsrapport({"Total Revenue": [1.0, 2.0, 3.0, 4.0]}), "EBITDA") is None


def test_arsserie_ger_none_for_tom_tabell():
    assert _arsserie(pd.DataFrame(), "Net Income") is None
    assert _arsserie(None, "Net Income") is None


def test_senaste_tar_senaste_aret():
    assert _senaste(arsrapport({"Total Debt": [174.0, 160.0, 150.0, 140.0]}),
                    "Total Debt") == 174.0


# =====================================================================
#  Valutakontroll
# =====================================================================
def test_valutakrock_flaggas():
    """AstraZeneca handlas i kronor men redovisar i dollar."""
    p = YfinanceProvider()
    bd = Bolagsdata(ticker="AZN.ST", hamtad=IDAG)
    bd.valuta, bd.rapportvaluta = "SEK", "USD"
    p._kontrollera_valuta(bd)
    assert any("VALUTAKROCK" in f for f in bd.fel)


def test_samma_valuta_ger_ingen_varning():
    p = YfinanceProvider()
    bd = Bolagsdata(ticker="VOLV-B.ST", hamtad=IDAG)
    bd.valuta, bd.rapportvaluta = "SEK", "SEK"
    p._kontrollera_valuta(bd)
    assert bd.fel == []


def test_handel_i_annan_valuta_an_kronor_flaggas():
    p = YfinanceProvider()
    bd = Bolagsdata(ticker="NDA-FI.HE", hamtad=IDAG)
    bd.valuta, bd.rapportvaluta = "EUR", "EUR"
    p._kontrollera_valuta(bd)
    assert any("inte SEK" in f for f in bd.fel)


# =====================================================================
#  Marknadsdata
# =====================================================================
def kurshistorik(dagar=300, kurs=100.0, volym=1_000_000.0):
    datum = pd.bdate_range(end="2026-11-13", periods=dagar)
    return pd.DataFrame(
        {"Close": [kurs] * dagar, "Volume": [volym] * dagar}, index=datum
    )


def test_daglig_omsattning_raknas_i_kronor_inte_antal_aktier():
    """Yahoos averageVolume är styckantal och duger inte som likviditetsmått."""
    p = YfinanceProvider(handelsdagar_for_omsattning=60)
    bd = Bolagsdata(ticker="TEST.ST", hamtad=IDAG)
    p._marknadsdata(bd, {"currentPrice": 100.0}, kurshistorik(kurs=100.0, volym=50_000.0))
    assert bd.varde("daglig_omsattning") == 100.0 * 50_000.0


def test_kort_kurshistorik_markeras_saknad():
    """Under 30 handelsdagar går inte momentum att räkna."""
    p = YfinanceProvider()
    bd = Bolagsdata(ticker="NY.ST", hamtad=IDAG)
    p._marknadsdata(bd, {"currentPrice": 50.0}, kurshistorik(dagar=20))
    assert not bd.har("kurshistorik")
    assert "handelsdagar" in bd.saknade["kurshistorik"]
    assert bd.varde("kurs") == 50.0  # kursen finns ändå


def test_kurs_faller_tillbaka_pa_kurshistoriken():
    p = YfinanceProvider()
    bd = Bolagsdata(ticker="TEST.ST", hamtad=IDAG)
    historik = kurshistorik(dagar=100)
    historik.iloc[-1, historik.columns.get_loc("Close")] = 123.5
    p._marknadsdata(bd, {}, historik)
    assert bd.varde("kurs") == 123.5
    assert bd.kallor["kurs"] == "yfinance:kurshistorik"


def test_ingen_kurshistorik_markerar_bada_falten():
    p = YfinanceProvider()
    bd = Bolagsdata(ticker="TEST.ST", hamtad=IDAG)
    p._marknadsdata(bd, {"currentPrice": 10.0}, None)
    assert "kurshistorik" in bd.saknade
    assert "kurshistorik" in bd.saknade["daglig_omsattning"]


# =====================================================================
#  Balansräkning: reservvägar
# =====================================================================
def test_nettoskuld_raknas_ur_skulder_minus_kassa():
    p = YfinanceProvider()
    bd = Bolagsdata(ticker="TEST.ST", hamtad=IDAG)
    balans = arsrapport({
        "Stockholders Equity": [100.0, 90.0, 80.0, 70.0],
        "Total Debt": [50.0, 45.0, 40.0, 35.0],
        "Cash And Cash Equivalents": [20.0, 18.0, 15.0, 12.0],
    })
    p._balansdata(bd, {}, balans)
    assert bd.varde("nettoskuld") == 30.0
    assert "beräknat" in bd.kallor["nettoskuld"]


def test_investerat_kapital_raknas_nar_raden_saknas():
    p = YfinanceProvider()
    bd = Bolagsdata(ticker="TEST.ST", hamtad=IDAG)
    balans = arsrapport({
        "Stockholders Equity": [100.0, 90.0, 80.0, 70.0],
        "Total Debt": [50.0, 45.0, 40.0, 35.0],
    })
    p._balansdata(bd, {}, balans)
    assert bd.varde("investerat_kapital") == 150.0


def test_balansdata_som_saknas_helt_markeras_med_orsak():
    p = YfinanceProvider()
    bd = Bolagsdata(ticker="TEST.ST", hamtad=IDAG)
    p._balansdata(bd, {}, pd.DataFrame())
    for falt in ("eget_kapital", "nettoskuld", "investerat_kapital", "bolagsvarde"):
        assert falt in bd.saknade, falt
        assert bd.saknade[falt]


def test_bolagsvarde_raknas_ur_borsvarde_och_nettoskuld():
    p = YfinanceProvider()
    bd = Bolagsdata(ticker="TEST.ST", hamtad=IDAG)
    bd.satt("borsvarde", 600e9, "test")
    balans = arsrapport({"Net Debt": [174e9, 160e9, 150e9, 140e9]})
    p._balansdata(bd, {}, balans)
    assert bd.varde("bolagsvarde") == 774e9


# =====================================================================
#  Resultaträkning
# =====================================================================
def test_resultatdata_satter_bade_senaste_aret_och_historiken():
    p = YfinanceProvider(ar_historik=5)
    bd = Bolagsdata(ticker="TEST.ST", hamtad=IDAG)
    resultat = arsrapport({
        "Total Revenue": [479.0, 526.0, 473.0, 372.0],
        "Operating Income": [49.0, 69.0, 77.0, 61.0],
        "Net Income": [34.0, 50.0, 56.0, 45.0],
    })
    p._resultatdata(bd, {"trailingEps": 19.21}, resultat)

    assert bd.varde("rorelseresultat") == 49.0
    assert len(bd.varde("rorelseresultat_historik")) == 4
    assert bd.varde("vinst_per_aktie") == 19.21


def test_historiken_kapas_till_konfigurerat_antal_ar():
    p = YfinanceProvider(ar_historik=3)
    bd = Bolagsdata(ticker="TEST.ST", hamtad=IDAG)
    p._resultatdata(bd, {}, arsrapport({"Operating Income": [1.0, 2.0, 3.0, 4.0]}))
    assert len(bd.varde("rorelseresultat_historik")) == 3


def test_saknad_resultatrakning_markerar_falten():
    p = YfinanceProvider()
    bd = Bolagsdata(ticker="TEST.ST", hamtad=IDAG)
    p._resultatdata(bd, {"ebitda": 71e9, "trailingEps": 5.0}, None)
    assert "rorelseresultat" in bd.saknade
    assert bd.varde("ebitda") == 71e9  # info-värdet används ändå


def test_ebitda_faller_tillbaka_pa_info():
    """Banker saknar EBITDA i resultaträkningen -- det är väntat."""
    p = YfinanceProvider()
    bd = Bolagsdata(ticker="SEB-A.ST", hamtad=IDAG)
    p._resultatdata(bd, {"ebitda": None}, arsrapport({"Net Income": [1.0, 2.0, 3.0, 4.0]}))
    assert not bd.har("ebitda")
    assert "ebitda" in bd.saknade


# =====================================================================
#  Omförsök
# =====================================================================
def test_forsok_ger_upp_efter_max_antal():
    p = YfinanceProvider(max_forsok=3, paus_sek=0)
    bd = Bolagsdata(ticker="TEST.ST", hamtad=IDAG)
    anrop = []

    def kraschar():
        anrop.append(1)
        raise ConnectionError("nätet nere")

    assert p._forsok("info", kraschar, bd) is None
    assert len(anrop) == 3
    assert any("ConnectionError" in f for f in bd.fel)


def test_forsok_lyckas_pa_andra_forsoket():
    p = YfinanceProvider(max_forsok=3, paus_sek=0)
    bd = Bolagsdata(ticker="TEST.ST", hamtad=IDAG)
    anrop = []

    def ibland():
        anrop.append(1)
        if len(anrop) == 1:
            raise TimeoutError("långsamt")
        return {"currentPrice": 100.0, "marketCap": 1e9}

    assert p._forsok("info", ibland, bd)["currentPrice"] == 100.0
    assert bd.fel == []


def test_tomt_svar_raknas_som_misslyckande():
    p = YfinanceProvider(max_forsok=2, paus_sek=0)
    bd = Bolagsdata(ticker="TEST.ST", hamtad=IDAG)
    assert p._forsok("resultatrakning", lambda: pd.DataFrame(), bd) is None
    assert any("tomt svar" in f for f in bd.fel)
