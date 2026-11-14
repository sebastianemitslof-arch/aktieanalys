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


# =====================================================================
#  Textdump: databasen i en form git kan hantera
# =====================================================================
def test_dumpen_gar_att_aterskapa(lager, tmp_path):
    from aktieanalys.data.lager import aterskapa

    lager.spara(bolag(dt.date(2026, 11, 13), 100.0, nettoskuld=5e9,
                      rorelseresultat_historik=[(2025, 1e9), (2024, 0.9e9)]))
    lager.spara_kurser("TEST.ST", pd.Series(
        [98.0, 99.0], index=pd.to_datetime(["2026-11-12", "2026-11-13"])))
    lager.logga_hamtning("TEST.ST", dt.date(2026, 11, 13), "test", "ok", 3)
    lager.spara_veckopoang(dt.date(2026, 11, 13), [
        {"ticker": "TEST.ST", "kategori": "Industri", "poang_slutlig": 61.2,
         "poang_kvantitativ": 60.0, "placering": 1, "ar_innehav": True,
         "justering": 2.0}])
    fore = lager.statistik()

    dump = tmp_path / "d.sql"
    lager.dumpa(dump)
    aterskapa(dump, tmp_path / "ny.sqlite")

    with Lager(tmp_path / "ny.sqlite") as ny:
        assert ny.statistik() == fore
        tillbaka = ny.las("TEST.ST", dt.date(2026, 11, 13))
        assert tillbaka.varde("kurs") == 100.0
        assert tillbaka.varde("rorelseresultat_historik") == [
            (2025, 1e9), (2024, 0.9e9)]
        assert len(ny.las_kurser("TEST.ST")) == 2
        assert ny.veckopoang_vid(dt.date(2026, 11, 13), "Industri")[
            "TEST.ST"]["justering"] == 2.0


def test_dumpen_ar_deterministisk(lager, tmp_path):
    """Samma databas ska ge exakt samma fil varje gång."""
    for d in [dt.date(2026, 11, 6), dt.date(2026, 11, 13)]:
        lager.spara(bolag(d, 100.0))
    a, b = tmp_path / "a.sql", tmp_path / "b.sql"
    lager.dumpa(a)
    lager.dumpa(b)

    rader_a = [r for r in a.read_text().splitlines() if r.startswith("INSERT")]
    rader_b = [r for r in b.read_text().splitlines() if r.startswith("INSERT")]
    assert rader_a == rader_b


def test_overskriven_rad_flyttar_sig_inte_i_dumpen(lager, tmp_path):
    """Hela vinsten med textdumpen bygger på att den är sorterad.

    Kör man om en vecka skrivs raderna över. Hamnade de då sist i filen
    skulle git se en stor omkastning i stället för en liten ändring, och
    repot skulle växa lika fort som med en binär databas.
    """
    for t in ["AAA.ST", "BBB.ST", "CCC.ST"]:
        bd = Bolagsdata(ticker=t, hamtad=dt.date(2026, 11, 13))
        bd.satt("kurs", 100.0, "test")
        lager.spara(bd)

    fore = tmp_path / "fore.sql"
    lager.dumpa(fore)

    # Skriv om mittenbolagets värde, som vid en omkörning samma dag
    bd = Bolagsdata(ticker="BBB.ST", hamtad=dt.date(2026, 11, 13))
    bd.satt("kurs", 123.0, "test")
    lager.spara(bd)

    efter = tmp_path / "efter.sql"
    lager.dumpa(efter)

    rader_fore = fore.read_text().splitlines()
    rader_efter = efter.read_text().splitlines()
    assert len(rader_fore) == len(rader_efter)

    olika = [i for i, (a, b) in enumerate(zip(rader_fore, rader_efter)) if a != b]
    assert len(olika) == 1, "bara en rad ska ha ändrats, inte flyttats om"
    assert "BBB.ST" in rader_efter[olika[0]]
    assert "123.0" in rader_efter[olika[0]]


def test_ny_vecka_blir_ett_litet_tillagg(lager, tmp_path):
    """En ny vecka ska lägga till rader, inte kasta om de gamla."""
    for t in ["AAA.ST", "BBB.ST"]:
        bd = Bolagsdata(ticker=t, hamtad=dt.date(2026, 11, 6))
        bd.satt("kurs", 100.0, "test")
        lager.spara(bd)
    v1 = tmp_path / "v1.sql"
    lager.dumpa(v1)

    for t in ["AAA.ST", "BBB.ST"]:
        bd = Bolagsdata(ticker=t, hamtad=dt.date(2026, 11, 13))
        bd.satt("kurs", 110.0, "test")
        lager.spara(bd)
    v2 = tmp_path / "v2.sql"
    lager.dumpa(v2)

    gamla = set(v1.read_text().splitlines())
    nya = set(v2.read_text().splitlines())
    assert gamla <= nya, "inga gamla rader ska ha ändrats"
    # Två nya kursvärden plus två rader i bolagsmeta (ett bolag, ett datum).
    assert len(nya - gamla) == 4, "bara veckans egna rader ska tillkomma"


def test_dumpen_tar_inte_med_sqlites_egna_tabeller(lager, tmp_path):
    """sqlite_sequence skapas av motorn och går inte att skapa om."""
    lager.logga_hamtning("A.ST", dt.date(2026, 11, 13), "test", "ok")
    dump = tmp_path / "d.sql"
    lager.dumpa(dump)
    assert "sqlite_sequence" not in dump.read_text()


