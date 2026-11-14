"""Tester för den automatiska bolagssökningen.

Den ursprungliga listan skrevs ur minnet, och ABB -- ett av börsens
största bolag -- saknades. Sökningen finns för att ett bolag ingen
tänkte på ändå ska kunna väljas.

Modulen föreslår, den ändrar aldrig universum.yaml. Testerna kontrollerar
framför allt att tveksamma fall FLAGGAS i stället för att tyst
kategoriseras fel.
"""

from __future__ import annotations

import pytest

from aktieanalys.hitta import (
    KANDA_INVESTMENTBOLAG,
    KRAVER_BEDOMNING,
    SEKTORKARTA,
    Kandidat,
    Sokresultat,
    _flagga_aktieslag,
    aktieslagsstam,
    yaml_forslag,
)


def kandidat(ticker, borsvarde=50e9, omsattning=100e6, **kw):
    return Kandidat(ticker=ticker, namn=f"{ticker} AB",
                    borsvarde=borsvarde, daglig_omsattning=omsattning, **kw)


# =====================================================================
#  Aktieslag
# =====================================================================
@pytest.mark.parametrize("ticker, stam", [
    ("ATCO-A.ST", "ATCO"),
    ("ATCO-B.ST", "ATCO"),
    ("INVE-B.ST", "INVE"),
    ("ALIV-SDB.ST", "ALIV"),
    ("AZN.ST", "AZN"),
    ("NDA-SE.ST", "NDA-SE"),   # SE är landskod, inte aktieslag
    ("SEB-A.ST", "SEB"),
])
def test_aktieslagsstam(ticker, stam):
    assert aktieslagsstam(ticker) == stam


def test_aktieslag_som_redan_finns_flaggas():
    """ATCO-A är redan med. Då ska ATCO-B inte föreslås utan varning."""
    r = Sokresultat(kandidater=[kandidat("ATCO-B.ST")])
    _flagga_aktieslag(r, {"ATCO-A.ST", "VOLV-B.ST"})
    assert r.nya[0].aktieslagskrock == "ATCO-A.ST"


def test_tva_aktieslag_i_samma_forslag_flaggas():
    """Föreslås både A och B ska det mest omsatta behållas."""
    r = Sokresultat(kandidater=[
        kandidat("INVE-A.ST", omsattning=154e6),
        kandidat("INVE-B.ST", omsattning=1094e6),
    ])
    _flagga_aktieslag(r, set())
    a = next(k for k in r.nya if k.ticker == "INVE-A.ST")
    b = next(k for k in r.nya if k.ticker == "INVE-B.ST")
    assert a.aktieslagskrock == "INVE-B.ST", "det tunnare aktieslaget flaggas"
    assert b.aktieslagskrock is None


def test_olika_bolag_flaggas_inte():
    r = Sokresultat(kandidater=[kandidat("VOLV-B.ST"), kandidat("SAND.ST")])
    _flagga_aktieslag(r, set())
    assert all(k.aktieslagskrock is None for k in r.nya)


# =====================================================================
#  Kategoriindelning
# =====================================================================
def test_sektorkartan_pekar_bara_pa_kategorier_som_finns():
    from aktieanalys.konfig import las_installningar
    from tests.conftest import KONFIGKATALOG

    inst = las_installningar(KONFIGKATALOG)
    for sektor, kategori in SEKTORKARTA.items():
        assert kategori in inst.kategorinamn, f"{sektor} -> {kategori}"
    for sektor, (kategori, _) in KRAVER_BEDOMNING.items():
        assert kategori in inst.kategorinamn, sektor


def test_finansiella_bolag_kraver_bedomning():
    """Yahoos Financial Services rymmer banker, försäkringsbolag och
    investmentbolag, som ska bedömas på helt olika mått."""
    assert "Financial Services" in KRAVER_BEDOMNING
    assert "Financial Services" not in SEKTORKARTA
    _, orsak = KRAVER_BEDOMNING["Financial Services"]
    assert "investmentbolag" in orsak


def test_fastighet_och_halsovard_ar_entydiga():
    assert SEKTORKARTA["Real Estate"] == "Fastighet"
    assert SEKTORKARTA["Healthcare"] == "Hälsovård"


