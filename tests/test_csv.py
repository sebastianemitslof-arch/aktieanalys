"""Tester för den manuella CSV-kompletteringen.

Varje test motsvarar ett misstag som är lätt att göra i ett
kalkylprogram. Poängen är att en felskriven siffra ska stoppa körningen
med ett tydligt besked, inte smyga in i analysen.
"""

from __future__ import annotations

import datetime as dt

import pytest

from aktieanalys.data.bolagsdata import Bolagsdata
from aktieanalys.data.csv_komplettering import CsvFel, CsvKomplettering

IDAG = dt.date(2026, 11, 13)


def skriv(katalog, namn: str, innehall: str):
    (katalog / namn).write_text(innehall.strip() + "\n", encoding="utf-8")
    return CsvKomplettering(katalog, idag=IDAG)


def bolag(ticker="INVE-B.ST") -> Bolagsdata:
    return Bolagsdata(ticker=ticker, hamtad=IDAG)


# =====================================================================
#  Det som ska fungera
# =====================================================================
def test_laser_varden_och_bygger_historik(tmp_path):
    csv = skriv(tmp_path, "substansvarden.csv", """
ticker,ar,substansvarde_per_aktie
INVE-B.ST,2025,385.50
INVE-B.ST,2024,342.10
INVE-B.ST,2023,298.40
""")
    bd = bolag()
    assert csv.komplettera(bd) == 2  # både talet och historiken

    assert bd.varde("substansvarde_per_aktie") == 385.50  # senaste året
    assert bd.varde("substansvarde_per_aktie_historik") == [
        (2025, 385.50), (2024, 342.10), (2023, 298.40)
    ]


def test_aren_sorteras_nyast_forst_oavsett_radordning(tmp_path):
    csv = skriv(tmp_path, "s.csv", """
ticker,ar,substansvarde_per_aktie
INVE-B.ST,2023,298.40
INVE-B.ST,2025,385.50
INVE-B.ST,2024,342.10
""")
    bd = bolag()
    csv.komplettera(bd)
    assert bd.varde("substansvarde_per_aktie") == 385.50
    assert [a for a, _ in bd.varde("substansvarde_per_aktie_historik")] == [2025, 2024, 2023]


def test_tom_cell_blir_saknat_inte_noll(tmp_path):
    """Det här är hela systemets viktigaste regel."""
    csv = skriv(tmp_path, "f.csv", """
ticker,ar,forvaltningsresultat_per_aktie,belaningsgrad,rantetackningsgrad
CAST.ST,2025,11.20,,3.10
""")
    bd = bolag("CAST.ST")
    csv.komplettera(bd)
    assert bd.varde("forvaltningsresultat_per_aktie") == 11.20
    assert bd.varde("rantetackningsgrad") == 3.10
    assert not bd.har("belaningsgrad")
    assert bd.varde("belaningsgrad") is None


def test_komplettering_skriver_inte_over_hamtad_data(tmp_path):
    csv = skriv(tmp_path, "s.csv", """
ticker,ar,substansvarde_totalt
INVE-B.ST,2025,999.0
""")
    bd = bolag()
    bd.satt("substansvarde_totalt", 1234.0, "yfinance")
    csv.komplettera(bd)
    assert bd.varde("substansvarde_totalt") == 1234.0


def test_komma_som_decimaltecken_accepteras(tmp_path):
    """Svenska kalkylprogram exporterar ofta med komma."""
    csv = skriv(tmp_path, "b.csv", """
ticker,ar,cet1_kvot
SEB-A.ST,2025,"0,182"
""")
    bd = bolag("SEB-A.ST")
    csv.komplettera(bd)
    assert bd.varde("cet1_kvot") == 0.182


def test_kommentarsrader_hoppas_over(tmp_path):
    csv = skriv(tmp_path, "s.csv", """
# det här är en kommentar
# INVE-B.ST,2020,1.0
ticker,ar,substansvarde_per_aktie
INVE-B.ST,2025,385.50
""")
    bd = bolag()
    csv.komplettera(bd)
    assert bd.varde("substansvarde_per_aktie") == 385.50
    assert len(bd.varde("substansvarde_per_aktie_historik")) == 1


def test_bara_rubrikrad_ar_tillatet(tmp_path):
    """Mallfilerna levereras tomma och ska inte ge fel."""
    csv = skriv(tmp_path, "s.csv", "ticker,ar,substansvarde_per_aktie")
    assert csv.tickers() == []
    assert csv.komplettera(bolag()) == 0


