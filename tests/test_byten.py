"""Tester för bytesreglerna.

Reglerna finns för att hålla omsättningen låg, så testerna är skrivna
för att visa att de verkligen SPÄRRAR. Varje villkor testas ensamt:
uppfylls tre av fyra ska inget byte föreslås.

Undantaget vid fundamental försämring testas särskilt noga -- det är
den enda vägen förbi spärrarna, och den får inte gå för långt.
"""

from __future__ import annotations

import datetime as dt

import pandas as pd
import pytest

from aktieanalys.byten import (
    bedom_byten,
    berakna_kostnad,
    rakna_veckor_i_rad,
    spara_veckans_poang,
)
from aktieanalys.data.bolagsdata import Bolagsdata
from aktieanalys.data.lager import Lager
from aktieanalys.konfig import fran_dictar
from aktieanalys.kvalitativ.klient import Kvalitativtresultat
from aktieanalys.kvalitativ.schema import Bedomning
from aktieanalys.poang.modell import poangsatt
from aktieanalys.universum import valj_urval

MDR, MKR = 1e9, 1e6
I_FONSTER = dt.date(2026, 11, 20)      # inom 11-15 -- 12-15
UTANFOR = dt.date(2026, 11, 14)        # dagen innan fönstret öppnar


def kursserie(dagar=1200, slut=130.0):
    datum = pd.bdate_range(end="2026-11-20", periods=dagar)
    steg = (slut - 100.0) / (dagar - 1)
    return pd.Series([100.0 + steg * i for i in range(dagar)], index=datum)


def bolag(ticker: str, roic=0.20, kop=None, salj=None) -> Bolagsdata:
    bd = Bolagsdata(ticker=ticker, hamtad=I_FONSTER, namn=ticker, valuta="SEK")
    varden = {
        "borsvarde": 50 * MDR, "daglig_omsattning": 100 * MKR, "kurs": 100.0,
        "rorelseresultat": 10e9, "investerat_kapital": 10e9 / roic,
        "bolagsvarde": 150e9, "vinst_per_aktie": 10.0, "nettoskuld": 5e9,
        "ebitda": 15e9, "antal_aktier": 1e9,
        "rorelseresultat_historik": [(2025, 10e9), (2024, 9.5e9), (2023, 9e9)],
        "omsattning_historik": [(2025, 50e9), (2024, 48e9), (2023, 45e9)],
        "vinst_per_aktie_historik": [(2025, 10.0), (2024, 9.5), (2023, 9.0)],
        "kurshistorik": kursserie(),
    }
    if kop is not None:
        varden["kopkurs"], varden["saljkurs"] = kop, salj
    for f, v in varden.items():
        bd.satt(f, v, "test")
    return bd


def bygg(config_dict, universum_dict, portfolj_dict, tmp_path,
         innehav="B1.ST", kopdatum="2024-01-15", **config_overrides):
    universum_dict["kategorier"] = {
        "Industri": {"mattuppsattning": "standard",
                     "bolag": [f"B{i}.ST" for i in range(1, 7)]}
    }
    portfolj_dict["innehav"] = {
        "Industri": {"ticker": innehav, "kopdatum": kopdatum} if innehav
        else {"ticker": "", "kopdatum": ""}
    }
    for nyckel, varde in config_overrides.items():
        grupp, _, falt = nyckel.partition("__")
        config_dict[grupp][falt] = varde
    config_dict["data"]["databas"] = str(tmp_path / "t.sqlite")
    return fran_dictar(config_dict, universum_dict, portfolj_dict, rotkatalog=tmp_path)


def scenario(inst, innehav_roic=0.05, utmanare_roic=0.30, datum=I_FONSTER):
    """B1 är innehavet (svagt), B6 är bästa utmanaren (starkt)."""
    data = {f"B{i}.ST": bolag(f"B{i}.ST", roic=0.05 + 0.01 * i) for i in range(2, 6)}
    data["B1.ST"] = bolag("B1.ST", roic=innehav_roic)
    data["B6.ST"] = bolag("B6.ST", roic=utmanare_roic)
    tavla = poangsatt(inst, valj_urval(inst, data, datum), data, datum)
    return tavla, data