# =====================================================================
#  Resultatet
# =====================================================================
def test_nya_och_befintliga_skiljs_at():
    r = Sokresultat(kandidater=[
        kandidat("VOLV-B.ST", redan_med=True),
        kandidat("ABB.ST"),
    ])
    assert [k.ticker for k in r.nya] == ["ABB.ST"]
    assert [k.ticker for k in r.befintliga] == ["VOLV-B.ST"]


def test_per_kategori_sorterar_storst_forst():
    r = Sokresultat(kandidater=[
        kandidat("LITEN.ST", borsvarde=6e9, kategori="Industri"),
        kandidat("STOR.ST", borsvarde=900e9, kategori="Industri"),
    ])
    assert [k.ticker for k in r.per_kategori()["Industri"]] == ["STOR.ST", "LITEN.ST"]


def test_okand_kategori_far_eget_fack():
    r = Sokresultat(kandidater=[kandidat("X.ST", kategori=None)])
    assert "Okänd kategori" in r.per_kategori()


def test_yaml_forslaget_gar_att_klistra_in():
    import yaml

    r = Sokresultat(kandidater=[
        kandidat("ABB.ST", borsvarde=1525e9, omsattning=588e6, kategori="Industri"),
    ])
    text = yaml_forslag(r)
    assert "ABB.ST" in text
    assert "1525 mdr" in text
    # Raderna ska gå att läsa som YAML när de hamnar under en bolagslista
    bolagsrader = [r_ for r_ in text.splitlines()
                   if r_.strip().startswith("- ")]
    assert bolagsrader
    laddat = yaml.safe_load("bolag:\n" + "\n".join(bolagsrader))
    assert laddat["bolag"] == ["ABB.ST"]


def test_kontrollmarkering_i_yaml():
    r = Sokresultat(kandidater=[
        kandidat("INVE-B.ST", kategori="Bank/finans",
                 anmarkning="rymmer investmentbolag"),
    ])
    assert "KONTROLLERA" in yaml_forslag(r)


# =====================================================================
#  Investmentbolag som Yahoo inte känner igen som sådana
# =====================================================================
def test_latour_och_lundberg_ar_med_i_listan():
    """Yahoo klassar dem som Industrials. De skulle annars glida in i
    Industri med standardmåtten, utan att någon flagga gick upp."""
    for ticker in ["LATO-B.ST", "LUND-B.ST", "RATO-B.ST"]:
        assert ticker in KANDA_INVESTMENTBOLAG, ticker


def test_investor_och_industrivarden_ar_med():
    for ticker in ["INVE-B.ST", "INDU-C.ST", "KINV-B.ST"]:
        assert ticker in KANDA_INVESTMENTBOLAG, ticker


def test_vanliga_industribolag_ar_inte_med():
    for ticker in ["ABB.ST", "VOLV-B.ST", "SAND.ST", "SKF-B.ST",
                   "STOR-B.ST", "SDIP-B.ST"]:
        assert ticker not in KANDA_INVESTMENTBOLAG, (
            f"{ticker} är ett rörelsedrivande bolag, inte ett investmentbolag"
        )


def test_investmentbolag_far_ingen_kategori(monkeypatch):
    """Ett investmentbolag ska inte kategoriseras alls -- det ska stoppas
    och förklaras."""
    import aktieanalys.hitta as hitta

    k = kandidat("LATO-B.ST")
    monkeypatch.setattr(
        hitta, "yf", __import__("types").SimpleNamespace(), raising=False
    )

    class FalskTicker:
        def __init__(self, t): pass
        @property
        def info(self): return {"sector": "Industrials"}

    import sys, types
    falsk = types.ModuleType("yfinance")
    falsk.Ticker = FalskTicker
    monkeypatch.setitem(sys.modules, "yfinance", falsk)

    hitta._fyll_sektorer([k], paus_sek=0)
    assert k.kategori is None, "ska inte hamna i Industri"
    assert "investmentbolag" in k.anmarkning
    assert "EV/EBIT" in k.anmarkning


def test_vanligt_industribolag_kategoriseras(monkeypatch):
    import sys, types

    import aktieanalys.hitta as hitta

    k = kandidat("ABB.ST")

    class FalskTicker:
        def __init__(self, t): pass
        @property
        def info(self): return {"sector": "Industrials"}

    falsk = types.ModuleType("yfinance")
    falsk.Ticker = FalskTicker
    monkeypatch.setitem(sys.modules, "yfinance", falsk)

    hitta._fyll_sektorer([k], paus_sek=0)
    assert k.kategori == "Industri"
    assert k.anmarkning is None
