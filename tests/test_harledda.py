"""Tester för de härledda måtten.

Det viktigaste testet i filen är test_substansvarde_harleds_aldrig_for_
investmentbolag. Investor redovisar innehav till marknadsvärde, så dess
eget kapital ÄR substansvärdet. Latour redovisar industrirörelsen till
bokfört värde och har ett eget kapital på en bråkdel av substansvärdet.
Härleddes substansvärdet ur eget kapital för båda hade Latour blivit
Sveriges dyraste bolag -- ett systematiskt fel, inte ett brus.
"""

from __future__ import annotations

import datetime as dt

import pytest

from aktieanalys.data.bolagsdata import Bolagsdata
from aktieanalys.data.harledda import HARLEDNINGAR, Harledare

IDAG = dt.date(2026, 11, 13)


def castellum() -> Bolagsdata:
    """Castellums verkliga råtal för 2025, så siffrorna går att följa."""
    bd = Bolagsdata(ticker="CAST.ST", hamtad=IDAG, namn="Castellum")
    varden = {
        "rantebarande_skulder": 58_124e6,
        "totala_tillgangar": 155_889e6,
        "rorelseresultat": 6_189e6,
        "rantekostnad": 2_244e6,
        "eget_kapital": 78_311e6,
        "antal_aktier": 426_324_897,          # yfinance info -- 13 % fel
        "antal_aktier_historik": [
            (2025, 492_102_049), (2024, 492_000_000),
            (2023, 493_000_000), (2022, 391_000_000),
        ],
        "eget_kapital_historik": [
            (2025, 78_311e6), (2024, 79_100e6),
            (2023, 77_300e6), (2022, 79_000e6),
        ],
        "rorelseresultat_historik": [(2025, 6_189e6), (2024, 5_900e6)],
        "rantekostnad_historik": [(2025, 2_244e6), (2024, 2_200e6)],
    }
    for f, v in varden.items():
        bd.satt(f, v, "test")
    return bd


# =====================================================================
#  Fastighetsbolagens mått
# =====================================================================
def test_belaningsgrad_stammer_med_castellums_redovisning():
    """Uppmätt: Castellum rapporterar 37 % för 2025."""
    bd = castellum()
    Harledare().harled(bd, "fastighet")
    assert bd.varde("belaningsgrad") == pytest.approx(0.373, abs=0.002)


def test_rantetackningsgrad_harleds():
    bd = castellum()
    Harledare().harled(bd, "fastighet")
    assert bd.varde("rantetackningsgrad") == pytest.approx(2.76, abs=0.01)


def test_substansvarde_stammer_med_castellums_epra_nrv():
    """Uppmätt: Castellum rapporterar EPRA NRV 160 kr per aktie för 2025."""
    bd = castellum()
    Harledare().harled(bd, "fastighet")
    assert bd.varde("substansvarde_per_aktie") == pytest.approx(159.1, abs=0.5)


def test_tal_per_aktie_anvander_rakenskapernas_aktieantal():
    """Yahoos info påstår 426 miljoner aktier för Castellum, balans-
    räkningen 492 miljoner. Det senare stämmer med bolagets egna tal."""
    bd = castellum()
    Harledare().harled(bd, "fastighet")
    # 78 311 / 492,1 = 159,1   (med info hade det blivit 183,7)
    assert bd.varde("substansvarde_per_aktie") < 170


def test_substansvardeshistorik_anvander_respektive_ars_aktieantal():
    """Castellum nyemitterade 2023: 391 -> 493 miljoner aktier. Räknas
    historiken på dagens aktieantal blir de äldre åren fel."""
    bd = castellum()
    Harledare().harled(bd, "fastighet")
    historik = dict(bd.varde("substansvarde_per_aktie_historik"))
    assert historik[2022] == pytest.approx(79_000e6 / 391_000_000, rel=0.001)
    assert historik[2025] == pytest.approx(78_311e6 / 492_102_049, rel=0.001)


def test_forvaltningsresultatets_historik_harleds_INTE():
    """Proxyn pekar åt fel håll mellan åren och skulle göra ett
    tillväxtmått vilseledande. Den ska därför inte finnas."""
    assert not any(
        h.falt == "forvaltningsresultat_per_aktie_historik" for h in HARLEDNINGAR
    )
    bd = castellum()
    Harledare().harled(bd, "fastighet")
    assert not bd.har("forvaltningsresultat_per_aktie_historik")


