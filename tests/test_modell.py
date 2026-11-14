"""Tester för rangordningen och sammanvägningen.

Två saker testas hårdast:

* Saknad data hanteras genom OMVIKTNING, aldrig genom nollor. En nolla
  betyder "sämst i klassen", vilket är ett helt annat påstående än
  "vi vet inte".
* Omviktningen har en gräns. Går för lite av vikten att sätta får
  bolaget ingen poäng alls, i stället för en poäng som ser ut som ett
  svar utan att vara det.
"""

from __future__ import annotations

import datetime as dt

import pandas as pd
import pytest

from aktieanalys.data.bolagsdata import Bolagsdata
from aktieanalys.konfig import fran_dictar
from aktieanalys.poang.modell import percentilrank, poangsatt
from aktieanalys.universum import valj_urval

IDAG = dt.date(2026, 11, 13)
MDR, MKR = 1e9, 1e6


# =====================================================================
#  Percentilrank
# =====================================================================
def test_basta_far_hundra_samsta_far_noll():
    rank = percentilrank({"A": 10.0, "B": 20.0, "C": 30.0}, hogre_ar_battre=True)
    assert rank == {"A": 0.0, "B": 50.0, "C": 100.0}


def test_lagre_ar_battre_vander_ordningen():
    """Ett värderingsmått: lägst P/E ska få högst rank."""
    rank = percentilrank({"A": 10.0, "B": 20.0, "C": 30.0}, hogre_ar_battre=False)
    assert rank == {"A": 100.0, "B": 50.0, "C": 0.0}


def test_lika_varden_delar_pa_placeringen():
    rank = percentilrank({"A": 10.0, "B": 10.0, "C": 30.0}, hogre_ar_battre=True)
    assert rank["A"] == rank["B"] == 25.0
    assert rank["C"] == 100.0


def test_ensamt_bolag_far_neutral_rank():
    assert percentilrank({"A": 42.0}, hogre_ar_battre=True) == {"A": 50.0}


def test_tom_grupp_ger_tomt_resultat():
    assert percentilrank({}, hogre_ar_battre=True) == {}


def test_skalan_ar_oberoende_av_antal_bolag():
    """Sju bolag och sexton bolag ska ge jämförbara poäng."""
    fa = percentilrank({c: float(i) for i, c in enumerate("ABC")}, True)
    manga = percentilrank({c: float(i) for i, c in enumerate("ABCDEFGHIJ")}, True)
    assert max(fa.values()) == max(manga.values()) == 100.0
    assert min(fa.values()) == min(manga.values()) == 0.0


def test_negativa_varden_rangordnas_korrekt():
    """Nettokassa är negativ nettoskuld och ska ranka bäst."""
    rank = percentilrank({"A": -2.0, "B": 1.0, "C": 4.0}, hogre_ar_battre=False)
    assert rank["A"] == 100.0


# =====================================================================
#  Sammanvägning
# =====================================================================
def kursserie(start=100.0, slut=130.0, dagar=1200):
    """Knappt fem år, så att årsskifteskurserna för historikjämförelsen
    finns. Med bara 400 dagar föll ev_ebit_mot_egen_historik bort tyst
    och gjorde testerna missvisande."""
    datum = pd.bdate_range(end="2026-11-13", periods=dagar)
    steg = (slut - start) / (dagar - 1)
    return pd.Series([start + steg * i for i in range(dagar)], index=datum)


def bolag(ticker, roic_niva=0.20, ev_ebit_niva=15.0, momentum=130.0, **extra):
    """Ett standardbolag där nyckeltalen går att styra."""
    rorelseresultat = 10e9
    bd = Bolagsdata(ticker=ticker, hamtad=IDAG, namn=ticker, valuta="SEK")
    varden = {
        "borsvarde": 100 * MDR,
        "daglig_omsattning": 100 * MKR,
        "kurs": 100.0,
        "rorelseresultat": rorelseresultat,
        "investerat_kapital": rorelseresultat / roic_niva,
        "bolagsvarde": rorelseresultat * ev_ebit_niva,
        "vinst_per_aktie": 10.0,
        "nettoskuld": 5e9,
        "ebitda": 15e9,
        "antal_aktier": 1e9,
        "rorelseresultat_historik": [(2025, 10e9), (2024, 9.5e9), (2023, 9e9)],
        "omsattning_historik": [(2025, 50e9), (2024, 48e9), (2023, 45e9)],
        "vinst_per_aktie_historik": [(2025, 10.0), (2024, 9.5), (2023, 9.0)],
        "kurshistorik": kursserie(slut=momentum),
    }
    varden.update(extra)
    for f, v in varden.items():
        if v is not None:
            bd.satt(f, v, "test")
    return bd