# =====================================================================
#  Kostnadsberäkningen
# =====================================================================
def test_courtage_pa_bada_affarerna(config_dict, universum_dict, portfolj_dict, tmp_path):
    inst = bygg(config_dict, universum_dict, portfolj_dict, tmp_path)
    k = berakna_kostnad(inst, bolag("B1.ST"), bolag("B6.ST"), saljer=True)

    assert k.courtage_salj == pytest.approx(125.0)   # 0,25 % av 50 000
    assert k.courtage_kop == pytest.approx(125.0)
    assert k.total_kr > 250


def test_forstagangskop_har_bara_kopcourtage(
    config_dict, universum_dict, portfolj_dict, tmp_path
):
    inst = bygg(config_dict, universum_dict, portfolj_dict, tmp_path)
    k = berakna_kostnad(inst, None, bolag("B6.ST"), saljer=False)
    assert k.courtage_salj == 0.0
    assert k.courtage_kop > 0


def test_minimicourtaget_galler_vid_sma_belopp(
    config_dict, universum_dict, portfolj_dict, tmp_path
):
    inst = bygg(config_dict, universum_dict, portfolj_dict, tmp_path,
                kostnader__antagen_positionsstorlek_kr=200.0)
    k = berakna_kostnad(inst, bolag("B1.ST"), bolag("B6.ST"))
    assert k.courtage_kop == 1.0   # 0,25 % av 200 kr = 0,50 -> minst 1 kr


def test_uppmatt_spread_anvands_nar_den_overstiger_golvet(
    config_dict, universum_dict, portfolj_dict, tmp_path
):
    inst = bygg(config_dict, universum_dict, portfolj_dict, tmp_path)
    # 1 procents spread: köp 99,5 / sälj 100,5
    k = berakna_kostnad(inst, bolag("B1.ST", kop=99.5, salj=100.5),
                        bolag("B6.ST", kop=99.5, salj=100.5))
    assert k.spread_procent == pytest.approx(1.0, abs=0.01)
    assert "uppmätt" in k.spread_kalla


def test_golvet_anvands_nar_spreaden_ar_mindre(
    config_dict, universum_dict, portfolj_dict, tmp_path
):
    inst = bygg(config_dict, universum_dict, portfolj_dict, tmp_path)
    k = berakna_kostnad(inst, bolag("B1.ST", kop=99.99, salj=100.01),
                        bolag("B6.ST", kop=99.99, salj=100.01))
    assert k.spread_procent == 0.15
    assert "golvet" in k.spread_kalla


def test_golvet_anvands_utan_kursdata(
    config_dict, universum_dict, portfolj_dict, tmp_path
):
    inst = bygg(config_dict, universum_dict, portfolj_dict, tmp_path)
    k = berakna_kostnad(inst, bolag("B1.ST"), bolag("B6.ST"))
    assert k.spread_procent == 0.15
    assert "ingen kursdata" in k.spread_kalla


def test_verklig_positionsstorlek_anvands_nar_den_finns(
    config_dict, universum_dict, portfolj_dict, tmp_path
):
    inst = bygg(config_dict, universum_dict, portfolj_dict, tmp_path)
    k = berakna_kostnad(inst, bolag("B1.ST"), bolag("B6.ST"), antal=1000)
    assert k.belopp_kr == 100_000.0   # 1000 aktier x 100 kr
    assert k.kand_positionsstorlek


def test_hog_kostnad_flaggas_vid_bred_spread(
    config_dict, universum_dict, portfolj_dict, tmp_path
):
    """En illikvid aktie med 3 procents spread kostar mer än courtaget."""
    inst = bygg(config_dict, universum_dict, portfolj_dict, tmp_path)
    k = berakna_kostnad(inst, bolag("B1.ST", kop=98.5, salj=101.5),
                        bolag("B6.ST", kop=98.5, salj=101.5))
    assert k.spread_procent == pytest.approx(3.0, abs=0.01)
    assert k.spread_kr > k.courtage_salj + k.courtage_kop
    assert k.hog(1.0)