def test_forvaltningsresultatets_niva_harleds():
    """Nivån används för kurs mot förvaltningsresultat och duger: felet
    är jämnt, så rangordningen håller."""
    bd = castellum()
    Harledare().harled(bd, "fastighet")
    assert bd.varde("forvaltningsresultat_per_aktie") == pytest.approx(8.02, abs=0.05)


# =====================================================================
#  Latour-fällan
# =====================================================================
def test_substansvarde_harleds_aldrig_for_investmentbolag():
    """Investor: eget kapital ÄR substansvärdet. Latour: eget kapital är
    en bråkdel av det. Härledning för båda hade varit systematiskt fel."""
    for ticker in ["INVE-B.ST", "LATO-B.ST"]:
        bd = Bolagsdata(ticker=ticker, hamtad=IDAG)
        bd.satt("eget_kapital", 42_623e6, "test")
        bd.satt("antal_aktier_historik", [(2025, 639_318_250)], "test")

        assert Harledare().harled(bd, "investmentbolag") == 0
        assert not bd.har("substansvarde_per_aktie")
        assert not bd.har("substansvarde_totalt")


def test_inga_harledningar_for_standard_eller_bank():
    for upps in ["standard", "bank"]:
        bd = castellum()
        assert Harledare().harled(bd, upps) == 0


def test_alla_harledningar_galler_bara_fastighet():
    """Ett skydd mot att någon råkar lägga till investmentbolag."""
    for h in HARLEDNINGAR:
        assert h.galler_for == ("fastighet",), h.falt


# =====================================================================
#  Företräde och felhantering
# =====================================================================
def test_verkliga_tal_vinner_over_harledda():
    bd = castellum()
    bd.satt("belaningsgrad", 0.41, "csv:fastighetsdata.csv")
    Harledare().harled(bd, "fastighet")
    assert bd.varde("belaningsgrad") == 0.41
    assert bd.kallor["belaningsgrad"].startswith("csv:")


def test_harlett_varde_markeras_som_proxy():
    bd = castellum()
    Harledare().harled(bd, "fastighet")
    assert Harledare.ar_harlett(bd, "belaningsgrad")
    assert "proxyvärde" in bd.kallor["belaningsgrad"]
    assert not Harledare.ar_harlett(bd, "rorelseresultat")


def test_saknat_rafalt_ger_orsak_som_pekar_pa_csv():
    bd = Bolagsdata(ticker="CAST.ST", hamtad=IDAG)
    bd.satt("rorelseresultat", 6_189e6, "test")
    Harledare().harled(bd, "fastighet")

    assert not bd.har("belaningsgrad")
    assert "kunde inte härledas" in bd.saknade["belaningsgrad"]
    assert "data/manuellt" in bd.saknade["belaningsgrad"]


def test_noll_i_rantekostnad_ger_inget_varde():
    """Division med noll ska ge saknat värde, inte oändlighet."""
    bd = castellum()
    bd.varden["rantekostnad"] = 0.0
    Harledare().harled(bd, "fastighet")
    assert not bd.har("rantetackningsgrad")


def test_noll_i_tillgangar_ger_inget_varde():
    bd = castellum()
    bd.varden["totala_tillgangar"] = 0.0
    Harledare().harled(bd, "fastighet")
    assert not bd.har("belaningsgrad")


def test_ar_utan_aktieantal_utesluts_ur_historiken():
    bd = castellum()
    bd.varden["antal_aktier_historik"] = [(2025, 492_102_049)]
    Harledare().harled(bd, "fastighet")
    historik = bd.varde("substansvarde_per_aktie_historik")
    assert [ar for ar, _ in historik] == [2025]


# =====================================================================
#  Hela kedjan
# =====================================================================
def test_fastighetskategorin_blir_rangordningsbar_pa_verklig_data():
    from aktieanalys.konfig import las_installningar
    from aktieanalys.universum import las_och_valj
    from tests.conftest import KONFIGKATALOG

    inst = las_installningar(KONFIGKATALOG)
    if not inst.databasvag.exists():
        pytest.skip("ingen databas -- kör hamta-data först")

    ku = las_och_valj(inst)[0].kategorier["Fastighet"]
    assert ku.rangordningsbar
    assert ku.saknade_pelare(inst) == [], "alla fyra pelare ska gå att sätta"
    assert "substansvarde_tillvaxt" in ku.anvandbara_matt
    assert "belaningsgrad" in ku.anvandbara_matt