def test_flera_filer_las_ihop(tmp_path):
    (tmp_path / "a.csv").write_text(
        "ticker,ar,substansvarde_per_aktie\nCAST.ST,2025,250.0\n", encoding="utf-8")
    (tmp_path / "b.csv").write_text(
        "ticker,ar,belaningsgrad\nCAST.ST,2025,0.41\n", encoding="utf-8")
    csv = CsvKomplettering(tmp_path, idag=IDAG)
    bd = bolag("CAST.ST")
    csv.komplettera(bd)
    assert bd.varde("substansvarde_per_aktie") == 250.0
    assert bd.varde("belaningsgrad") == 0.41


def test_saknad_katalog_ar_ofarligt(tmp_path):
    csv = CsvKomplettering(tmp_path / "finns-inte", idag=IDAG)
    assert csv.komplettera(bolag()) == 0


def test_okanda_tickers_pekas_ut(tmp_path):
    csv = skriv(tmp_path, "s.csv", """
ticker,ar,substansvarde_per_aktie
INVE-B.ST,2025,385.50
FELSTAVAD.ST,2025,100.0
""")
    assert csv.okanda_tickers(["INVE-B.ST", "CAST.ST"]) == ["FELSTAVAD.ST"]


# =====================================================================
#  Det som ska ge fel
# =====================================================================
def test_okand_kolumn_ger_forslag(tmp_path):
    with pytest.raises(CsvFel) as fel:
        skriv(tmp_path, "s.csv", """
ticker,ar,substansvarde_per_akti
INVE-B.ST,2025,385.50
""")
    assert "okänd kolumn" in str(fel.value)
    assert "substansvarde_per_aktie" in str(fel.value)


def test_automatiskt_falt_som_kolumn_nekas(tmp_path):
    """Kurs hämtas från Yahoo och ska inte skrivas in för hand."""
    with pytest.raises(CsvFel, match="hämtas automatiskt"):
        skriv(tmp_path, "s.csv", "ticker,ar,kurs\nINVE-B.ST,2025,100.0")


def test_historikkolumn_nekas(tmp_path):
    """Historiken byggs av årsraderna och skrivs aldrig som kolumn."""
    with pytest.raises(CsvFel, match="okänd kolumn"):
        skriv(tmp_path, "s.csv",
              "ticker,ar,substansvarde_per_aktie_historik\nINVE-B.ST,2025,100.0")


def test_saknad_tickerkolumn_nekas(tmp_path):
    with pytest.raises(CsvFel, match="'ticker'"):
        skriv(tmp_path, "s.csv", "bolag,ar,cet1_kvot\nSEB-A.ST,2025,0.18")


def test_saknad_arskolumn_nekas(tmp_path):
    with pytest.raises(CsvFel, match="'ar'"):
        skriv(tmp_path, "s.csv", "ticker,cet1_kvot\nSEB-A.ST,0.18")


def test_fil_utan_nyckeltalskolumner_nekas(tmp_path):
    with pytest.raises(CsvFel, match="inga nyckeltalskolumner"):
        skriv(tmp_path, "s.csv", "ticker,ar\nSEB-A.ST,2025")


def test_text_i_stallet_for_tal_namnger_raden(tmp_path):
    with pytest.raises(CsvFel) as fel:
        skriv(tmp_path, "s.csv", """
ticker,ar,substansvarde_per_aktie
INVE-B.ST,2025,385.50
INVE-B.ST,2024,ungefär 342
""")
    assert "rad 3" in str(fel.value)
    assert "substansvarde_per_aktie" in str(fel.value)


def test_felaktigt_artal_nekas(tmp_path):
    with pytest.raises(CsvFel, match="måste vara ett årtal"):
        skriv(tmp_path, "s.csv",
              "ticker,ar,cet1_kvot\nSEB-A.ST,i fjol,0.18")


def test_artal_i_framtiden_nekas(tmp_path):
    with pytest.raises(CsvFel, match="utanför det rimliga"):
        skriv(tmp_path, "s.csv",
              "ticker,ar,cet1_kvot\nSEB-A.ST,2031,0.18")


def test_dubblerad_ticker_och_ar_nekas(tmp_path):
    with pytest.raises(CsvFel, match="står flera gånger"):
        skriv(tmp_path, "s.csv", """
ticker,ar,cet1_kvot
SEB-A.ST,2025,0.18
SEB-A.ST,2025,0.19
""")


def test_tom_ticker_nekas(tmp_path):
    with pytest.raises(CsvFel, match="ticker saknas"):
        skriv(tmp_path, "s.csv", "ticker,ar,cet1_kvot\n,2025,0.18")


# =====================================================================
#  De medföljande mallfilerna
# =====================================================================
def test_medfoljande_mallar_ar_giltiga():
    from tests.conftest import ROT

    csv = CsvKomplettering(ROT / "data" / "manuellt", idag=IDAG)
    assert len(csv.filer) == 3
    assert csv.tickers() == []  # mallarna är tomma, inga påhittade tal