def test_normal_kostnad_flaggas_inte(
    config_dict, universum_dict, portfolj_dict, tmp_path
):
    """50 000 kr i en likvid aktie: courtage 2 x 125 kr plus spread 75 kr
    för hela vändan (halva spreaden per affär, två affärer)."""
    inst = bygg(config_dict, universum_dict, portfolj_dict, tmp_path)
    k = berakna_kostnad(inst, bolag("B1.ST"), bolag("B6.ST"))
    assert k.spread_kr == pytest.approx(75.0)
    assert k.total_kr == pytest.approx(325.0)
    assert k.total_procent == pytest.approx(0.65, abs=0.01)
    assert not k.hog(1.0)


def test_minimicourtaget_gor_sma_poster_dyra(
    config_dict, universum_dict, portfolj_dict, tmp_path
):
    """Vid 200 kr äter minimicourtaget upp mer än en procent."""
    inst = bygg(config_dict, universum_dict, portfolj_dict, tmp_path,
                kostnader__antagen_positionsstorlek_kr=200.0)
    k = berakna_kostnad(inst, bolag("B1.ST"), bolag("B6.ST"))
    assert k.hog(1.0)


# =====================================================================
#  Uthållighet
# =====================================================================
@pytest.fixture
def lager(tmp_path):
    with Lager(tmp_path / "v.sqlite") as l:
        yield l


def logga(lager, datum, innehavspoang, utmanarpoang):
    lager.spara_veckopoang(datum, [
        {"ticker": "B1.ST", "kategori": "Industri",
         "poang_slutlig": innehavspoang, "poang_kvantitativ": innehavspoang,
         "placering": 2, "ar_innehav": True},
        {"ticker": "B6.ST", "kategori": "Industri",
         "poang_slutlig": utmanarpoang, "poang_kvantitativ": utmanarpoang,
         "placering": 1, "ar_innehav": False},
    ])


def test_raknar_veckor_i_rad(lager):
    for n, d in enumerate([dt.date(2026, 11, 13), dt.date(2026, 11, 6),
                           dt.date(2026, 10, 30)]):
        logga(lager, d, 50.0, 70.0)        # 40 procent bättre
    antal, datum = rakna_veckor_i_rad(
        lager, "Industri", "B6.ST", "B1.ST", 20.0, dt.date(2026, 11, 20))
    assert antal == 3
    assert datum[0] == dt.date(2026, 11, 13)


def test_raknandet_bryts_vid_forsta_veckan_som_inte_holl(lager):
    logga(lager, dt.date(2026, 11, 13), 50.0, 70.0)   # håller
    logga(lager, dt.date(2026, 11, 6), 50.0, 52.0)    # håller inte
    logga(lager, dt.date(2026, 10, 30), 50.0, 70.0)   # håller, men för sent

    antal, _ = rakna_veckor_i_rad(
        lager, "Industri", "B6.ST", "B1.ST", 20.0, dt.date(2026, 11, 20))
    assert antal == 1


def test_dagens_datum_raknas_inte_i_historiken(lager):
    """Dagens poäng kommer från den färska körningen, inte databasen."""
    logga(lager, dt.date(2026, 11, 20), 50.0, 70.0)
    antal, _ = rakna_veckor_i_rad(
        lager, "Industri", "B6.ST", "B1.ST", 20.0, dt.date(2026, 11, 20))
    assert antal == 0


def test_tom_historik_ger_noll(lager):
    antal, datum = rakna_veckor_i_rad(
        lager, "Industri", "B6.ST", "B1.ST", 20.0, dt.date(2026, 11, 20))
    assert (antal, datum) == (0, [])


