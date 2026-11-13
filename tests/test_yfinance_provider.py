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
from aktieanalys.data.valuta import Valutaomraknare
from aktieanalys.data.yfinance_provider import (
    YfinanceProvider,
    _Vaxling,
    _arsserie,
    _senaste,
    _senaste_med_ar,
    _tal,
)

KURSER = {"USD": 10.0, "EUR": 11.0}


def vaxl(bd, rapportvaluta="SEK"):
    """Växling med fasta valutakurser -- inga nätanrop i testerna."""
    return _Vaxling(bd, rapportvaluta, Valutaomraknare(fasta_kurser=KURSER))

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
    assert any("räknas om till kronor" in f for f in bd.fel)


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
    p._balansdata(bd, {}, balans, vaxl(bd))
    assert bd.varde("nettoskuld") == 30.0
    assert "beräknat" in bd.kallor["nettoskuld"]


def test_investerat_kapital_raknas_nar_raden_saknas():
    p = YfinanceProvider()
    bd = Bolagsdata(ticker="TEST.ST", hamtad=IDAG)
    balans = arsrapport({
        "Stockholders Equity": [100.0, 90.0, 80.0, 70.0],
        "Total Debt": [50.0, 45.0, 40.0, 35.0],
    })
    p._balansdata(bd, {}, balans, vaxl(bd))
    assert bd.varde("investerat_kapital") == 150.0


def test_balansdata_som_saknas_helt_markeras_med_orsak():
    p = YfinanceProvider()
    bd = Bolagsdata(ticker="TEST.ST", hamtad=IDAG)
    p._balansdata(bd, {}, pd.DataFrame(), vaxl(bd))
    for falt in ("eget_kapital", "nettoskuld", "investerat_kapital", "bolagsvarde"):
        assert falt in bd.saknade, falt
        assert bd.saknade[falt]


def test_bolagsvarde_raknas_ur_borsvarde_och_nettoskuld():
    p = YfinanceProvider()
    bd = Bolagsdata(ticker="TEST.ST", hamtad=IDAG)
    bd.satt("borsvarde", 600e9, "test")
    balans = arsrapport({"Net Debt": [174e9, 160e9, 150e9, 140e9]})
    p._balansdata(bd, {}, balans, vaxl(bd))
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
    p._resultatdata(bd, {"trailingEps": 19.21}, resultat, vaxl(bd))

    assert bd.varde("rorelseresultat") == 49.0
    assert len(bd.varde("rorelseresultat_historik")) == 4
    assert bd.varde("vinst_per_aktie") == 19.21


def test_historiken_kapas_till_konfigurerat_antal_ar():
    p = YfinanceProvider(ar_historik=3)
    bd = Bolagsdata(ticker="TEST.ST", hamtad=IDAG)
    p._resultatdata(bd, {}, arsrapport({"Operating Income": [1.0, 2.0, 3.0, 4.0]}),
                    vaxl(bd))
    assert len(bd.varde("rorelseresultat_historik")) == 3


def test_saknad_resultatrakning_markerar_falten():
    p = YfinanceProvider()
    bd = Bolagsdata(ticker="TEST.ST", hamtad=IDAG)
    p._resultatdata(bd, {"ebitda": 71e9, "trailingEps": 5.0}, None, vaxl(bd))
    assert "rorelseresultat" in bd.saknade
    assert bd.varde("ebitda") == 71e9  # info-värdet används ändå


def test_ebitda_faller_tillbaka_pa_info():
    """Banker saknar EBITDA i resultaträkningen -- det är väntat."""
    p = YfinanceProvider()
    bd = Bolagsdata(ticker="SEB-A.ST", hamtad=IDAG)
    p._resultatdata(bd, {"ebitda": None},
                    arsrapport({"Net Income": [1.0, 2.0, 3.0, 4.0]}), vaxl(bd))
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


