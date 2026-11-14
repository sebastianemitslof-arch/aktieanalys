"""Tester för formlerna som gör rådata till nyckeltal.

Tyngdpunkten ligger på spärrarna. Ett negativt EV/EBIT ser billigt ut
men betyder rörelseförlust; ett negativt P/E likaså. Sådana tal måste
bli None, annars rankas förlustbolag som de billigaste i kategorin.
"""

from __future__ import annotations

import datetime as dt

import pandas as pd
import pytest

from aktieanalys.data.bolagsdata import Bolagsdata
from aktieanalys.poang.berakning import BERAKNINGAR, berakna

IDAG = dt.date(2026, 11, 13)
MIN_AR = 3


def bolag(**varden) -> Bolagsdata:
    bd = Bolagsdata(ticker="TEST.ST", hamtad=IDAG)
    for f, v in varden.items():
        bd.satt(f, v, "test")
    return bd


def kursserie(dagar: int = 400, start: float = 100.0, slut: float = 130.0):
    datum = pd.bdate_range(end="2026-11-13", periods=dagar)
    steg = (slut - start) / (dagar - 1)
    return pd.Series([start + steg * i for i in range(dagar)], index=datum)


# =====================================================================
#  Kvalitet
# =====================================================================
def test_roic():
    bd = bolag(rorelseresultat=34_811e6, investerat_kapital=137_661e6)
    assert berakna("roic", bd, MIN_AR) == pytest.approx(0.2529, abs=0.0001)


def test_roic_utan_investerat_kapital_ger_none():
    assert berakna("roic", bolag(rorelseresultat=1e9), MIN_AR) is None


def test_roic_med_negativt_kapital_ger_none():
    """Negativt eget kapital ger en ROIC som ser fantastisk ut."""
    bd = bolag(rorelseresultat=1e9, investerat_kapital=-5e9)
    assert berakna("roic", bd, MIN_AR) is None


def test_marginalstabilitet_hogre_for_jamnare_marginal():
    jamn = bolag(
        rorelseresultat_historik=[(2025, 10.0), (2024, 10.2), (2023, 9.8)],
        omsattning_historik=[(2025, 100.0), (2024, 100.0), (2023, 100.0)],
    )
    ojamn = bolag(
        rorelseresultat_historik=[(2025, 15.0), (2024, 5.0), (2023, 10.0)],
        omsattning_historik=[(2025, 100.0), (2024, 100.0), (2023, 100.0)],
    )
    assert berakna("rorelsemarginal_stabilitet_5ar", jamn, MIN_AR) > \
           berakna("rorelsemarginal_stabilitet_5ar", ojamn, MIN_AR)


def test_marginalstabilitet_kraver_tillrackligt_manga_ar():
    bd = bolag(
        rorelseresultat_historik=[(2025, 10.0), (2024, 10.0)],
        omsattning_historik=[(2025, 100.0), (2024, 100.0)],
    )
    assert berakna("rorelsemarginal_stabilitet_5ar", bd, MIN_AR) is None


def test_marginalstabilitet_hoppar_over_ar_utan_omsattning():
    bd = bolag(
        rorelseresultat_historik=[(2025, 10.0), (2024, 10.0), (2023, 10.0), (2022, 5.0)],
        omsattning_historik=[(2025, 100.0), (2024, 100.0), (2023, 100.0)],
    )
    assert berakna("rorelsemarginal_stabilitet_5ar", bd, MIN_AR) is not None


def test_vinststabilitet_tal_bolag_av_olika_storlek():
    """Variationskoefficient, inte absolut spridning -- annars hade stora
    bolag alltid sett ostabila ut."""
    litet = bolag(nettoresultat_historik=[(2025, 100.0), (2024, 110.0), (2023, 90.0)])
    stort = bolag(nettoresultat_historik=[(2025, 100e9), (2024, 110e9), (2023, 90e9)])
    assert berakna("vinststabilitet_5ar", litet, MIN_AR) == pytest.approx(
        berakna("vinststabilitet_5ar", stort, MIN_AR)
    )