def test_saknad_poang_bryter_raknandet(lager):
    """Ett bolag som inte fanns i urvalet en vecka bryter kedjan."""
    lager.spara_veckopoang(dt.date(2026, 11, 13), [
        {"ticker": "B1.ST", "kategori": "Industri", "poang_slutlig": 50.0,
         "placering": 1, "ar_innehav": True}])
    antal, _ = rakna_veckor_i_rad(
        lager, "Industri", "B6.ST", "B1.ST", 20.0, dt.date(2026, 11, 20))
    assert antal == 0


# =====================================================================
#  Reglerna: varje villkor spärrar ensamt
# =====================================================================
def kor(inst, tavla, data, lager, kvalitativt=None, datum=I_FONSTER):
    return bedom_byten(inst, tavla, data, lager, kvalitativt, datum).kategorier["Industri"]


def fyll_historik(lager, veckor=3, innehav=40.0, utmanare=80.0):
    for n in range(1, veckor + 1):
        logga(lager, I_FONSTER - dt.timedelta(days=7 * n), innehav, utmanare)


def test_alla_villkor_uppfyllda_ger_forslag(
    config_dict, universum_dict, portfolj_dict, tmp_path, lager
):
    inst = bygg(config_dict, universum_dict, portfolj_dict, tmp_path)
    tavla, data = scenario(inst)
    fyll_historik(lager)

    kb = kor(inst, tavla, data, lager)
    assert kb.foreslas, [v.text for v in kb.ej_uppfyllda]
    assert kb.typ == "byte"
    assert kb.utmanare == "B6.ST"
    assert kb.veckor_i_rad >= 3


def test_for_liten_forbattring_sparrar(
    config_dict, universum_dict, portfolj_dict, tmp_path, lager
):
    inst = bygg(config_dict, universum_dict, portfolj_dict, tmp_path)
    # Innehavet nästan lika bra som utmanaren
    tavla, data = scenario(inst, innehav_roic=0.28, utmanare_roic=0.30)
    fyll_historik(lager)

    kb = kor(inst, tavla, data, lager)
    assert not kb.foreslas
    assert any(v.namn == "tröskel" for v in kb.ej_uppfyllda)


def test_for_fa_veckor_i_rad_sparrar(
    config_dict, universum_dict, portfolj_dict, tmp_path, lager
):
    inst = bygg(config_dict, universum_dict, portfolj_dict, tmp_path)
    tavla, data = scenario(inst)
    fyll_historik(lager, veckor=1)   # bara en tidigare vecka

    kb = kor(inst, tavla, data, lager)
    assert not kb.foreslas
    assert any(v.namn == "uthållighet" for v in kb.ej_uppfyllda)
    assert kb.veckor_i_rad == 2      # denna vecka plus en


def test_for_kort_innehavstid_sparrar(
    config_dict, universum_dict, portfolj_dict, tmp_path, lager
):
    nyss = (I_FONSTER - dt.timedelta(days=60)).isoformat()
    inst = bygg(config_dict, universum_dict, portfolj_dict, tmp_path, kopdatum=nyss)
    tavla, data = scenario(inst)
    fyll_historik(lager)

    kb = kor(inst, tavla, data, lager)
    assert not kb.foreslas
    assert any(v.namn == "innehavstid" for v in kb.ej_uppfyllda)


def test_utanfor_beslutsfonster_sparrar(
    config_dict, universum_dict, portfolj_dict, tmp_path, lager
):
    inst = bygg(config_dict, universum_dict, portfolj_dict, tmp_path)
    tavla, data = scenario(inst, datum=UTANFOR)
    fyll_historik(lager)

    kb = kor(inst, tavla, data, lager, datum=UTANFOR)
    assert not kb.foreslas
    assert any(v.namn == "beslutsfönster" for v in kb.ej_uppfyllda)