# =====================================================================
#  Valutaomräkning
# =====================================================================
def test_rakenskaper_raknas_om_till_kronor():
    """AstraZeneca redovisar i dollar men handlas i kronor."""
    p = YfinanceProvider()
    bd = Bolagsdata(ticker="AZN.ST", hamtad=IDAG)
    balans = arsrapport({
        "Stockholders Equity": [48.667, 45.0, 40.0, 35.0],
        "Net Debt": [21.635, 20.0, 18.0, 16.0],
    })
    p._balansdata(bd, {}, balans, vaxl(bd, "USD"))

    assert bd.varde("eget_kapital") == 48.667 * 10.0
    assert bd.varde("nettoskuld") == 21.635 * 10.0
    assert "omräknat från USD" in bd.kallor["eget_kapital"]


def test_bookvalue_raknas_om():
    """Det här är felet som gör Yahoos eget priceToBook obrukbart."""
    p = YfinanceProvider()
    bd = Bolagsdata(ticker="AZN.ST", hamtad=IDAG)
    p._balansdata(bd, {"bookValue": 33.351}, pd.DataFrame(), vaxl(bd, "USD"))

    assert bd.varde("eget_kapital_per_aktie") == 33.351 * 10.0
    # Med kurs 1866 blir P/B ca 5.6 i stället för Yahoos 55.95
    assert 5.0 < 1866.0 / bd.varde("eget_kapital_per_aktie") < 6.0


def test_trailing_eps_raknas_INTE_om():
    """trailingEps är redan i handelsvalutan -- omräkning skulle bli fel."""
    p = YfinanceProvider()
    bd = Bolagsdata(ticker="AZN.ST", hamtad=IDAG)
    p._resultatdata(bd, {"trailingEps": 66.43}, None, vaxl(bd, "USD"))

    assert bd.varde("vinst_per_aktie") == 66.43
    assert "omräknat" not in bd.kallor["vinst_per_aktie"]


def test_enterprise_value_raknas_INTE_om():
    """enterpriseValue är redan i handelsvalutan (uppmätt mot börsvärdet)."""
    p = YfinanceProvider()
    bd = Bolagsdata(ticker="AZN.ST", hamtad=IDAG)
    p._balansdata(bd, {"enterpriseValue": 2.892e12}, pd.DataFrame(), vaxl(bd, "USD"))

    assert bd.varde("bolagsvarde") == 2.892e12


def test_antal_aktier_raknas_INTE_om():
    p = YfinanceProvider()
    bd = Bolagsdata(ticker="AZN.ST", hamtad=IDAG)
    p._balansdata(bd, {"sharesOutstanding": 1.55e9}, pd.DataFrame(), vaxl(bd, "USD"))
    assert bd.varde("antal_aktier") == 1.55e9


def test_arsserie_raknas_om_ar_for_ar():
    p = YfinanceProvider(ar_historik=4)
    bd = Bolagsdata(ticker="AZN.ST", hamtad=IDAG)
    resultat = arsrapport({"Operating Income": [13.3, 12.0, 11.0, 10.0]})
    p._resultatdata(bd, {}, resultat, vaxl(bd, "USD"))

    serie = bd.varde("rorelseresultat_historik")
    assert serie == [(2025, 133.0), (2024, 120.0), (2023, 110.0), (2022, 100.0)]


def test_senaste_aret_raknas_om_bara_en_gang():
    """Det enskilda talet tas ur den redan omräknade historiken."""
    p = YfinanceProvider()
    bd = Bolagsdata(ticker="AZN.ST", hamtad=IDAG)
    p._resultatdata(bd, {}, arsrapport({"Operating Income": [13.3, 12.0, 11.0, 10.0]}),
                    vaxl(bd, "USD"))
    assert bd.varde("rorelseresultat") == 133.0  # inte 1330.0


def test_bolagsvarde_blandar_inte_valutor():
    """Börsvärde är kronor, nettoskuld är rapportvaluta -- reservvägen
    måste använda den omräknade nettoskulden."""
    p = YfinanceProvider()
    bd = Bolagsdata(ticker="AZN.ST", hamtad=IDAG)
    bd.satt("borsvarde", 2894e9, "yfinance:info")  # redan kronor
    balans = arsrapport({"Net Debt": [21.635, 20.0, 18.0, 16.0]})
    p._balansdata(bd, {}, balans, vaxl(bd, "USD"))

    assert bd.varde("bolagsvarde") == 2894e9 + 21.635 * 10.0


