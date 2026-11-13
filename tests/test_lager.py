"""Tester för SQLite-lagret, med tyngdpunkt på punkt-i-tiden-läsning.

Backtestet i steg 9 står och faller med att databasen inte låter
framtidsinformation läcka bakåt. Det är det flera av testerna här
kontrollerar.
"""

from __future__ import annotations

import datetime as dt

import pandas as pd
import pytest

from aktieanalys.data.bolagsdata import Bolagsdata
from aktieanalys.data.lager import Lager


@pytest.fixture
def lager(tmp_path):
    with Lager(tmp_path / "test.sqlite") as l:
        yield l


def bolag(datum: dt.date, kurs: float, **extra) -> Bolagsdata:
    bd = Bolagsdata(ticker="TEST.ST", hamtad=datum, namn="Testbolaget")
    bd.satt("kurs", kurs, "test")
    for falt, varde in extra.items():
        bd.satt(falt, varde, "test")
    return bd


# =====================================================================
#  Rundtur
# =====================================================================
def test_spara_och_lasa_tal(lager):
    lager.spara(bolag(dt.date(2026, 11, 13), 100.0, nettoskuld=-5e9))
    tillbaka = lager.las("TEST.ST", dt.date(2026, 11, 13))
    assert tillbaka.varde("kurs") == 100.0
    assert tillbaka.varde("nettoskuld") == -5e9


def test_spara_och_lasa_arslista(lager):
    serie = [(2025, 5e9), (2024, 4.5e9), (2023, 4.0e9)]
    lager.spara(bolag(dt.date(2026, 11, 13), 100.0, rorelseresultat_historik=serie))
    tillbaka = lager.las("TEST.ST", dt.date(2026, 11, 13))
    assert tillbaka.varde("rorelseresultat_historik") == serie


def test_okant_bolag_ger_none(lager):
    assert lager.las("FINNS-INTE.ST") is None


def test_saknade_falt_sparas_med_orsak(lager):
    bd = bolag(dt.date(2026, 11, 13), 100.0)
    bd.markera_saknat("belaningsgrad", "kräver CSV-komplettering")
    lager.spara(bd)
    tillbaka = lager.las("TEST.ST", dt.date(2026, 11, 13))
    assert tillbaka.saknade["belaningsgrad"] == "kräver CSV-komplettering"


# =====================================================================
#  Punkt i tiden -- ingen look-ahead
# =====================================================================
def test_las_ger_vardet_som_gallde_da(lager):
    lager.spara(bolag(dt.date(2026, 10, 1), 90.0))
    lager.spara(bolag(dt.date(2026, 11, 1), 110.0))

    assert lager.las("TEST.ST", dt.date(2026, 10, 15)).varde("kurs") == 90.0
    assert lager.las("TEST.ST", dt.date(2026, 11, 15)).varde("kurs") == 110.0


def test_las_fore_forsta_hamtningen_ger_none(lager):
    lager.spara(bolag(dt.date(2026, 11, 1), 110.0))
    assert lager.las("TEST.ST", dt.date(2026, 10, 1)) is None


def test_falt_som_bara_finns_i_aldre_hamtning_overlever(lager):
    """Slutar yfinance leverera ett fält ska det gamla värdet finnas kvar."""
    lager.spara(bolag(dt.date(2026, 10, 1), 90.0, cet1_kvot=0.18))
    lager.spara(bolag(dt.date(2026, 11, 1), 110.0))

    senaste = lager.las("TEST.ST", dt.date(2026, 11, 1))
    assert senaste.varde("kurs") == 110.0
    assert senaste.varde("cet1_kvot") == 0.18
    assert senaste.kallor["cet1_kvot"] == "test"


def test_samma_dag_skrivs_over_men_inte_gardagen(lager):
    lager.spara(bolag(dt.date(2026, 11, 1), 100.0))
    lager.spara(bolag(dt.date(2026, 11, 13), 200.0))
    lager.spara(bolag(dt.date(2026, 11, 13), 201.0))  # omkörning samma dag

    assert lager.las("TEST.ST", dt.date(2026, 11, 13)).varde("kurs") == 201.0
    assert lager.las("TEST.ST", dt.date(2026, 11, 1)).varde("kurs") == 100.0


# =====================================================================
#  Kurshistorik
# =====================================================================
def test_kurshistorik_kapas_vid_datum(lager):
    serie = pd.Series(
        [98.0, 99.0, 100.0, 101.0],
        index=pd.to_datetime(["2026-11-10", "2026-11-11", "2026-11-12", "2026-11-13"]),
    )
    lager.spara(bolag(dt.date(2026, 11, 13), 101.0, kurshistorik=serie))

    hela = lager.las("TEST.ST", dt.date(2026, 11, 13)).varde("kurshistorik")
    assert len(hela) == 4

    kapad = lager.las_kurser("TEST.ST", dt.date(2026, 11, 11))
    assert len(kapad) == 2
    assert kapad.iloc[-1] == 99.0


