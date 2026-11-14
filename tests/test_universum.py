"""Tester för urvalet: vilka bolag som får vara med och varför inte.

Tyngdpunkten ligger på två saker som är lätta att få fel:

* Saknas ett mått för ALLA bolag i en kategori ska måttet falla bort ur
  modellen, inte bolagen ur kategorin.
* Nuvarande innehav får aldrig uteslutas tyst. Att ett innehav krympt
  under gränsen är i sig något som ska stå i rapporten.
"""

from __future__ import annotations

import datetime as dt

import pandas as pd
import pytest

from aktieanalys.data.bolagsdata import Bolagsdata
from aktieanalys.konfig import fran_dictar
from aktieanalys.universum import MDR, MKR, valj_urval

IDAG = dt.date(2026, 11, 13)


def bolag(
    ticker: str,
    borsvarde: float = 50 * MDR,
    omsattning: float = 100 * MKR,
    valuta: str = "SEK",
    komplett: bool = True,
    utan: tuple[str, ...] = (),
) -> Bolagsdata:
    """Ett bolag med full standarduppsättning, minus det som anges i utan."""
    bd = Bolagsdata(ticker=ticker, hamtad=IDAG, namn=ticker, valuta=valuta,
                    rapportvaluta=valuta)
    varden = {
        "borsvarde": borsvarde,
        "daglig_omsattning": omsattning,
        "kurs": 100.0,
    }
    if komplett:
        varden |= {
            "rorelseresultat": 5e9,
            "investerat_kapital": 30e9,
            "rorelseresultat_historik": [(2025, 5e9), (2024, 4.5e9), (2023, 4e9)],
            "omsattning_historik": [(2025, 50e9), (2024, 48e9), (2023, 45e9)],
            "bolagsvarde": 55e9,
            "vinst_per_aktie": 10.0,
            "vinst_per_aktie_historik": [(2025, 10.0), (2024, 9.0), (2023, 8.0)],
            "nettoskuld": 5e9,
            "ebitda": 8e9,
            "antal_aktier": 500e6,
            "kurshistorik": pd.Series(
                [100.0] * 300, index=pd.bdate_range(end="2026-11-13", periods=300)
            ),
        }
    for falt in utan:
        varden.pop(falt, None)
    for falt, v in varden.items():
        bd.satt(falt, v, "test")
    return bd


@pytest.fixture
def enkel(config_dict, universum_dict, portfolj_dict, tmp_path):
    """Ett litet universum: en kategori med sex standardbolag."""
    universum_dict["kategorier"] = {
        "Industri": {
            "mattuppsattning": "standard",
            "bolag": [f"B{i}.ST" for i in range(1, 7)],
        }
    }
    portfolj_dict["innehav"] = {"Industri": {"ticker": "", "kopdatum": ""}}
    return fran_dictar(config_dict, universum_dict, portfolj_dict, rotkatalog=tmp_path)


def alla(tickers, **kw) -> dict[str, Bolagsdata]:
    return {t: bolag(t, **kw) for t in tickers}


# =====================================================================
#  Spärr 1: storlek och likviditet
# =====================================================================
def test_komplett_universum_slapper_igenom_alla(enkel):
    urval = valj_urval(enkel, alla(enkel.alla_tickers()), IDAG)
    ku = urval.kategorier["Industri"]
    assert ku.antal == 6
    assert ku.uteslutna == []
    assert ku.rangordningsbar


def test_for_litet_borsvarde_utesluter(enkel):
    data = alla(enkel.alla_tickers())
    data["B1.ST"] = bolag("B1.ST", borsvarde=3 * MDR)
    ku = valj_urval(enkel, data, IDAG).kategorier["Industri"]

    assert "B1.ST" not in ku.med
    assert len(ku.uteslutna) == 1
    assert "3.0 mdr kr, under gränsen 5 mdr" in ku.uteslutna[0].orsak


def test_for_lag_omsattning_utesluter(enkel):
    data = alla(enkel.alla_tickers())
    data["B2.ST"] = bolag("B2.ST", omsattning=4 * MKR)
    ku = valj_urval(enkel, data, IDAG).kategorier["Industri"]

    assert "B2.ST" not in ku.med
    assert "4.0 mkr/dag, under gränsen 10 mkr" in ku.uteslutna[0].orsak


def test_bada_orsakerna_redovisas(enkel):
    data = alla(enkel.alla_tickers())
    data["B3.ST"] = bolag("B3.ST", borsvarde=1 * MDR, omsattning=1 * MKR)
    ku = valj_urval(enkel, data, IDAG).kategorier["Industri"]
    assert len(ku.uteslutna[0].orsaker) == 2