def test_saknad_valutakurs_markerar_faltet_saknat():
    """Ett oomräknat tal är värre än inget tal."""
    p = YfinanceProvider()
    bd = Bolagsdata(ticker="XYZ.ST", hamtad=IDAG)
    tom = _Vaxling(bd, "GBP", Valutaomraknare(fasta_kurser={}))  # ingen GBP-kurs
    balans = arsrapport({"Stockholders Equity": [100.0, 90.0, 80.0, 70.0]})
    p._balansdata(bd, {}, balans, tom)

    assert not bd.har("eget_kapital")
    assert "kunde inte räknas om från GBP" in bd.saknade["eget_kapital"]


def test_svenska_bolag_rors_inte():
    p = YfinanceProvider()
    bd = Bolagsdata(ticker="VOLV-B.ST", hamtad=IDAG)
    balans = arsrapport({"Stockholders Equity": [178.395, 170.0, 160.0, 150.0]})
    p._balansdata(bd, {}, balans, vaxl(bd, "SEK"))

    assert bd.varde("eget_kapital") == 178.395
    assert "omräknat" not in bd.kallor["eget_kapital"]


def test_senaste_med_ar_ger_bade_ar_och_varde():
    df = arsrapport({"Total Debt": [174.0, 160.0, 150.0, 140.0]})
    assert _senaste_med_ar(df, "Total Debt") == (2025, 174.0)
    assert _senaste_med_ar(df, "Finns Inte") == (None, None)


# =====================================================================
#  Valutaomräknaren själv
# =====================================================================
def test_omraknare_ger_ett_for_kronor():
    assert Valutaomraknare(fasta_kurser={}).kurs("SEK") == 1.0
    assert Valutaomraknare(fasta_kurser={}).kurs(None) == 1.0


def test_omraknare_ger_none_for_okand_valuta():
    v = Valutaomraknare(fasta_kurser={"USD": 10.0})
    assert v.kurs("JPY") is None
    assert v.till_sek(100.0, "JPY") is None


def test_omraknare_slapper_igenom_none():
    assert Valutaomraknare(fasta_kurser={"USD": 10.0}).till_sek(None, "USD") is None


def test_omraknare_valjer_kurs_vid_arsslut():
    """Varje år ska räknas om med kursen som gällde vid det bokslutet."""
    serie = pd.Series(
        [9.0, 10.0, 11.0],
        index=pd.to_datetime(["2023-12-29", "2024-12-30", "2025-12-30"]),
    )
    v = Valutaomraknare()
    v._serier["USD"] = serie

    assert v.kurs("USD", 2023) == 9.0
    assert v.kurs("USD", 2024) == 10.0
    assert v.kurs("USD", 2025) == 11.0
    assert v.kurs("USD") == 11.0            # senaste kända
    assert v.kurs("USD", 2022) is None      # före vår historik


def test_okand_rapportvaluta_utesluter_rakenskaperna():
    """Att gissa SEK gav tysta tiopotensfel. Nu markeras fälten saknade."""
    p = YfinanceProvider()
    bd = Bolagsdata(ticker="AZN.ST", hamtad=IDAG)
    okand = _Vaxling(bd, None, Valutaomraknare(fasta_kurser=KURSER))
    balans = arsrapport({"Stockholders Equity": [48.667, 45.0, 40.0, 35.0]})
    p._balansdata(bd, {"bookValue": 33.351}, balans, okand)

    assert not bd.har("eget_kapital")
    assert not bd.har("eget_kapital_per_aktie")
    assert "rapportvalutan är okänd" in bd.saknade["eget_kapital"]


def test_okand_rapportvaluta_utesluter_arsserier():
    p = YfinanceProvider()
    bd = Bolagsdata(ticker="AZN.ST", hamtad=IDAG)
    okand = _Vaxling(bd, None, Valutaomraknare(fasta_kurser=KURSER))
    p._resultatdata(bd, {}, arsrapport({"Operating Income": [13.3, 12.0, 11.0, 10.0]}),
                    okand)

    assert not bd.har("rorelseresultat_historik")
    assert "rapportvalutan är okänd" in bd.saknade["rorelseresultat_historik"]


