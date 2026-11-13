"""Tester för datakontraktet.

Det viktigaste testet i filen är test_alla_matt_kraver_fält_som_finns:
det kontrollerar att poängmodellen och datalagret talar samma språk.
"""

from __future__ import annotations

import datetime as dt

import pytest

from aktieanalys.data.bolagsdata import FALT, MANUELLA_FALT, Bolagsdata, OkantFalt
from aktieanalys.poang.matt import MATT

IDAG = dt.date(2026, 11, 13)


def ny() -> Bolagsdata:
    return Bolagsdata(ticker="TEST.ST", hamtad=IDAG)


# =====================================================================
#  Kontraktet mellan måtten och datafälten
# =====================================================================
def test_alla_matt_kraver_falt_som_finns():
    """Varje mått får bara kräva datafält som datalagret kan leverera.

    Går det här testet sönder har någon lagt till ett mått som kräver
    data ingen provider vet hur man hämtar.
    """
    for namn, d in MATT.items():
        for falt in d.kraver:
            assert falt in FALT, (
                f"måttet {namn!r} kräver fältet {falt!r} som inte finns i FALT"
            )


def test_manuella_falt_ar_de_vi_tror():
    """De åtta fält som yfinance inte kan ge ska vara markerade manuella."""
    assert set(MANUELLA_FALT) == {
        "substansvarde_per_aktie",
        "substansvarde_per_aktie_historik",
        "substansvarde_totalt",
        "forvaltningsresultat_per_aktie",
        "forvaltningsresultat_per_aktie_historik",
        "belaningsgrad",
        "rantetackningsgrad",
        "cet1_kvot",
    }


def test_alla_falt_har_beskrivning_och_enhet():
    for namn, d in FALT.items():
        assert d.namn == namn
        assert len(d.beskrivning) > 15, f"{namn} saknar riktig beskrivning"
        assert d.enhet, f"{namn} saknar enhet"


# =====================================================================
#  Saknad data blir aldrig noll
# =====================================================================
def test_none_blir_saknat_inte_noll():
    bd = ny()
    bd.satt("kurs", None, "test")
    assert not bd.har("kurs")
    assert bd.varde("kurs") is None
    assert "kurs" in bd.saknade


def test_nan_blir_saknat_inte_noll():
    bd = ny()
    bd.satt("kurs", float("nan"), "test")
    assert not bd.har("kurs")
    assert "NaN" in bd.saknade["kurs"]


def test_noll_ar_ett_giltigt_varde():
    """En verklig nolla ska sparas -- det är gissade nollor vi vägrar."""
    bd = ny()
    bd.satt("nettoskuld", 0.0, "test")
    assert bd.har("nettoskuld")
    assert bd.varde("nettoskuld") == 0.0


def test_markera_saknat_skriver_inte_over_befintligt_varde():
    """CSV-lagret kompletterar -- det raderar aldrig hämtad data."""
    bd = ny()
    bd.satt("kurs", 123.0, "yfinance")
    bd.markera_saknat("kurs", "borde inte hamna här")
    assert bd.varde("kurs") == 123.0
    assert "kurs" not in bd.saknade


def test_satt_rensar_tidigare_saknatmarkering():
    bd = ny()
    bd.markera_saknat("belaningsgrad", "väntar på CSV")
    bd.satt("belaningsgrad", 0.42, "csv:fastighetsdata.csv")
    assert bd.har("belaningsgrad")
    assert "belaningsgrad" not in bd.saknade


# =====================================================================
#  Fältnamn
# =====================================================================
def test_okant_falt_hojer_fel_med_forslag():
    bd = ny()
    with pytest.raises(OkantFalt) as fel:
        bd.satt("kursen", 100.0, "test")
    assert "kurs" in str(fel.value)


def test_okant_falt_vid_lasning_hojer_fel():
    with pytest.raises(OkantFalt):
        ny().varde("hittepa")


# =====================================================================
#  Stöd till poängmodellen
# =====================================================================
def test_saknade_av_listar_vad_som_fattas():
    bd = ny()
    bd.satt("rorelseresultat", 1000.0, "test")
    kraver = MATT["roic"].kraver
    assert bd.saknade_av(kraver) == ["investerat_kapital"]
    assert not bd.har_alla(kraver)

    bd.satt("investerat_kapital", 5000.0, "test")
    assert bd.har_alla(kraver)
    assert bd.saknade_av(kraver) == []


def test_kallor_sparas_per_falt():
    bd = ny()
    bd.satt("kurs", 100.0, "yfinance:info")
    bd.satt("belaningsgrad", 0.4, "csv:fastighetsdata.csv")
    assert bd.kallor["kurs"] == "yfinance:info"
    assert bd.kallor["belaningsgrad"] == "csv:fastighetsdata.csv"