@pytest.fixture
def inst(config_dict, universum_dict, portfolj_dict, tmp_path):
    universum_dict["kategorier"] = {
        "Industri": {"mattuppsattning": "standard",
                     "bolag": [f"B{i}.ST" for i in range(1, 7)]}
    }
    portfolj_dict["innehav"] = {"Industri": {"ticker": "", "kopdatum": ""}}
    return fran_dictar(config_dict, universum_dict, portfolj_dict, rotkatalog=tmp_path)


def kor(inst, data):
    urval = valj_urval(inst, data, IDAG)
    return poangsatt(inst, urval, data, IDAG)


def test_hogre_kvalitet_ger_hogre_kvalitetspoang(inst):
    data = {f"B{i}.ST": bolag(f"B{i}.ST", roic_niva=0.05 * i) for i in range(1, 7)}
    kp = kor(inst, data).kategorier["Industri"]

    basta = max(kp.bolag, key=lambda b: b.pelare["kvalitet"].poang)
    assert basta.ticker == "B6.ST"  # högst ROIC


def test_lagre_vardering_ger_hogre_varderingspoang(inst):
    data = {f"B{i}.ST": bolag(f"B{i}.ST", ev_ebit_niva=5.0 * i) for i in range(1, 7)}
    kp = kor(inst, data).kategorier["Industri"]

    basta = max(kp.bolag, key=lambda b: b.pelare["vardering"].poang)
    assert basta.ticker == "B1.ST"  # lägst EV/EBIT


def test_totalpoangen_ar_viktat_medelvarde_av_pelarna(inst):
    data = {f"B{i}.ST": bolag(f"B{i}.ST", roic_niva=0.05 * i) for i in range(1, 7)}
    kp = kor(inst, data).kategorier["Industri"]
    vikter = inst.config.poang.vikter.som_dict()

    for b in kp.bolag:
        vantat = sum(b.pelare[p].poang * v for p, v in vikter.items())
        assert b.totalpoang == pytest.approx(vantat, abs=0.01)


def test_bolagen_sorteras_med_hogst_poang_forst(inst):
    data = {f"B{i}.ST": bolag(f"B{i}.ST", roic_niva=0.05 * i) for i in range(1, 7)}
    kp = kor(inst, data).kategorier["Industri"]
    poang = [b.totalpoang for b in kp.bolag]
    assert poang == sorted(poang, reverse=True)


# =====================================================================
#  Saknad data: omviktning, aldrig nollor
# =====================================================================
def test_saknat_matt_viktas_om_inom_pelaren(inst):
    """B1 saknar P/E. Värderingspelaren ska räknas på de två andra
    måtten med uppräknade vikter -- inte med en nolla för P/E."""
    data = {f"B{i}.ST": bolag(f"B{i}.ST") for i in range(1, 7)}
    data["B1.ST"] = bolag("B1.ST", vinst_per_aktie=None)

    kp = kor(inst, data).kategorier["Industri"]
    b1 = next(b for b in kp.bolag if b.ticker == "B1.ST")
    vardering = b1.pelare["vardering"]

    pe = next(m for m in vardering.matt if m.namn == "pe")
    assert not pe.finns
    assert pe.anvand_vikt == 0.0
    # ev_ebit 0.45 och ev_ebit_mot_egen_historik 0.30 blir 0.60 och 0.40
    ev = next(m for m in vardering.matt if m.namn == "ev_ebit")
    assert ev.anvand_vikt == pytest.approx(0.45 / 0.75, abs=0.001)
    assert vardering.andel_vikt == pytest.approx(0.75, abs=0.001)
    assert vardering.poang is not None