def test_kand_meta_anvands_nar_yahoo_utelamnar_valutan():
    """Bantade svar från Yahoo saknar ibland financialCurrency."""
    p = YfinanceProvider()
    bd = Bolagsdata(ticker="AZN.ST", hamtad=IDAG)
    # Simulerar hamta()-logiken: info utan financialCurrency
    info = {"currency": "SEK", "longName": "AstraZeneca PLC"}
    kand = {"namn": "AstraZeneca PLC", "valuta": "SEK", "rapportvaluta": "USD"}
    bd.rapportvaluta = info.get("financialCurrency") or kand.get("rapportvaluta")
    assert bd.rapportvaluta == "USD"

    vaxling = _Vaxling(bd, bd.rapportvaluta, Valutaomraknare(fasta_kurser=KURSER))
    assert vaxling.kand and vaxling.behovs


# =====================================================================
#  Tidszoner
# =====================================================================
def test_naiva_datum_behaller_ratt_dag():
    """tz_convert hade flyttat svensk midnatt till föregående dag."""
    from aktieanalys.data.valuta import naiva_datum

    serie = pd.Series(
        [100.0, 101.0],
        index=pd.to_datetime(["2026-11-12", "2026-11-13"]).tz_localize("Europe/Stockholm"),
    )
    naken = naiva_datum(serie)
    assert naken.index.tz is None
    assert [d.date().isoformat() for d in naken.index] == ["2026-11-12", "2026-11-13"]


def test_naiva_datum_ror_inte_omarkta_serier():
    from aktieanalys.data.valuta import naiva_datum

    serie = pd.Series([1.0], index=pd.to_datetime(["2026-11-13"]))
    assert naiva_datum(serie).index.tz is None


def test_naiva_datum_klarar_tom_serie():
    from aktieanalys.data.valuta import naiva_datum

    assert len(naiva_datum(pd.Series(dtype=float))) == 0


def test_valutakurs_fungerar_med_tidszonsmarkt_serie():
    """Det här felet tog ner en hel veckokörning: Yahoo märker
    valutapar med Europe/London, aktier med Europe/Stockholm."""
    from aktieanalys.data.valuta import naiva_datum

    serie = pd.Series(
        [10.0, 11.0],
        index=pd.to_datetime(["2024-12-30", "2025-12-30"]).tz_localize("Europe/London"),
    )
    v = Valutaomraknare()
    v._serier["EUR"] = serie          # som om den kom direkt från Yahoo
    assert v.kurs("EUR", 2025) == 11.0
    assert v.kurs("EUR", 2024) == 10.0


def test_kurshistoriken_lagras_utan_tidszon():
    p = YfinanceProvider()
    bd = Bolagsdata(ticker="TEST.ST", hamtad=IDAG)
    historik = kurshistorik(dagar=100)
    historik.index = historik.index.tz_localize("Europe/Stockholm")
    p._marknadsdata(bd, {"currentPrice": 100.0}, historik)
    assert bd.varde("kurshistorik").index.tz is None


# =====================================================================
#  Ett trasigt bolag får inte stoppa veckokörningen
# =====================================================================
def test_ovantat_fel_i_tolkningen_stoppar_bara_bolaget(monkeypatch):
    p = YfinanceProvider(max_forsok=1, paus_sek=0)

    def kraschar(*a, **kw):
        raise TypeError("simulerad pandas-krasch")

    monkeypatch.setattr(p, "_balansdata", kraschar)
    monkeypatch.setattr(p, "_forsok", lambda vad, anrop, bd: (
        {"currency": "SEK", "financialCurrency": "SEK", "currentPrice": 100.0,
         "longName": "Testbolaget"} if vad == "info" else None
    ))

    bd = p.hamta("TEST.ST", IDAG)   # ska INTE höja
    assert any("simulerad pandas-krasch" in f for f in bd.fel)
    assert bd.varde("kurs") == 100.0   # det som hann sättas finns kvar
