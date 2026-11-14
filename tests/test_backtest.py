"""Tester för backtestet.

Det enda som egentligen betyder något här är att ingen
framtidsinformation läcker in. Ett backtest som i smyg använder dagens
siffror på gamla år ser alltid bra ut och säger ingenting.

Därför är de hårdaste testerna de som konstruerar ett bolag vars
nuvarande siffror skiljer sig kraftigt från de historiska, och kräver
att rekonstruktionen använder de historiska.
"""

from __future__ import annotations

import datetime as dt

import pandas as pd
import pytest

from aktieanalys.backtest import (
    EJ_REKONSTRUERBARA,
    MEDVETET_UTELAMNADE,
    UR_HISTORIK,
    ar_kant,
    historisk_bolagsdata,
    kor_backtest,
)
from aktieanalys.data.bolagsdata import Bolagsdata
from aktieanalys.data.lager import Lager
from tests.test_byten import bygg


def kursserie(fran="2021-01-04", till="2026-11-13", start=100.0, slut=200.0):
    datum = pd.bdate_range(fran, till)
    steg = (slut - start) / (len(datum) - 1)
    return pd.Series([start + steg * i for i in range(len(datum))], index=datum)


def volymserie(fran="2021-01-04", till="2026-11-13", volym=1_000_000.0):
    datum = pd.bdate_range(fran, till)
    return pd.Series([volym] * len(datum), index=datum)


def bolag_med_historik(ticker="TEST.ST") -> Bolagsdata:
    """Ett bolag vars NUVARANDE siffror är drastiskt annorlunda än de
    historiska, så att look-ahead syns med en gång."""
    bd = Bolagsdata(ticker=ticker, hamtad=dt.date(2026, 11, 13), namn=ticker,
                    valuta="SEK", rapportvaluta="SEK")
    varden = {
        "kurshistorik": kursserie(),
        "volymhistorik": volymserie(),
        "kurs": 200.0,
        # Dagens siffror: mycket bättre än de gamla
        "rorelseresultat": 50e9,
        "nettoskuld": 1e9,
        "antal_aktier": 2e9,
        "eget_kapital": 100e9,
        "borsvarde": 400e9,
        "bolagsvarde": 401e9,
        # Historiken: svagare, och med färre aktier
        "rorelseresultat_historik": [
            (2025, 50e9), (2024, 20e9), (2023, 10e9), (2022, 5e9)],
        "nettoskuld_historik": [
            (2025, 1e9), (2024, 30e9), (2023, 40e9), (2022, 45e9)],
        "antal_aktier_historik": [
            (2025, 2e9), (2024, 1e9), (2023, 1e9), (2022, 1e9)],
        "eget_kapital_historik": [
            (2025, 100e9), (2024, 40e9), (2023, 35e9), (2022, 30e9)],
        "ebitda_historik": [(2025, 60e9), (2024, 25e9), (2023, 14e9), (2022, 8e9)],
        "omsattning_historik": [
            (2025, 200e9), (2024, 150e9), (2023, 140e9), (2022, 130e9)],
        "vinst_per_aktie_historik": [
            (2025, 20.0), (2024, 8.0), (2023, 4.0), (2022, 2.0)],
        "investerat_kapital_historik": [
            (2025, 150e9), (2024, 90e9), (2023, 85e9), (2022, 80e9)],
    }
    for f, v in varden.items():
        bd.satt(f, v, "test")
    return bd


# =====================================================================
#  Rapportfördröjningen
# =====================================================================
def test_bokslut_blir_kant_efter_fordrojningen():
    """2023 års bokslut antas offentligt från 1 april 2024."""
    assert not ar_kant(2023, dt.date(2024, 3, 31), 3)
    assert ar_kant(2023, dt.date(2024, 4, 1), 3)
    assert ar_kant(2023, dt.date(2025, 1, 1), 3)


def test_fordrojningen_gar_att_andra():
    assert not ar_kant(2023, dt.date(2024, 2, 1), 3)
    assert ar_kant(2023, dt.date(2024, 2, 1), 1)


def test_fordrojning_over_arsskiftet():
    """En fördröjning på 14 månader hamnar året därpå."""
    assert not ar_kant(2023, dt.date(2025, 2, 1), 14)
    assert ar_kant(2023, dt.date(2025, 3, 1), 14)