def test_saknat_matt_ger_inte_noll_i_rank(inst):
    """Ett bolag som saknar ett mått ska inte straffas som om det vore
    sämst -- det ska bedömas på det som finns."""
    data = {f"B{i}.ST": bolag(f"B{i}.ST", ev_ebit_niva=10.0) for i in range(1, 7)}
    data["B1.ST"] = bolag("B1.ST", ev_ebit_niva=10.0, vinst_per_aktie=None)

    kp = kor(inst, data).kategorier["Industri"]
    b1 = next(b for b in kp.bolag if b.ticker == "B1.ST")
    andra = next(b for b in kp.bolag if b.ticker == "B2.ST")
    # Alla är likvärdiga på övriga mått, så poängen ska vara nära lika
    assert b1.pelare["vardering"].poang == pytest.approx(
        andra.pelare["vardering"].poang, abs=1.0
    )


def test_orsaken_till_saknat_matt_sparas(inst):
    data = {f"B{i}.ST": bolag(f"B{i}.ST") for i in range(1, 7)}
    data["B1.ST"] = bolag("B1.ST", ebitda=None)

    kp = kor(inst, data).kategorier["Industri"]
    b1 = next(b for b in kp.bolag if b.ticker == "B1.ST")
    styrka = b1.pelare["finansiell_styrka"]
    matt = styrka.matt[0]
    assert not matt.finns
    assert "ebitda" in matt.orsak


def test_tom_pelare_ger_none_inte_noll(inst):
    data = {f"B{i}.ST": bolag(f"B{i}.ST") for i in range(1, 7)}
    for i in range(1, 7):
        data[f"B{i}.ST"].varden.pop("kurshistorik")

    kp = kor(inst, data).kategorier["Industri"]
    for b in kp.bolag + kp.obedombara:
        assert b.pelare["momentum"].poang is None


# =====================================================================
#  Tröskeln för obedömbara bolag
# =====================================================================
def test_bolag_med_for_lite_data_blir_obedombart(
    config_dict, universum_dict, portfolj_dict, tmp_path
):
    """min_andel_poangvikt är 0.6. Faller kvalitet, värdering och
    styrka bort återstår 25 procent av vikten.

    Universumfiltret släpps på här (max_saknade_matt_per_bolag högt), så
    att det är POÄNGTRÖSKELN som testas och inte urvalet -- i skarp drift
    fångas ett så trasigt bolag redan av urvalet.
    """
    universum_dict["kategorier"] = {
        "Industri": {"mattuppsattning": "standard",
                     "bolag": [f"B{i}.ST" for i in range(1, 7)]}
    }
    portfolj_dict["innehav"] = {"Industri": {"ticker": "", "kopdatum": ""}}
    config_dict["universum"]["max_saknade_matt_per_bolag"] = 99
    inst = fran_dictar(config_dict, universum_dict, portfolj_dict, rotkatalog=tmp_path)

    data = {f"B{i}.ST": bolag(f"B{i}.ST") for i in range(1, 7)}
    trasigt = bolag("B1.ST")
    for falt in ("investerat_kapital", "rorelseresultat_historik",
                 "bolagsvarde", "vinst_per_aktie", "ebitda"):
        trasigt.varden.pop(falt, None)
    data["B1.ST"] = trasigt

    kp = kor(inst, data).kategorier["Industri"]
    assert "B1.ST" not in [b.ticker for b in kp.bolag]
    obedombart = next(b for b in kp.obedombara if b.ticker == "B1.ST")
    assert obedombart.totalpoang is None
    assert "av poängvikten" in obedombart.orsak


def test_bolag_strax_over_troskeln_bedoms(inst):
    """Faller bara finansiell styrka bort återstår 85 procent."""
    data = {f"B{i}.ST": bolag(f"B{i}.ST") for i in range(1, 7)}
    data["B1.ST"] = bolag("B1.ST", ebitda=None)

    kp = kor(inst, data).kategorier["Industri"]
    b1 = next(b for b in kp.bolag if b.ticker == "B1.ST")
    assert b1.bedombar
    assert b1.andel_vikt == pytest.approx(0.85, abs=0.001)