def test_kurshistorik_slas_samman_over_hamtningar(lager):
    """Nya dagar läggs till, gamla behålls -- historiken växer."""
    lager.spara(bolag(dt.date(2026, 11, 11), 99.0, kurshistorik=pd.Series(
        [98.0, 99.0], index=pd.to_datetime(["2026-11-10", "2026-11-11"]))))
    lager.spara(bolag(dt.date(2026, 11, 13), 101.0, kurshistorik=pd.Series(
        [100.0, 101.0], index=pd.to_datetime(["2026-11-12", "2026-11-13"]))))

    assert len(lager.las_kurser("TEST.ST")) == 4


def test_tom_kursserie_sparas_utan_fel(lager):
    assert lager.spara_kurser("TEST.ST", pd.Series(dtype=float)) == 0


# =====================================================================
#  Cachens färskhet
# =====================================================================
def test_alder_och_farskhet(lager):
    lager.spara(bolag(dt.date(2026, 11, 10), 100.0))
    idag = dt.date(2026, 11, 13)

    assert lager.alder_i_dagar("TEST.ST", idag) == 3
    assert lager.ar_farsk("TEST.ST", 5, idag) is True
    assert lager.ar_farsk("TEST.ST", 1, idag) is False


def test_okant_bolag_ar_aldrig_farskt(lager):
    assert lager.alder_i_dagar("FINNS-INTE.ST") is None
    assert lager.ar_farsk("FINNS-INTE.ST", 999) is False


# =====================================================================
#  Robusthet och översikt
# =====================================================================
def test_falt_som_tagits_bort_ur_katalogen_ignoreras(lager):
    """Byter vi ut ett fält ska gamla rader inte krascha inläsningen."""
    lager.spara(bolag(dt.date(2026, 11, 13), 100.0))
    lager.db.execute(
        "INSERT INTO fundamenta (ticker, datum, falt, varde) VALUES (?,?,?,?)",
        ("TEST.ST", "2026-11-13", "ett_gammalt_falt", 42.0),
    )
    lager.db.commit()

    tillbaka = lager.las("TEST.ST", dt.date(2026, 11, 13))
    assert tillbaka.varde("kurs") == 100.0
    assert "ett_gammalt_falt" not in tillbaka.varden


def test_hamtlogg_och_problemlista(lager):
    idag = dt.date(2026, 11, 13)
    lager.logga_hamtning("A.ST", idag, "yfinance", "ok", 21)
    lager.logga_hamtning("B.ST", idag, "yfinance", "utan_svar", 0, "tomt svar")

    problem = lager.hamtningar_med_problem(idag)
    assert len(problem) == 1
    assert problem[0]["ticker"] == "B.ST"


def test_statistik(lager):
    lager.spara(bolag(dt.date(2026, 11, 13), 100.0, nettoskuld=1e9))
    s = lager.statistik()
    assert s["bolag"] == 1
    assert s["varden"] == 2
    assert s["senaste_datum"] == "2026-11-13"


def test_databasfilen_skapas_med_katalog(tmp_path):
    vag = tmp_path / "djupt" / "nere" / "cache.sqlite"
    with Lager(vag):
        pass
    assert vag.exists()


# =====================================================================
#  Rensa tillbakadragna härledningar
# =====================================================================
def test_harledda_falt_listas(lager):
    bd = bolag(dt.date(2026, 11, 13), 100.0)
    bd.satt("belaningsgrad", 0.37, "härlett ur skulder, tillgångar (proxyvärde)")
    bd.satt("rantetackningsgrad", 2.8, "csv:fastighetsdata.csv")
    lager.spara(bd)

    assert lager.harledda_falt() == {"belaningsgrad": 1}


def test_ta_bort_harledda_ror_inte_csv_eller_yfinance(lager):
    """En tillbakadragen härledning ska kunna rensas utan att verkliga
    tal försvinner."""
    a = bolag(dt.date(2026, 11, 13), 100.0)
    a.satt("belaningsgrad", 0.37, "härlett ur skulder, tillgångar (proxyvärde)")
    lager.spara(a)

    b = Bolagsdata(ticker="ANNAT.ST", hamtad=dt.date(2026, 11, 13))
    b.satt("belaningsgrad", 0.41, "csv:fastighetsdata.csv")
    b.satt("kurs", 50.0, "yfinance:info")
    lager.spara(b)

    assert lager.ta_bort_harledda("belaningsgrad") == 1

    assert not lager.las("TEST.ST", dt.date(2026, 11, 13)).har("belaningsgrad")
    kvar = lager.las("ANNAT.ST", dt.date(2026, 11, 13))
    assert kvar.varde("belaningsgrad") == 0.41
    assert kvar.varde("kurs") == 50.0


def test_ta_bort_harledda_for_okant_falt_ar_ofarligt(lager):
    assert lager.ta_bort_harledda("belaningsgrad") == 0