def test_substansvarde_tillvaxt_ar_arlig_takt():
    bd = bolag(substansvarde_per_aktie_historik=[
        (2025, 121.0), (2024, 110.0), (2023, 100.0)])
    assert berakna("substansvarde_tillvaxt", bd, MIN_AR) == pytest.approx(0.10, abs=0.001)


def test_tillvaxt_fran_negativt_startvarde_ger_none():
    bd = bolag(substansvarde_per_aktie_historik=[
        (2025, 50.0), (2024, 10.0), (2023, -20.0)])
    assert berakna("substansvarde_tillvaxt", bd, MIN_AR) is None


# =====================================================================
#  Värdering -- spärrarna
# =====================================================================
def test_ev_ebit():
    bd = bolag(bolagsvarde=960_260e6, rorelseresultat=34_811e6)
    assert berakna("ev_ebit", bd, MIN_AR) == pytest.approx(27.58, abs=0.01)


def test_ev_ebit_vid_rorelseforlust_ger_none():
    """En negativ EV/EBIT ser låg ut men betyder förlust."""
    bd = bolag(bolagsvarde=100e9, rorelseresultat=-5e9)
    assert berakna("ev_ebit", bd, MIN_AR) is None


def test_pe_vid_forlust_ger_none():
    assert berakna("pe", bolag(kurs=100.0, vinst_per_aktie=-5.0), MIN_AR) is None


def test_pe_vid_nollvinst_ger_none():
    assert berakna("pe", bolag(kurs=100.0, vinst_per_aktie=0.0), MIN_AR) is None


def test_p_b_vid_negativt_eget_kapital_ger_none():
    bd = bolag(kurs=100.0, eget_kapital_per_aktie=-20.0)
    assert berakna("p_b", bd, MIN_AR) is None


def test_kurs_mot_substans():
    bd = bolag(kurs=134.7, substansvarde_per_aktie=159.1)
    assert berakna("kurs_mot_substans", bd, MIN_AR) == pytest.approx(0.847, abs=0.001)


# =====================================================================
#  Jämförelse med bolagets egen historik
# =====================================================================
def test_pe_mot_egen_historik_under_ett_nar_bolaget_ar_billigare_an_vanligt():
    """Kursen är 100 i dag med vinst 10 (P/E 10). Historiskt låg kursen
    också runt 100 men vinsten var 5 (P/E 20). Alltså halva den
    normala värderingen."""
    bd = bolag(
        kurs=100.0,
        vinst_per_aktie=10.0,
        vinst_per_aktie_historik=[(2025, 5.0), (2024, 5.0), (2023, 5.0)],
        kurshistorik=kursserie(dagar=900, start=100.0, slut=100.0),
    )
    assert berakna("pe_mot_egen_historik", bd, MIN_AR) == pytest.approx(0.5, abs=0.02)


def test_historik_anvander_median_inte_medelvarde():
    """Ett enstaka extremår ska inte flytta bolagets normala värdering."""
    med_extremar = bolag(
        kurs=100.0,
        vinst_per_aktie=10.0,
        vinst_per_aktie_historik=[(2025, 10.0), (2024, 10.0), (2023, 0.5)],
        kurshistorik=kursserie(dagar=900, start=100.0, slut=100.0),
    )
    # Median av [10, 10, 200] = 10 -> kvot 1.0. Medelvärde hade gett 0.07.
    assert berakna("pe_mot_egen_historik", med_extremar, MIN_AR) == pytest.approx(
        1.0, abs=0.05
    )


def test_historik_kraver_tillrackligt_manga_ar():
    bd = bolag(
        kurs=100.0,
        vinst_per_aktie=10.0,
        vinst_per_aktie_historik=[(2025, 5.0)],
        kurshistorik=kursserie(dagar=900),
    )
    assert berakna("pe_mot_egen_historik", bd, MIN_AR) is None