def test_saknat_borsvarde_utesluter_med_tydlig_orsak(enkel):
    data = alla(enkel.alla_tickers())
    data["B4.ST"] = bolag("B4.ST", utan=("borsvarde",))
    data["B4.ST"].markera_saknat("borsvarde", "tomt svar")
    ku = valj_urval(enkel, data, IDAG).kategorier["Industri"]
    assert "börsvärde saknas" in ku.uteslutna[0].orsak


def test_annan_handelsvaluta_utesluter(enkel):
    """Gränserna är satta i kronor och går inte att tillämpa på euro."""
    data = alla(enkel.alla_tickers())
    data["B5.ST"] = bolag("B5.ST", valuta="EUR")
    ku = valj_urval(enkel, data, IDAG).kategorier["Industri"]
    assert "handlas i EUR" in ku.uteslutna[0].orsak


def test_bolag_utan_data_hamnar_i_utan_data(enkel):
    data = alla(enkel.alla_tickers())
    del data["B6.ST"]
    urval = valj_urval(enkel, data, IDAG)
    assert "B6.ST" in urval.utan_data
    assert "hamta-data" in urval.utan_data["B6.ST"]
    assert "B6.ST" not in urval.kategorier["Industri"].med


# =====================================================================
#  Spärr 2: måttet faller bort, inte bolagen
# =====================================================================
def test_matt_som_ingen_har_valjs_bort_men_bolagen_blir_kvar(enkel):
    """Det här är kärnan i hela modulen. Har ingen bank kapitaltäckning
    ska måttet falla bort -- inte alla banker."""
    data = alla(enkel.alla_tickers(), utan=("investerat_kapital",))
    ku = valj_urval(enkel, data, IDAG).kategorier["Industri"]

    assert ku.antal == 6, "bolagen ska vara kvar"
    assert "roic" in ku.bortvalda_matt
    assert "inget bolag i kategorin har data" in ku.bortvalda_matt["roic"]
    assert "investerat_kapital" in ku.bortvalda_matt["roic"]
    assert "roic" not in ku.anvandbara_matt
    assert ku.rangordningsbar, "resten av måtten går fortfarande att använda"


def test_matt_som_for_fa_har_valjs_bort(enkel):
    """min_bolag_for_rank är 4: en rank bland tre bolag säger för lite."""
    data = alla(enkel.alla_tickers(), utan=("investerat_kapital",))
    for t in ["B1.ST", "B2.ST", "B3.ST"]:
        data[t] = bolag(t)
    ku = valj_urval(enkel, data, IDAG).kategorier["Industri"]

    assert "roic" in ku.bortvalda_matt
    assert "bara 3 av 6 bolag" in ku.bortvalda_matt["roic"]


def test_matt_som_precis_tillrackligt_manga_har_behalls(enkel):
    data = alla(enkel.alla_tickers(), utan=("investerat_kapital",))
    for t in ["B1.ST", "B2.ST", "B3.ST", "B4.ST"]:
        data[t] = bolag(t)
    ku = valj_urval(enkel, data, IDAG).kategorier["Industri"]
    assert "roic" in ku.anvandbara_matt


def test_tom_pelare_pekas_ut(enkel):
    """Faller alla mått i en pelare bort kan den delen av poängen inte sättas."""
    data = alla(enkel.alla_tickers(), utan=("kurshistorik",))
    ku = valj_urval(enkel, data, IDAG).kategorier["Industri"]
    assert "momentum" in ku.saknade_pelare(enkel)
    assert "kvalitet" not in ku.saknade_pelare(enkel)


def test_kategori_utan_anvandbara_matt_ar_inte_rangordningsbar(enkel):
    data = alla(enkel.alla_tickers(), komplett=False)
    ku = valj_urval(enkel, data, IDAG).kategorier["Industri"]
    assert ku.anvandbara_matt == []
    assert not ku.rangordningsbar
    assert len(ku.saknade_pelare(enkel)) == 4


# =====================================================================
#  Spärr 3: bolag som saknar data andra har
# =====================================================================
def test_bolag_med_for_manga_saknade_matt_utesluts(enkel):
    """max_saknade_matt_per_bolag är 2."""
    data = alla(enkel.alla_tickers())
    data["B1.ST"] = bolag(
        "B1.ST",
        utan=("investerat_kapital", "bolagsvarde", "vinst_per_aktie", "nettoskuld"),
    )
    ku = valj_urval(enkel, data, IDAG).kategorier["Industri"]

    assert "B1.ST" not in ku.med
    uteslutning = next(u for u in ku.uteslutna if u.ticker == "B1.ST")
    assert "högst 2 tillåts" in uteslutning.orsak