# =====================================================================
#  Ingen look-ahead
# =====================================================================
def test_enskilda_tal_tas_ur_det_senast_kanda_aret():
    """Det viktigaste testet i filen.

    Bolaget har 1 miljard i nettoskuld i dag men hade 40 miljarder 2023.
    Rekonstruerat till mitten av 2024 ska det ha 2023 års siffra.
    """
    bd = bolag_med_historik()
    h = historisk_bolagsdata(bd, dt.date(2024, 7, 1), 3)

    assert h.varde("nettoskuld") == 40e9, "dagens nettoskuld får inte användas"
    assert h.varde("rorelseresultat") == 10e9
    assert h.varde("eget_kapital") == 35e9
    assert h.varde("antal_aktier") == 1e9
    assert "2023" in h.kallor["nettoskuld"]


def test_arsserier_kapas_vid_det_som_var_kant():
    bd = bolag_med_historik()
    h = historisk_bolagsdata(bd, dt.date(2024, 7, 1), 3)

    ar = [a for a, _ in h.varde("rorelseresultat_historik")]
    assert ar == [2023, 2022], "2024 och 2025 var inte offentliga i juli 2024"


def test_kurshistoriken_kapas_vid_datumet():
    bd = bolag_med_historik()
    h = historisk_bolagsdata(bd, dt.date(2024, 7, 1), 3)
    serie = h.varde("kurshistorik")

    assert serie.index[-1].date() <= dt.date(2024, 7, 1)
    assert h.varde("kurs") == pytest.approx(float(serie.iloc[-1]))
    assert h.varde("kurs") < 200.0, "dagens kurs får inte användas"


def test_borsvarde_raknas_pa_dagens_kurs_och_dagens_aktieantal():
    """Aktieantalet ska vara det som gällde då, inte dagens."""
    bd = bolag_med_historik()
    h = historisk_bolagsdata(bd, dt.date(2024, 7, 1), 3)

    assert h.varde("borsvarde") == pytest.approx(h.varde("kurs") * 1e9)
    assert h.varde("borsvarde") != 400e9


def test_bolagsvarde_byggs_av_historiska_delar():
    bd = bolag_med_historik()
    h = historisk_bolagsdata(bd, dt.date(2024, 7, 1), 3)
    assert h.varde("bolagsvarde") == pytest.approx(h.varde("borsvarde") + 40e9)


def test_inget_kant_bokslut_ger_saknade_falt():
    bd = bolag_med_historik()
    h = historisk_bolagsdata(bd, dt.date(2023, 1, 10), 3)
    assert not h.har("rorelseresultat")
    assert "offentliggjort" in h.saknade["rorelseresultat_historik"]


def test_for_kort_kurshistorik_ger_none():
    bd = bolag_med_historik()
    assert historisk_bolagsdata(bd, dt.date(2021, 1, 10), 3) is None


def test_varje_falt_i_ur_historik_har_en_arsserie():
    """Ett fält som pekar på en serie som inte finns skulle tyst falla
    tillbaka på dagens värde."""
    from aktieanalys.data.bolagsdata import FALT

    for falt, historikfalt in UR_HISTORIK.items():
        assert falt in FALT, falt
        assert historikfalt in FALT, historikfalt
        assert FALT[historikfalt].typ == "arslista", historikfalt


def test_likviditeten_raknas_ur_den_historiska_volymen():
    """Dagens omsättning får inte användas på gamla år."""
    bd = bolag_med_historik()
    bd.satt("daglig_omsattning", 999e9, "test")   # orimligt dagens värde
    h = historisk_bolagsdata(bd, dt.date(2024, 7, 1), 3)

    assert h.har("daglig_omsattning")
    assert h.varde("daglig_omsattning") < 1e9, "dagens siffra får inte läcka in"
    assert "vid datumet" in h.kallor["daglig_omsattning"]


def test_utan_volymhistorik_blir_likviditeten_saknad():
    """Hellre uteslutet bolag än dagens omsättning påklistrad."""
    bd = bolag_med_historik()
    bd.varden.pop("volymhistorik")
    bd.satt("daglig_omsattning", 100e6, "test")
    h = historisk_bolagsdata(bd, dt.date(2024, 7, 1), 3)

    assert not h.har("daglig_omsattning")
    assert "hellre" in h.saknade["daglig_omsattning"]


def test_inga_falt_tar_dagens_varde_i_smyg():
    """EJ_REKONSTRUERBARA ska vara tom. Växer den måste varje post ha
    en förklaring som hamnar i rapportens varningar."""
    for falt, orsak in EJ_REKONSTRUERBARA.items():
        assert len(orsak) > 25, f"{falt} saknar en riktig förklaring"


def test_medvetet_utelamnade_har_forklaring_och_konsekvens():
    assert MEDVETET_UTELAMNADE, "minst kapitaltäckningen ska stå här"
    for falt, orsak in MEDVETET_UTELAMNADE.items():
        assert len(orsak) > 50, f"{falt}: förklaringen ska säga konsekvensen också"