def test_aterskapning_utan_dump_ger_fel(tmp_path):
    from aktieanalys.data.lager import aterskapa

    with pytest.raises(FileNotFoundError):
        aterskapa(tmp_path / "finns-inte.sql", tmp_path / "ny.sqlite")


def test_aterskapning_ersatter_befintlig_databas(lager, tmp_path):
    from aktieanalys.data.lager import aterskapa

    lager.spara(bolag(dt.date(2026, 11, 13), 100.0))
    dump = tmp_path / "d.sql"
    lager.dumpa(dump)

    mal = tmp_path / "mal.sqlite"
    with Lager(mal) as gammal:
        gammal.spara(bolag(dt.date(2026, 11, 13), 999.0))
    aterskapa(dump, mal)

    with Lager(mal) as ny:
        assert ny.las("TEST.ST", dt.date(2026, 11, 13)).varde("kurs") == 100.0


def test_den_verkliga_databasen_gar_att_dumpa_och_aterskapa(tmp_path):
    from aktieanalys.data.lager import aterskapa
    from aktieanalys.konfig import las_installningar
    from tests.conftest import KONFIGKATALOG

    inst = las_installningar(KONFIGKATALOG)
    if not inst.databasvag.exists():
        pytest.skip("ingen databas -- kör hamta-data först")

    dump = tmp_path / "d.sql"
    with Lager(inst.databasvag) as original:
        original.dumpa(dump)
        fore = original.statistik()

    aterskapa(dump, tmp_path / "ny.sqlite")
    with Lager(tmp_path / "ny.sqlite") as ny:
        efter = ny.statistik()

    for nyckel in ["bolag", "varden", "kursrader", "saknade_varden",
                   "hamtningar", "forsta_datum", "senaste_datum"]:
        assert fore[nyckel] == efter[nyckel], nyckel


# =====================================================================
#  Fältversionen: "färsk" måste betyda "komplett" också
# =====================================================================
def test_farsk_data_med_gammal_faltuppsattning_hamtas_om(lager):
    """Fem fastighetsbolag lästes ur cachen utan de balansposter
    härledningarna behöver, och uteslöts för saknade mått trots att
    siffrorna fanns hos Yahoo. Datumet var färskt; fälten var det inte."""
    from aktieanalys.data.bolagsdata import FALTVERSION

    lager.spara(bolag(dt.date(2026, 11, 13), 100.0))
    assert lager.faltversion("TEST.ST") == FALTVERSION
    assert lager.ar_farsk("TEST.ST", 5, dt.date(2026, 11, 13))

    # Simulera data hämtad under en äldre fältuppsättning
    lager.db.execute(
        "UPDATE bolagsmeta SET faltversion = ? WHERE ticker = ?",
        (FALTVERSION - 1, "TEST.ST"),
    )
    lager.db.commit()
    assert not lager.ar_farsk("TEST.ST", 5, dt.date(2026, 11, 13)), (
        "data hämtad med äldre fältuppsättning ska räknas som inaktuell"
    )


def test_okant_bolag_har_version_noll(lager):
    assert lager.faltversion("FINNS-INTE.ST") == 0


def test_gammal_databas_far_kolumnen_tillagd(tmp_path):
    """CREATE TABLE IF NOT EXISTS rör inte en tabell som redan finns."""
    import sqlite3

    vag = tmp_path / "gammal.sqlite"
    db = sqlite3.connect(vag)
    db.execute(
        "CREATE TABLE bolagsmeta (ticker TEXT NOT NULL, datum TEXT NOT NULL, "
        "namn TEXT, valuta TEXT, rapportvaluta TEXT, "
        "PRIMARY KEY (ticker, datum))"
    )
    db.execute(
        "INSERT INTO bolagsmeta VALUES ('GAMMAL.ST','2026-01-01','Gammal','SEK','SEK')"
    )
    db.commit()
    db.close()

    with Lager(vag) as l:
        kolumner = {
            r["name"] for r in l.db.execute("PRAGMA table_info(bolagsmeta)")
        }
        assert "faltversion" in kolumner
        # Befintliga rader får version 1, alltså äldre än nuvarande
        assert l.faltversion("GAMMAL.ST") == 1
        assert not l.ar_farsk("GAMMAL.ST", 99999, dt.date(2026, 1, 2))


def test_migreringen_tappar_inte_data(tmp_path):
    import sqlite3

    vag = tmp_path / "gammal.sqlite"
    db = sqlite3.connect(vag)
    db.execute(
        "CREATE TABLE bolagsmeta (ticker TEXT NOT NULL, datum TEXT NOT NULL, "
        "namn TEXT, valuta TEXT, rapportvaluta TEXT, "
        "PRIMARY KEY (ticker, datum))"
    )
    db.execute(
        "INSERT INTO bolagsmeta VALUES ('AZN.ST','2026-01-01','AstraZeneca','SEK','USD')"
    )
    db.commit()
    db.close()

    with Lager(vag) as l:
        meta = l.senast_kanda_meta("AZN.ST", dt.date(2026, 6, 1))
        assert meta["rapportvaluta"] == "USD", "valutaminnet ska överleva"
        assert meta["namn"] == "AstraZeneca"


def test_migreringen_gar_att_kora_flera_ganger(tmp_path):
    vag = tmp_path / "m.sqlite"
    for _ in range(3):
        with Lager(vag) as l:
            l._migrera()
    with Lager(vag) as l:
        kolumner = [
            r["name"] for r in l.db.execute("PRAGMA table_info(bolagsmeta)")
        ]
        assert kolumner.count("faltversion") == 1