def test_bolag_med_fa_saknade_matt_blir_kvar(enkel):
    """Ett eller två saknade mått är tillåtet -- poängmodellen hanterar det."""
    data = alla(enkel.alla_tickers())
    data["B1.ST"] = bolag("B1.ST", utan=("nettoskuld",))
    ku = valj_urval(enkel, data, IDAG).kategorier["Industri"]
    assert "B1.ST" in ku.med


# =====================================================================
#  Innehavet utesluts aldrig
# =====================================================================
@pytest.fixture
def med_innehav(config_dict, universum_dict, portfolj_dict, tmp_path):
    universum_dict["kategorier"] = {
        "Industri": {
            "mattuppsattning": "standard",
            "bolag": [f"B{i}.ST" for i in range(1, 7)],
        }
    }
    portfolj_dict["innehav"] = {
        "Industri": {"ticker": "B1.ST", "kopdatum": "2024-01-15",
                     "antal": 100, "anskaffningskurs": 90.0}
    }
    return fran_dictar(config_dict, universum_dict, portfolj_dict, rotkatalog=tmp_path)


def test_innehav_som_krympt_under_gransen_utesluts_inte(med_innehav):
    """Att innehavet blivit för litet ska du få VETA, inte slippa se."""
    data = alla(med_innehav.alla_tickers())
    data["B1.ST"] = bolag("B1.ST", borsvarde=2 * MDR)
    ku = valj_urval(med_innehav, data, IDAG).kategorier["Industri"]

    assert "B1.ST" in ku.med
    assert not any(u.ticker == "B1.ST" for u in ku.uteslutna)
    assert any("INNEHAVET klarar inte längre" in v for v in ku.innehav_varningar)
    assert any("2.0 mdr" in v for v in ku.innehav_varningar)


def test_innehav_med_for_lite_data_utesluts_inte_men_varnas(med_innehav):
    data = alla(med_innehav.alla_tickers())
    data["B1.ST"] = bolag(
        "B1.ST",
        utan=("investerat_kapital", "bolagsvarde", "vinst_per_aktie", "nettoskuld"),
    )
    ku = valj_urval(med_innehav, data, IDAG).kategorier["Industri"]

    assert "B1.ST" in ku.med
    assert any("ofullständigt underlag" in v for v in ku.innehav_varningar)


def test_innehav_helt_utan_data_varnas(med_innehav):
    data = alla(med_innehav.alla_tickers())
    del data["B1.ST"]
    ku = valj_urval(med_innehav, data, IDAG).kategorier["Industri"]

    assert any("saknar data helt" in v for v in ku.innehav_varningar)
    assert "B1.ST" in valj_urval(med_innehav, data, IDAG).utan_data


def test_friskt_innehav_ger_inga_varningar(med_innehav):
    ku = valj_urval(med_innehav, alla(med_innehav.alla_tickers()), IDAG).kategorier["Industri"]
    assert ku.innehav == "B1.ST"
    assert ku.innehav_varningar == []


# =====================================================================
#  Sammanfattning och det verkliga universumet
# =====================================================================
def test_sammanfattning_namnger_problemen(enkel):
    data = alla(enkel.alla_tickers(), komplett=False)
    urval = valj_urval(enkel, data, IDAG)
    text = urval.sammanfattning()
    assert "går inte att rangordna" in text


def test_urvalet_pa_verklig_data():
    """Mot den riktiga konfigurationen och den riktiga databasen, om den finns."""
    from aktieanalys.konfig import las_installningar
    from aktieanalys.universum import las_och_valj
    from tests.conftest import KONFIGKATALOG, ROT

    inst = las_installningar(KONFIGKATALOG)
    if not inst.databasvag.exists():
        pytest.skip("ingen databas -- kör hamta-data först")

    urval, data = las_och_valj(inst)
    assert urval.antal_med > 40
    # De fyra kategorier som har full automatisk täckning ska vara
    # rangordningsbara utan manuell komplettering.
    for kategori in ["Industri", "Hälsovård", "Konsument", "Teknik"]:
        ku = urval.kategorier[kategori]
        assert ku.rangordningsbar, kategori
        assert len(ku.anvandbara_matt) == 7, kategori
        assert ku.saknade_pelare(inst) == [], kategori
    # Varje uteslutning ska ha en orsak i klartext.
    for u in urval.alla_uteslutna():
        assert u.orsaker and all(len(o) > 10 for o in u.orsaker)
