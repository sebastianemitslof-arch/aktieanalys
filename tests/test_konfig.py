"""Tester för inläsning och validering av konfigurationen.

Varje test motsvarar ett misstag som är lätt att göra i YAML-filerna.
Går testet igenom får du ett läsbart felmeddelande i stället för en
felaktig analys.
"""

from __future__ import annotations

import datetime as dt

import pytest

from aktieanalys.konfig import (
    Innehav,
    KonfigFel,
    fran_dictar,
    las_installningar,
)
from tests.conftest import KONFIGKATALOG


# =====================================================================
#  Den medföljande konfigurationen
# =====================================================================
def test_medfoljande_config_ar_giltig():
    """Filerna i config/ ska validera som de levereras."""
    inst = las_installningar(KONFIGKATALOG)
    assert len(inst.kategorinamn) == 7
    assert len(inst.alla_tickers()) > 50


def test_alla_kategorier_har_ett_innehavsfalt():
    """portfolj.yaml ska täcka exakt kategorierna i universum.yaml."""
    inst = las_installningar(KONFIGKATALOG)
    assert set(inst.portfolj.innehav) == set(inst.universum.kategorier)


def test_varje_kategori_pekar_pa_en_mattuppsattning_som_finns():
    inst = las_installningar(KONFIGKATALOG)
    for kategori in inst.kategorinamn:
        upps = inst.mattuppsattning_for(kategori)
        assert upps.alla_matt()


def test_sokvagar_blir_absoluta():
    """Sökvägarna ska peka rätt oavsett var kommandot körs ifrån."""
    inst = las_installningar(KONFIGKATALOG)
    assert inst.databasvag.is_absolute()
    assert inst.databasvag.name == "cache.sqlite"
    assert inst.rapportkatalog.is_absolute()


def test_bank_och_fastighet_anvander_sektorsmatt():
    """Banker ska inte bedömas på EV/EBIT, fastighet inte på vanlig vinst."""
    inst = las_installningar(KONFIGKATALOG)
    bank = inst.mattuppsattning_for("Bank/finans").alla_matt()
    assert "roe" in bank and "p_b" in bank and "kapitalstyrka" in bank
    assert "ev_ebit" not in bank
    assert "nettoskuld_ebitda" not in bank

    fastighet = inst.mattuppsattning_for("Fastighet").alla_matt()
    assert "forvaltningsresultat_per_aktie_tillvaxt" in fastighet
    assert "belaningsgrad" in fastighet
    assert "rantetackningsgrad" in fastighet
    assert "kurs_mot_substans" in fastighet

    inv = inst.mattuppsattning_for("Investmentbolag").alla_matt()
    assert "substansrabatt_mot_historik" in inv
    assert "substansvarde_tillvaxt" in inv


# =====================================================================
#  Fel i config.yaml
# =====================================================================
def test_pelarvikter_maste_summera_till_ett(config_dict, universum_dict, portfolj_dict):
    config_dict["poang"]["vikter"]["momentum"] = 0.50  # summan blir 1.25
    with pytest.raises(KonfigFel, match="summerar till 1.25"):
        fran_dictar(config_dict, universum_dict, portfolj_dict)


def test_mattvikter_inom_pelare_maste_summera_till_ett(
    config_dict, universum_dict, portfolj_dict
):
    config_dict["poang"]["mattuppsattningar"]["standard"]["kvalitet"]["roic"] = 0.9
    with pytest.raises(KonfigFel, match="standard.kvalitet summerar till"):
        fran_dictar(config_dict, universum_dict, portfolj_dict)


def test_felstavat_matt_ger_forslag(config_dict, universum_dict, portfolj_dict):
    upps = config_dict["poang"]["mattuppsattningar"]["standard"]["kvalitet"]
    upps["roci"] = upps.pop("roic")
    with pytest.raises(KonfigFel) as fel:
        fran_dictar(config_dict, universum_dict, portfolj_dict)
    assert "okänt mått 'roci'" in str(fel.value)
    assert "roic" in str(fel.value)


def test_matt_i_fel_pelare_upptacks(config_dict, universum_dict, portfolj_dict):
    """pe är ett värderingsmått och får inte ligga under kvalitet."""
    upps = config_dict["poang"]["mattuppsattningar"]["standard"]
    upps["kvalitet"] = {"pe": 1.0}
    with pytest.raises(KonfigFel, match="hör till pelaren 'vardering'"):
        fran_dictar(config_dict, universum_dict, portfolj_dict)


def test_okand_installning_upptacks(config_dict, universum_dict, portfolj_dict):
    """Felstavade inställningsnamn ska aldrig ignoreras tyst."""
    config_dict["universum"]["min_borsvarde"] = 5.0
    with pytest.raises(KonfigFel, match="okänd inställning"):
        fran_dictar(config_dict, universum_dict, portfolj_dict)