def test_troskeln_gar_att_andra(config_dict, universum_dict, portfolj_dict, tmp_path):
    universum_dict["kategorier"] = {
        "Industri": {"mattuppsattning": "standard",
                     "bolag": [f"B{i}.ST" for i in range(1, 7)]}
    }
    portfolj_dict["innehav"] = {"Industri": {"ticker": "", "kopdatum": ""}}
    config_dict["poang"]["min_andel_poangvikt"] = 0.9
    inst = fran_dictar(config_dict, universum_dict, portfolj_dict, rotkatalog=tmp_path)

    data = {f"B{i}.ST": bolag(f"B{i}.ST") for i in range(1, 7)}
    data["B1.ST"] = bolag("B1.ST", ebitda=None)  # 85 procent kvar

    kp = kor(inst, data).kategorier["Industri"]
    assert "B1.ST" in [b.ticker for b in kp.obedombara]


# =====================================================================
#  Innehavet
# =====================================================================
@pytest.fixture
def med_innehav(config_dict, universum_dict, portfolj_dict, tmp_path):
    universum_dict["kategorier"] = {
        "Industri": {"mattuppsattning": "standard",
                     "bolag": [f"B{i}.ST" for i in range(1, 7)]}
    }
    portfolj_dict["innehav"] = {
        "Industri": {"ticker": "B3.ST", "kopdatum": "2024-01-15"}
    }
    return fran_dictar(config_dict, universum_dict, portfolj_dict, rotkatalog=tmp_path)


def test_innehavet_markeras(med_innehav):
    data = {f"B{i}.ST": bolag(f"B{i}.ST", roic_niva=0.05 * i) for i in range(1, 7)}
    kp = kor(med_innehav, data).kategorier["Industri"]

    innehav = kp.innehavspoang()
    assert innehav is not None
    assert innehav.ticker == "B3.ST"
    assert innehav.ar_innehav
    assert kp.placering("B3.ST") is not None


def test_innehavet_finns_kvar_aven_som_obedombart(med_innehav):
    """Ett innehav utan data ska synas i rapporten, inte försvinna."""
    data = {f"B{i}.ST": bolag(f"B{i}.ST") for i in range(1, 7)}
    tomt = bolag("B3.ST")
    for falt in ("investerat_kapital", "rorelseresultat_historik", "bolagsvarde",
                 "vinst_per_aktie", "ebitda", "kurshistorik"):
        tomt.varden.pop(falt, None)
    data["B3.ST"] = tomt

    kp = kor(med_innehav, data).kategorier["Industri"]
    innehav = kp.innehavspoang()
    assert innehav is not None and innehav.ticker == "B3.ST"
    assert not innehav.bedombar


# =====================================================================
#  Toppurval och sammanfattning
# =====================================================================
def test_topp_ger_de_basta(inst):
    data = {f"B{i}.ST": bolag(f"B{i}.ST", roic_niva=0.05 * i) for i in range(1, 7)}
    kp = kor(inst, data).kategorier["Industri"]
    topp = kp.topp(3)
    assert len(topp) == 3
    assert topp[0].totalpoang >= topp[1].totalpoang >= topp[2].totalpoang


def test_poangtavlan_pa_verklig_data():
    from aktieanalys.konfig import las_installningar
    from aktieanalys.universum import las_och_valj
    from tests.conftest import KONFIGKATALOG

    inst = las_installningar(KONFIGKATALOG)
    if not inst.databasvag.exists():
        pytest.skip("ingen databas -- kör hamta-data först")

    urval, data = las_och_valj(inst)
    tavla = poangsatt(inst, urval, data)

    assert tavla.antal_bedomda > 40
    for namn, kp in tavla.kategorier.items():
        assert kp.rangordningsbar, namn
        for b in kp.bolag:
            assert 0 <= b.totalpoang <= 100, f"{b.ticker}: {b.totalpoang}"
            assert b.andel_vikt >= inst.config.poang.min_andel_poangvikt
        # Bästa och sämsta i varje kategori ska skilja sig åt
        assert kp.bolag[0].totalpoang > kp.bolag[-1].totalpoang, namn