def test_kapitaltackning_anvands_inte_historiskt():
    bd = bolag_med_historik()
    bd.satt("cet1_kvot", 0.18, "csv")
    h = historisk_bolagsdata(bd, dt.date(2024, 7, 1), 3)
    assert not h.har("cet1_kvot"), "dagens CET1 får inte användas på 2024"


# =====================================================================
#  Simuleringen
# =====================================================================
@pytest.fixture
def uppsattning(config_dict, universum_dict, portfolj_dict, tmp_path):
    inst = bygg(config_dict, universum_dict, portfolj_dict, tmp_path, innehav=None)
    data = {}
    for n, ticker in enumerate(inst.alla_tickers(), start=1):
        bd = bolag_med_historik(ticker)
        # Lite spridning, så att rangordningen betyder något
        bd.varden["kurshistorik"] = kursserie(slut=150.0 + n * 20)
        bd.varden["rorelseresultat_historik"] = [
            (ar, v * (0.8 + n * 0.1))
            for ar, v in bd.varde("rorelseresultat_historik")]
        data[ticker] = bd
    return inst, data


def test_backtestet_gar_att_kora(uppsattning, tmp_path):
    inst, data = uppsattning
    with Lager(tmp_path / "b.sqlite") as lager:
        r = kor_backtest(
            inst, data, lager, dt.date(2024, 6, 1), dt.date(2025, 6, 1),
            startkapital=300_000, fordrojning_manader=3, jamforelseindex=None,
        )
    assert len(r.varden) > 40, "en värdering per vecka"
    assert r.slutvarde > 0
    assert r.varningar, "varningarna ska alltid följa med resultatet"


def test_portfoljen_borjar_i_kassa_och_koper_i_beslutsfonster(
    uppsattning, tmp_path
):
    inst, data = uppsattning
    with Lager(tmp_path / "b.sqlite") as lager:
        r = kor_backtest(
            inst, data, lager, dt.date(2024, 6, 1), dt.date(2025, 6, 1),
            startkapital=300_000, fordrojning_manader=3,
        )
    kop = [a for a in r.affarer if a.typ == "kop"]
    assert kop, "förstagångsköp ska ske när ett beslutsfönster öppnar"
    for a in kop:
        assert inst.i_beslutsfonster(a.datum), "köp utanför fönstret"


def test_kostnader_dras_vid_varje_affar(uppsattning, tmp_path):
    inst, data = uppsattning
    with Lager(tmp_path / "b.sqlite") as lager:
        r = kor_backtest(
            inst, data, lager, dt.date(2024, 6, 1), dt.date(2025, 6, 1),
            startkapital=300_000, fordrojning_manader=3,
        )
    assert r.total_kostnad() > 0
    for a in r.affarer:
        assert a.kostnad > 0


def test_omsattning_per_ar_redovisas(uppsattning, tmp_path):
    inst, data = uppsattning
    with Lager(tmp_path / "b.sqlite") as lager:
        r = kor_backtest(
            inst, data, lager, dt.date(2024, 6, 1), dt.date(2025, 6, 1),
            startkapital=300_000, fordrojning_manader=3,
        )
    per_ar = r.per_ar()
    assert set(per_ar) == {2024, 2025}
    for rad in per_ar.values():
        assert rad["omsattning_procent"] >= 0


def test_portfoljen_i_konfigurationen_lamnas_orord(uppsattning, tmp_path):
    """Simuleringen pekar om innehavet medan den kör. Efteråt ska det
    vara som det var, annars förstörs den riktiga portföljen."""
    inst, data = uppsattning
    fore = {k: inst.innehav_i(k).ticker for k in inst.kategorinamn}
    with Lager(tmp_path / "b.sqlite") as lager:
        kor_backtest(inst, data, lager, dt.date(2024, 6, 1), dt.date(2024, 9, 1),
                     startkapital=300_000, fordrojning_manader=3)
    assert {k: inst.innehav_i(k).ticker for k in inst.kategorinamn} == fore


def test_avkastning_och_arsavkastning_haller_ihop(uppsattning, tmp_path):
    inst, data = uppsattning
    with Lager(tmp_path / "b.sqlite") as lager:
        r = kor_backtest(
            inst, data, lager, dt.date(2024, 6, 1), dt.date(2026, 6, 1),
            startkapital=300_000, fordrojning_manader=3,
        )
    sammansatt = (1 + r.arsavkastning / 100) ** r.ar
    assert sammansatt == pytest.approx(1 + r.avkastning / 100, rel=0.02)
