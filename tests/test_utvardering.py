"""Tester för IC-mätningen.

Den mäter om poängen säger något om efterföljande avkastning. Det
farliga med ett sådant mått är att det nästan alltid ger ett tal som
ser meningsfullt ut. Därför testas framför allt att bruset räknas och
redovisas, och att omdömet säger ifrån när värdet ligger inom det.
"""

from __future__ import annotations

import datetime as dt

import pytest

from aktieanalys.utvardering import (
    ICresultat,
    Period,
    _rangkorrelation,
    _slumpa_om,
)


def period(datum, ic, dagar=365):
    return Period(datum=datum, slutdatum=datum + dt.timedelta(days=dagar),
                  antal_bolag=50, ic=ic)


# =====================================================================
#  Rangkorrelationen
# =====================================================================
def test_perfekt_rangordning_ger_ett():
    assert _rangkorrelation([(1, 10), (2, 20), (3, 30), (4, 40), (5, 50)]) \
        == pytest.approx(1.0)


def test_omvand_rangordning_ger_minus_ett():
    assert _rangkorrelation([(1, 50), (2, 40), (3, 30), (4, 20), (5, 10)]) \
        == pytest.approx(-1.0)


def test_rangkorrelationen_bryr_sig_bara_om_ordningen():
    """Spearman, inte Pearson: extremvärden ska inte dominera."""
    assert _rangkorrelation([(1, 1), (2, 100), (3, 101), (4, 500), (5, 501)]) \
        == pytest.approx(1.0)


def test_for_fa_par_ger_none():
    assert _rangkorrelation([(1, 1), (2, 2)]) is None


def test_utan_spridning_ger_none():
    assert _rangkorrelation([(5, 1), (5, 2), (5, 3), (5, 4), (5, 5)]) is None


# =====================================================================
#  Bruset
# =====================================================================
def test_slumpad_poang_ger_ungefar_noll():
    import statistics

    par = [[(float(i), float(i) * 1.5) for i in range(12)]]
    utfall = _slumpa_om(par, antal=200)
    assert len(utfall) == 200
    assert abs(statistics.fmean(utfall)) < 0.15, "slumpad poäng ska ge noll i snitt"


def test_bruset_har_stor_spridning_vid_fa_bolag():
    """Med tio bolag per kategori är en enstaka IC nästan oläsbar."""
    import statistics

    par = [[(float(i), float((i * 7) % 10)) for i in range(10)]]
    utfall = _slumpa_om(par, antal=300)
    assert statistics.stdev(utfall) > 0.2


def test_nollband_kraver_tillrackligt_underlag():
    r = ICresultat(noll=[0.0] * 10)
    assert r.nollband() is None
    r.noll = [0.0] * 100
    assert r.nollband() is not None


def test_varde_inom_bruset_markeras():
    r = ICresultat(
        perioder=[period(dt.date(2024, 1, 1), 0.05),
                  period(dt.date(2025, 2, 1), 0.07)],
        noll=[x / 1000 for x in range(-300, 301)],
    )
    assert r.inom_bruset() is True
    assert "INOM bruset" in r.omdome()


def test_varde_utanfor_bruset_markeras():
    r = ICresultat(
        perioder=[period(dt.date(2024, 1, 1), 0.9),
                  period(dt.date(2025, 2, 1), 0.9)],
        noll=[x / 10000 for x in range(-300, 301)],
    )
    assert r.inom_bruset() is False
    assert "utanför" in r.omdome()


# =====================================================================
#  Oberoende perioder
# =====================================================================
def test_overlappande_perioder_rensas_bort():
    """Tre mätningar en månad isär delar nästan hela mätfönstret."""
    r = ICresultat(perioder=[
        period(dt.date(2024, 1, 1), 0.1),
        period(dt.date(2024, 2, 1), 0.1),
        period(dt.date(2024, 3, 1), 0.1),
        period(dt.date(2025, 1, 5), 0.1),
    ])
    assert len(r.perioder) == 4
    assert [p.datum for p in r.oberoende] == [
        dt.date(2024, 1, 1), dt.date(2025, 1, 5)]


def test_sammanfattningen_raknar_ratt():
    r = ICresultat(perioder=[
        period(dt.date(2024, 1, 1), 0.2),
        period(dt.date(2025, 2, 1), -0.1),
    ])
    s = r.sammanfatta()
    assert s["antal"] == 2
    assert s["medel"] == pytest.approx(0.05)
    assert s["andel_positiva"] == 50.0


# =====================================================================
#  Stabiliteten
# =====================================================================
def test_trog_rangordning_namns_i_omdomet():
    """Rör sig rangordningen knappt mäts samma vad om och om igen."""
    r = ICresultat(
        perioder=[period(dt.date(2024, 1, 1), 0.3),
                  period(dt.date(2025, 2, 1), 0.3)],
        noll=[x / 10000 for x in range(-300, 301)],
        stabilitet={"Industri": 0.92, "Konsument": 0.90},
    )
    assert r.genomsnittlig_stabilitet() == pytest.approx(0.91)
    assert "samma vad om och om igen" in r.omdome()
    assert "Industri" in r.omdome(), "den frysta kategorin ska namnges"
    assert "närmare ett" in r.omdome()


def test_enskild_frusen_kategori_namns_aven_nar_snittet_ser_bra_ut():
    """Ett genomsnitt på 0,58 kan dölja att Industri ligger på 0,92."""
    r = ICresultat(
        perioder=[period(dt.date(2024, 1, 1), 0.3),
                  period(dt.date(2025, 2, 1), 0.3)],
        noll=[x / 10000 for x in range(-300, 301)],
        stabilitet={"Industri": 0.92, "Konsument": 0.90, "Teknik": 0.30,
                    "Fastighet": 0.11},
    )
    assert r.genomsnittlig_stabilitet() < 0.6
    omdome = r.omdome()
    assert "Industri" in omdome and "Konsument" in omdome
    assert "Fastighet" not in omdome


def test_rorlig_rangordning_ger_ingen_sadan_varning():
    r = ICresultat(
        perioder=[period(dt.date(2024, 1, 1), 0.3),
                  period(dt.date(2025, 2, 1), 0.3)],
        noll=[x / 10000 for x in range(-300, 301)],
        stabilitet={"Industri": 0.1},
    )
    assert "samma vad" not in r.omdome()
    assert "trögrörlig" not in r.omdome()


def test_for_fa_perioder_sager_ifran():
    r = ICresultat(perioder=[period(dt.date(2024, 1, 1), 0.5)])
    assert "För få oberoende perioder" in r.omdome()