def test_saknad_installning_upptacks(config_dict, universum_dict, portfolj_dict):
    del config_dict["kostnader"]["courtage_procent"]
    with pytest.raises(KonfigFel, match="saknas"):
        fran_dictar(config_dict, universum_dict, portfolj_dict)


def test_text_i_stallet_for_tal_upptacks(config_dict, universum_dict, portfolj_dict):
    config_dict["kostnader"]["courtage_procent"] = "noll komma tjugofem"
    with pytest.raises(KonfigFel, match="måste vara ett tal"):
        fran_dictar(config_dict, universum_dict, portfolj_dict)


def test_tom_pelare_upptacks(config_dict, universum_dict, portfolj_dict):
    config_dict["poang"]["mattuppsattningar"]["standard"]["momentum"] = {}
    with pytest.raises(KonfigFel, match="är tom"):
        fran_dictar(config_dict, universum_dict, portfolj_dict)


def test_orimlig_justering_for_claude_upptacks(
    config_dict, universum_dict, portfolj_dict
):
    """Claude ska inte kunna ges makt att flytta poängen hur mycket som helst."""
    config_dict["kvalitativt"]["max_justering"] = 0.9
    with pytest.raises(KonfigFel):
        fran_dictar(config_dict, universum_dict, portfolj_dict)


def test_felaktigt_beslutsfonster_upptacks(config_dict, universum_dict, portfolj_dict):
    config_dict["byten"]["beslutsfonster"] = [{"fran": "15/2", "till": "15/3"}]
    with pytest.raises(KonfigFel, match="MM-DD"):
        fran_dictar(config_dict, universum_dict, portfolj_dict)


def test_bakvant_beslutsfonster_upptacks(config_dict, universum_dict, portfolj_dict):
    config_dict["byten"]["beslutsfonster"] = [{"fran": "12-01", "till": "01-31"}]
    with pytest.raises(KonfigFel, match="slutar före det börjar"):
        fran_dictar(config_dict, universum_dict, portfolj_dict)


def test_felaktig_loggniva_upptacks(config_dict, universum_dict, portfolj_dict):
    config_dict["loggning"]["niva"] = "PRATSAM"
    with pytest.raises(KonfigFel):
        fran_dictar(config_dict, universum_dict, portfolj_dict)


def test_felaktig_mejladress_upptacks(config_dict, universum_dict, portfolj_dict):
    config_dict["rapport"]["mejl"]["till"] = "inte-en-adress"
    with pytest.raises(KonfigFel, match="mejladress"):
        fran_dictar(config_dict, universum_dict, portfolj_dict)


# =====================================================================
#  Fel i universum.yaml
# =====================================================================
def test_okand_mattuppsattning_upptacks(config_dict, universum_dict, portfolj_dict):
    universum_dict["kategorier"]["Industri"]["mattuppsattning"] = "verkstad"
    with pytest.raises(KonfigFel, match="finns inte i config.yaml"):
        fran_dictar(config_dict, universum_dict, portfolj_dict)


def test_ticker_utan_st_upptacks(config_dict, universum_dict, portfolj_dict):
    universum_dict["kategorier"]["Industri"]["bolag"].append("VOLV-B")
    with pytest.raises(KonfigFel, match=r"saknar ändelsen \.ST"):
        fran_dictar(config_dict, universum_dict, portfolj_dict)


def test_dubblett_inom_kategori_upptacks(config_dict, universum_dict, portfolj_dict):
    bolag = universum_dict["kategorier"]["Industri"]["bolag"]
    bolag.append(bolag[0])
    with pytest.raises(KonfigFel, match="flera gånger"):
        fran_dictar(config_dict, universum_dict, portfolj_dict)


def test_samma_bolag_i_tva_kategorier_upptacks(
    config_dict, universum_dict, portfolj_dict
):
    """Ett bolag i två kategorier skulle låta det konkurrera med sig självt."""
    universum_dict["kategorier"]["Teknik"]["bolag"].append("VOLV-B.ST")
    with pytest.raises(KonfigFel, match="står i både"):
        fran_dictar(config_dict, universum_dict, portfolj_dict)


def test_tom_kategori_upptacks(config_dict, universum_dict, portfolj_dict):
    universum_dict["kategorier"]["Teknik"]["bolag"] = []
    with pytest.raises(KonfigFel, match="minst ett bolag"):
        fran_dictar(config_dict, universum_dict, portfolj_dict)