def test_alla_villkor_redovisas_aven_nar_de_haller(
    config_dict, universum_dict, portfolj_dict, tmp_path, lager
):
    """Rapporten ska visa hela bilden, inte bara det som brister."""
    inst = bygg(config_dict, universum_dict, portfolj_dict, tmp_path)
    tavla, data = scenario(inst)
    fyll_historik(lager, veckor=1)

    kb = kor(inst, tavla, data, lager)
    namn = {v.namn for v in kb.villkor}
    assert namn == {"tröskel", "uthållighet", "innehavstid", "beslutsfönster"}
    assert kb.forbattring_procent > 20, "förbättringen ska synas även utan förslag"


# =====================================================================
#  Undantaget vid fundamental försämring
# =====================================================================
def forsamring(ticker="B1.ST") -> Kvalitativtresultat:
    r = Kvalitativtresultat()
    r.bedomningar[ticker] = Bedomning(
        ticker=ticker, justering=-8.0, motivering="Vinstvarning i oktober.",
        anvand=True, fundamental_forsamring=True,
    )
    return r


def test_forsamring_asidosatter_innehavstid(
    config_dict, universum_dict, portfolj_dict, tmp_path, lager
):
    nyss = (I_FONSTER - dt.timedelta(days=30)).isoformat()
    inst = bygg(config_dict, universum_dict, portfolj_dict, tmp_path, kopdatum=nyss)
    tavla, data = scenario(inst)
    fyll_historik(lager)

    kb = kor(inst, tavla, data, lager, kvalitativt=forsamring())
    assert kb.foreslas
    assert kb.undantag and "fundamental försämring" in kb.undantag
    innehavstid = next(v for v in kb.villkor if v.namn == "innehavstid")
    assert not innehavstid.uppfyllt and innehavstid.undantaget


def test_forsamring_asidosatter_beslutsfonstret(
    config_dict, universum_dict, portfolj_dict, tmp_path, lager
):
    inst = bygg(config_dict, universum_dict, portfolj_dict, tmp_path)
    tavla, data = scenario(inst, datum=UTANFOR)
    fyll_historik(lager)

    kb = kor(inst, tavla, data, lager, kvalitativt=forsamring(), datum=UTANFOR)
    assert kb.foreslas


def test_forsamring_asidosatter_INTE_troskeln(
    config_dict, universum_dict, portfolj_dict, tmp_path, lager
):
    """En försämring i det egna innehavet gör inte någon annan värd att köpa."""
    inst = bygg(config_dict, universum_dict, portfolj_dict, tmp_path)
    tavla, data = scenario(inst, innehav_roic=0.28, utmanare_roic=0.30)
    fyll_historik(lager)

    kb = kor(inst, tavla, data, lager, kvalitativt=forsamring())
    assert not kb.foreslas
    assert any(v.namn == "tröskel" for v in kb.ej_uppfyllda)


def test_forsamring_asidosatter_INTE_uthalligheten(
    config_dict, universum_dict, portfolj_dict, tmp_path, lager
):
    inst = bygg(config_dict, universum_dict, portfolj_dict, tmp_path)
    tavla, data = scenario(inst)
    # ingen historik alls

    kb = kor(inst, tavla, data, lager, kvalitativt=forsamring())
    assert not kb.foreslas
    assert any(v.namn == "uthållighet" for v in kb.ej_uppfyllda)


def test_forsamring_i_UTMANAREN_ger_inget_undantag(
    config_dict, universum_dict, portfolj_dict, tmp_path, lager
):
    """Undantaget gäller försämring i det egna innehavet, inte hos andra."""
    nyss = (I_FONSTER - dt.timedelta(days=30)).isoformat()
    inst = bygg(config_dict, universum_dict, portfolj_dict, tmp_path, kopdatum=nyss)
    tavla, data = scenario(inst)
    fyll_historik(lager)

    kb = kor(inst, tavla, data, lager, kvalitativt=forsamring("B6.ST"))
    assert not kb.foreslas
    assert kb.undantag is None