def test_historik_hoppar_over_forlustar():
    bd = bolag(
        kurs=100.0,
        vinst_per_aktie=10.0,
        vinst_per_aktie_historik=[
            (2025, 10.0), (2024, 10.0), (2023, 10.0), (2022, -5.0)],
        kurshistorik=kursserie(dagar=1200, start=100.0, slut=100.0),
    )
    assert berakna("pe_mot_egen_historik", bd, MIN_AR) == pytest.approx(1.0, abs=0.05)


# =====================================================================
#  Finansiell styrka
# =====================================================================
def test_nettoskuld_ebitda():
    bd = bolag(nettoskuld=11_932e6, ebitda=44_082e6)
    assert berakna("nettoskuld_ebitda", bd, MIN_AR) == pytest.approx(0.271, abs=0.001)


def test_nettokassa_ger_negativt_tal():
    """Negativ nettoskuld är bra, och måttet är märkt lägre-är-bättre."""
    bd = bolag(nettoskuld=-20e9, ebitda=10e9)
    assert berakna("nettoskuld_ebitda", bd, MIN_AR) == -2.0


def test_nettoskuld_ebitda_vid_negativ_ebitda_ger_none():
    bd = bolag(nettoskuld=10e9, ebitda=-2e9)
    assert berakna("nettoskuld_ebitda", bd, MIN_AR) is None


def test_kapitalstyrka_tas_rakt_av():
    assert berakna("kapitalstyrka", bolag(cet1_kvot=0.171), MIN_AR) == 0.171


# =====================================================================
#  Momentum
# =====================================================================
def test_momentum_utesluter_senaste_manaden():
    """Serien stiger till och med dag -40, sedan faller den brant. Den
    sista månadens fall ska inte synas i momentum."""
    datum = pd.bdate_range(end="2026-11-13", periods=400)
    varden = [100.0 + i * 0.1 for i in range(380)] + [50.0] * 20
    bd = bolag(kurshistorik=pd.Series(varden, index=datum))
    m = berakna("momentum_12m_exkl_1m", bd, MIN_AR)
    assert m > 0, "kraschen i sista månaden ska inte dra ner momentum"


def test_momentum_pa_stigande_kurs_ar_positivt():
    bd = bolag(kurshistorik=kursserie(dagar=400, start=100.0, slut=130.0))
    assert berakna("momentum_12m_exkl_1m", bd, MIN_AR) > 0


def test_momentum_pa_fallande_kurs_ar_negativt():
    bd = bolag(kurshistorik=kursserie(dagar=400, start=130.0, slut=100.0))
    assert berakna("momentum_12m_exkl_1m", bd, MIN_AR) < 0


def test_momentum_kraver_tillrackligt_lang_historik():
    """Ett nyligen noterat bolag har inget 12-månadersmomentum."""
    bd = bolag(kurshistorik=kursserie(dagar=200))
    assert berakna("momentum_12m_exkl_1m", bd, MIN_AR) is None


def test_momentum_utan_kurshistorik_ger_none():
    assert berakna("momentum_12m_exkl_1m", bolag(kurs=100.0), MIN_AR) is None


# =====================================================================
#  Robusthet
# =====================================================================
def test_alla_matt_klarar_ett_tomt_bolag():
    """Ingen formel får krascha på ett bolag utan data."""
    tomt = Bolagsdata(ticker="TOM.ST", hamtad=IDAG)
    for mattnamn in BERAKNINGAR:
        assert berakna(mattnamn, tomt, MIN_AR) is None, mattnamn


def test_okant_matt_hojer_fel():
    with pytest.raises(KeyError):
        berakna("hittepa", bolag(), MIN_AR)


def test_fel_i_formel_ger_none_i_stallet_for_krasch(monkeypatch):
    """Ett oväntat fel får stoppa ett mått, aldrig veckokörningen."""
    def kraschar(bd, min_ar):
        raise ValueError("simulerat fel")

    monkeypatch.setitem(BERAKNINGAR, "roic", kraschar)
    assert berakna("roic", bolag(), MIN_AR) is None