# =====================================================================
#  Fel i portfolj.yaml
# =====================================================================
def test_innehav_maste_finnas_i_sin_kategori(config_dict, universum_dict, portfolj_dict):
    """VOLV-B hör till Industri och kan inte vara Teknik-innehavet."""
    portfolj_dict["innehav"]["Teknik"] = {
        "ticker": "VOLV-B.ST",
        "kopdatum": "2024-01-15",
        "antal": 10,
        "anskaffningskurs": 250,
    }
    with pytest.raises(KonfigFel, match="finns inte i kategorin 'Teknik'"):
        fran_dictar(config_dict, universum_dict, portfolj_dict)


def test_innehav_utan_kopdatum_upptacks(config_dict, universum_dict, portfolj_dict):
    portfolj_dict["innehav"]["Industri"] = {"ticker": "VOLV-B.ST", "kopdatum": ""}
    with pytest.raises(KonfigFel, match="kopdatum"):
        fran_dictar(config_dict, universum_dict, portfolj_dict)


def test_kopdatum_i_framtiden_upptacks(config_dict, universum_dict, portfolj_dict):
    imorgon = dt.date.today() + dt.timedelta(days=1)
    portfolj_dict["innehav"]["Industri"] = {
        "ticker": "VOLV-B.ST",
        "kopdatum": imorgon.isoformat(),
    }
    with pytest.raises(KonfigFel, match="framtiden"):
        fran_dictar(config_dict, universum_dict, portfolj_dict)


def test_okand_kategori_i_portfoljen_upptacks(
    config_dict, universum_dict, portfolj_dict
):
    portfolj_dict["innehav"]["Råvaror"] = {"ticker": ""}
    with pytest.raises(KonfigFel, match="finns inte i universum.yaml"):
        fran_dictar(config_dict, universum_dict, portfolj_dict)


def test_giltigt_innehav_accepteras(config_dict, universum_dict, portfolj_dict):
    portfolj_dict["innehav"]["Industri"] = {
        "ticker": "VOLV-B.ST",
        "kopdatum": "2024-01-15",
        "antal": 40,
        "anskaffningskurs": 265.50,
    }
    inst = fran_dictar(config_dict, universum_dict, portfolj_dict)
    innehav = inst.innehav_i("Industri")
    assert innehav.finns
    assert innehav.ticker == "VOLV-B.ST"
    assert innehav.kopdatum == dt.date(2024, 1, 15)


def test_tom_portfolj_ar_tillaten(config_dict, universum_dict, portfolj_dict):
    """Att inte ha köpt något än är ett giltigt utgångsläge."""
    inst = fran_dictar(config_dict, universum_dict, portfolj_dict)
    assert all(not inst.innehav_i(k).finns for k in inst.kategorinamn)


# =====================================================================
#  Beräkningar som regelmotorn bygger på
# =====================================================================
def test_innehavstid_raknas_i_manader():
    innehav = Innehav(ticker="VOLV-B.ST", kopdatum=dt.date(2024, 1, 15))
    manader = innehav.innehavstid_manader(idag=dt.date(2025, 1, 14))
    assert 11.8 < manader < 12.2


def test_innehavstid_ar_none_utan_innehav():
    assert Innehav().innehavstid_manader() is None


def test_beslutsfonster_kanner_igen_datum():
    inst = las_installningar(KONFIGKATALOG)
    assert inst.i_beslutsfonster(dt.date(2026, 2, 20)) is True   # efter bokslut
    assert inst.i_beslutsfonster(dt.date(2026, 5, 20)) is True   # efter Q1
    assert inst.i_beslutsfonster(dt.date(2026, 1, 20)) is False  # mitt i rapportfloden
    assert inst.i_beslutsfonster(dt.date(2026, 7, 10)) is False  # sommaren


def test_kategori_for_ticker():
    inst = las_installningar(KONFIGKATALOG)
    assert inst.kategori_for("VOLV-B.ST") == "Industri"
    assert inst.kategori_for("CAST.ST") == "Fastighet"
    assert inst.kategori_for("FINNS-INTE.ST") is None


# =====================================================================
#  Filhantering
# =====================================================================
def test_saknad_fil_ger_begripligt_fel(tmp_path):
    with pytest.raises(KonfigFel, match="Hittar inte konfigurationsfilen"):
        las_installningar(tmp_path)


def test_trasig_yaml_ger_begripligt_fel(tmp_path):
    (tmp_path / "config.yaml").write_text("universum:\n  min: 5\n   fel_indrag: 1\n")
    with pytest.raises(KonfigFel, match="YAML"):
        las_installningar(tmp_path)


def test_tom_fil_ger_begripligt_fel(tmp_path):
    (tmp_path / "config.yaml").write_text("")
    with pytest.raises(KonfigFel, match="är tom"):
        las_installningar(tmp_path)