def test_undantaget_gar_att_stanga_av(
    config_dict, universum_dict, portfolj_dict, tmp_path, lager
):
    nyss = (I_FONSTER - dt.timedelta(days=30)).isoformat()
    inst = bygg(config_dict, universum_dict, portfolj_dict, tmp_path, kopdatum=nyss,
                byten__tillat_undantag_vid_riskflagga=False)
    tavla, data = scenario(inst)
    fyll_historik(lager)

    kb = kor(inst, tavla, data, lager, kvalitativt=forsamring())
    assert not kb.foreslas
    assert kb.undantag is None


# =====================================================================
#  Förstagångsköp och specialfall
# =====================================================================
def test_tom_kategori_ger_forstagangskop(
    config_dict, universum_dict, portfolj_dict, tmp_path, lager
):
    inst = bygg(config_dict, universum_dict, portfolj_dict, tmp_path, innehav=None)
    tavla, data = scenario(inst)

    kb = kor(inst, tavla, data, lager)
    assert kb.typ == "forstagangskop"
    assert kb.foreslas
    assert kb.utmanare == tavla.kategorier["Industri"].bolag[0].ticker
    assert kb.kostnad.courtage_salj == 0.0


def test_forstagangskop_sparras_utanfor_fonstret(
    config_dict, universum_dict, portfolj_dict, tmp_path, lager
):
    inst = bygg(config_dict, universum_dict, portfolj_dict, tmp_path, innehav=None)
    tavla, data = scenario(inst, datum=UTANFOR)

    kb = kor(inst, tavla, data, lager, datum=UTANFOR)
    assert not kb.foreslas


def test_kvalitativ_justering_paverkar_jamforelsen(
    config_dict, universum_dict, portfolj_dict, tmp_path, lager
):
    """Sänks utmanaren av en riskflagga kan tröskeln sluta hålla."""
    inst = bygg(config_dict, universum_dict, portfolj_dict, tmp_path)
    tavla, data = scenario(inst, innehav_roic=0.20, utmanare_roic=0.30)
    fyll_historik(lager)

    utan = kor(inst, tavla, data, lager)

    sankt = Kvalitativtresultat()
    sankt.bedomningar["B6.ST"] = Bedomning(
        ticker="B6.ST", justering=-10.0, motivering="Regulatorisk utredning inledd.",
        anvand=True,
    )
    med = kor(inst, tavla, data, lager, kvalitativt=sankt)
    assert med.forbattring_procent < utan.forbattring_procent


# =====================================================================
#  Veckologgen
# =====================================================================
def test_spara_veckans_poang_gar_att_lasa_tillbaka(
    config_dict, universum_dict, portfolj_dict, tmp_path, lager
):
    inst = bygg(config_dict, universum_dict, portfolj_dict, tmp_path)
    tavla, data = scenario(inst)

    antal = spara_veckans_poang(lager, tavla)
    assert antal == len(tavla.kategorier["Industri"].bolag)

    rader = lager.veckopoang_vid(I_FONSTER, "Industri")
    assert rader["B1.ST"]["ar_innehav"] is True
    assert rader["B6.ST"]["placering"] == 1


def test_omkorning_samma_dag_skriver_over(
    config_dict, universum_dict, portfolj_dict, tmp_path, lager
):
    inst = bygg(config_dict, universum_dict, portfolj_dict, tmp_path)
    tavla, data = scenario(inst)
    spara_veckans_poang(lager, tavla)
    spara_veckans_poang(lager, tavla)
    assert len(lager.rapportdatum("Industri")) == 1


def test_justeringen_sparas_med(
    config_dict, universum_dict, portfolj_dict, tmp_path, lager
):
    inst = bygg(config_dict, universum_dict, portfolj_dict, tmp_path)
    tavla, data = scenario(inst)
    spara_veckans_poang(lager, tavla, forsamring())

    rader = lager.veckopoang_vid(I_FONSTER, "Industri")
    assert rader["B1.ST"]["justering"] == -8.0
    assert rader["B1.ST"]["poang_slitlig" if False else "poang_slutlig"] < \
           rader["B1.ST"]["poang_kvantitativ"]
