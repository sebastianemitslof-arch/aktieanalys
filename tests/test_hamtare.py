"""Tester för hämtaren: cache, CSV-påläggning och felhantering.

Inga nätanrop -- allt körs mot Minnesprovider, så testerna är snabba
och ger samma svar oavsett vad Yahoo gör just nu.
"""

from __future__ import annotations

import datetime as dt

import pandas as pd
import pytest

from aktieanalys.data.csv_komplettering import CsvKomplettering
from aktieanalys.data.hamtare import Hamtare
from aktieanalys.data.lager import Lager
from aktieanalys.data.provider import Minnesprovider, Provider
from aktieanalys.data.bolagsdata import Bolagsdata
from aktieanalys.konfig import fran_dictar

IDAG = dt.date(2026, 11, 13)


@pytest.fixture
def inst(config_dict, universum_dict, portfolj_dict, tmp_path):
    config_dict["data"]["databas"] = str(tmp_path / "cache.sqlite")
    config_dict["data"]["manuell_katalog"] = str(tmp_path / "manuellt")
    return fran_dictar(config_dict, universum_dict, portfolj_dict, rotkatalog=tmp_path)


def minnesdata(kurs=100.0):
    return {
        "namn": "Testbolaget",
        "kurs": kurs,
        "borsvarde": 50e9,
        "daglig_omsattning": 80e6,
        "rorelseresultat": 5e9,
        "investerat_kapital": 30e9,
        "kurshistorik": pd.Series(
            [kurs - 2, kurs - 1, kurs],
            index=pd.to_datetime(["2026-11-11", "2026-11-12", "2026-11-13"]),
        ),
    }


def bygg(inst, provider, csvkatalog=None):
    return Hamtare(
        inst,
        provider=provider,
        lager=Lager(inst.databasvag),
        csv=CsvKomplettering(csvkatalog or inst.manuell_katalog, idag=IDAG),
    )


# =====================================================================
#  Grundflödet
# =====================================================================
def test_hamtar_och_sparar(inst):
    p = Minnesprovider({"VOLV-B.ST": minnesdata()})
    h = bygg(inst, p)
    try:
        rapport = h.hamta(["VOLV-B.ST"], idag=IDAG)
        assert rapport.antal == 1
        assert rapport.nyhamtade == ["VOLV-B.ST"]
        assert rapport.fran_cache == []
        assert rapport.data["VOLV-B.ST"].varde("kurs") == 100.0
        # och det ligger i databasen efteråt
        assert h.lager.las("VOLV-B.ST", IDAG).varde("kurs") == 100.0
    finally:
        h.stang()


def test_andra_korningen_tar_data_ur_cachen(inst):
    """yfinance ska inte anropas i onödan."""
    p = Minnesprovider({"VOLV-B.ST": minnesdata()})
    h = bygg(inst, p)
    try:
        h.hamta(["VOLV-B.ST"], idag=IDAG)
        p.satt_bolag("VOLV-B.ST", minnesdata(kurs=999.0))  # källan ändras

        rapport = h.hamta(["VOLV-B.ST"], idag=IDAG)
        assert rapport.fran_cache == ["VOLV-B.ST"]
        assert rapport.data["VOLV-B.ST"].varde("kurs") == 100.0  # cachen gäller
    finally:
        h.stang()


def test_tvinga_gar_forbi_cachen(inst):
    p = Minnesprovider({"VOLV-B.ST": minnesdata()})
    h = bygg(inst, p)
    try:
        h.hamta(["VOLV-B.ST"], idag=IDAG)
        p.satt_bolag("VOLV-B.ST", minnesdata(kurs=999.0))

        rapport = h.hamta(["VOLV-B.ST"], tvinga=True, idag=IDAG)
        assert rapport.nyhamtade == ["VOLV-B.ST"]
        assert rapport.data["VOLV-B.ST"].varde("kurs") == 999.0
    finally:
        h.stang()


def test_gammal_cache_hamtas_om(inst):
    p = Minnesprovider({"VOLV-B.ST": minnesdata()})
    h = bygg(inst, p)
    try:
        h.hamta(["VOLV-B.ST"], idag=dt.date(2026, 11, 1))
        p.satt_bolag("VOLV-B.ST", minnesdata(kurs=150.0))

        # cache_dagar är 1 i standardkonfigurationen
        rapport = h.hamta(["VOLV-B.ST"], idag=IDAG)
        assert rapport.nyhamtade == ["VOLV-B.ST"]
        assert rapport.data["VOLV-B.ST"].varde("kurs") == 150.0
    finally:
        h.stang()


# =====================================================================
#  CSV-påläggning
# =====================================================================
def test_csv_fyller_pa_efter_hamtning(inst, tmp_path):
    katalog = tmp_path / "manuellt"
    katalog.mkdir()
    (katalog / "f.csv").write_text(
        "ticker,ar,belaningsgrad,rantetackningsgrad\n"
        "CAST.ST,2025,0.41,3.1\nCAST.ST,2024,0.43,2.9\n",
        encoding="utf-8",
    )
    p = Minnesprovider({"CAST.ST": minnesdata()})
    h = bygg(inst, p, csvkatalog=katalog)
    try:
        rapport = h.hamta(["CAST.ST"], idag=IDAG)
        bd = rapport.data["CAST.ST"]
        assert bd.varde("belaningsgrad") == 0.41
        assert bd.varde("rantetackningsgrad") == 3.1
        assert bd.kallor["belaningsgrad"].startswith("csv:")
    finally:
        h.stang()


def test_rattad_csv_far_effekt_aven_nar_data_ar_cachad(inst, tmp_path):
    """Du ska inte behöva tvinga om hämtningen för att en CSV-rättelse ska gälla."""
    katalog = tmp_path / "manuellt"
    katalog.mkdir()
    p = Minnesprovider({"CAST.ST": minnesdata()})

    h = bygg(inst, p, csvkatalog=katalog)
    try:
        assert not h.hamta(["CAST.ST"], idag=IDAG).data["CAST.ST"].har("belaningsgrad")
    finally:
        h.stang()

    (katalog / "f.csv").write_text(
        "ticker,ar,belaningsgrad\nCAST.ST,2025,0.41\n", encoding="utf-8")

    h2 = bygg(inst, p, csvkatalog=katalog)
    try:
        rapport = h2.hamta(["CAST.ST"], idag=IDAG)
        assert rapport.fran_cache == ["CAST.ST"]
        assert rapport.data["CAST.ST"].varde("belaningsgrad") == 0.41
    finally:
        h2.stang()


# =====================================================================
#  Felhantering
# =====================================================================
def test_bolag_utan_svar_stoppar_inte_de_andra(inst):
    p = Minnesprovider({"VOLV-B.ST": minnesdata()})  # SAND.ST finns inte
    h = bygg(inst, p)
    try:
        rapport = h.hamta(["VOLV-B.ST", "SAND.ST"], idag=IDAG)
        assert rapport.nyhamtade == ["VOLV-B.ST"]
        assert "SAND.ST" in rapport.utan_svar
        assert "minnesprovidern" in rapport.utan_svar["SAND.ST"]
    finally:
        h.stang()


def test_provider_som_kraschar_fangas(inst):
    class TrasigProvider(Provider):
        namn = "trasig"

        def hamta(self, ticker, idag=None, kand_meta=None):
            bd = Bolagsdata(ticker=ticker, hamtad=idag or IDAG)
            bd.anteckna_fel("simulerat nätverksfel")
            return bd

    h = bygg(inst, TrasigProvider())
    try:
        rapport = h.hamta(["VOLV-B.ST"], idag=IDAG)
        assert rapport.utan_svar["VOLV-B.ST"] == "simulerat nätverksfel"
        assert rapport.antal == 1  # bolaget finns med, men utan värden
    finally:
        h.stang()


def test_manuella_falt_far_orsak_som_pekar_pa_csv(inst):
    p = Minnesprovider({"CAST.ST": minnesdata()})
    h = bygg(inst, p)
    try:
        bd = h.hamta(["CAST.ST"], idag=IDAG).data["CAST.ST"]
        assert "data/manuellt" in bd.saknade["belaningsgrad"]
    finally:
        h.stang()


def test_okanda_csv_tickers_rapporteras(inst, tmp_path):
    katalog = tmp_path / "manuellt"
    katalog.mkdir()
    (katalog / "f.csv").write_text(
        "ticker,ar,belaningsgrad\nSTAVFEL.ST,2025,0.41\n", encoding="utf-8")
    h = bygg(inst, Minnesprovider({"CAST.ST": minnesdata()}), csvkatalog=katalog)
    try:
        rapport = h.hamta(["CAST.ST"], idag=IDAG)
        assert rapport.okanda_csv_tickers == ["STAVFEL.ST"]
    finally:
        h.stang()


def test_hamtloggen_skriver_en_rad_per_bolag(inst):
    p = Minnesprovider({"VOLV-B.ST": minnesdata()})
    h = bygg(inst, p)
    try:
        h.hamta(["VOLV-B.ST", "SAND.ST"], idag=IDAG)
        assert h.lager.statistik()["hamtningar"] == 2
        problem = h.lager.hamtningar_med_problem(IDAG)
        assert [r["ticker"] for r in problem] == ["SAND.ST"]
    finally:
        h.stang()


# =====================================================================
#  Valutaminnet
# =====================================================================
def test_kand_valuta_skickas_med_till_providern(inst):
    """Hämtaren ska ge providern vad vi redan vet, så att ett bantat
    svar från Yahoo inte tappar rapportvalutan."""
    sedda: list[dict] = []

    class Spion(Provider):
        namn = "spion"

        def hamta(self, ticker, idag=None, kand_meta=None):
            sedda.append(kand_meta or {})
            bd = Bolagsdata(ticker=ticker, hamtad=idag or IDAG, namn="Spionbolaget")
            bd.valuta, bd.rapportvaluta = "SEK", "USD"
            bd.satt("kurs", 100.0, "spion")
            return bd

    h = bygg(inst, Spion())
    try:
        h.hamta(["AZN.ST"], idag=IDAG)          # första gången: inget känt
        assert sedda[-1] == {}
        h.hamta(["AZN.ST"], tvinga=True, idag=IDAG)   # nu finns det i databasen
        assert sedda[-1]["rapportvaluta"] == "USD"
        assert sedda[-1]["valuta"] == "SEK"
        assert sedda[-1]["namn"] == "Spionbolaget"
    finally:
        h.stang()


def test_valuta_overlever_i_databasen(inst):
    """Valutan ska gå att läsa tillbaka, annars tappas den vid cacheläsning."""
    class Kalla(Provider):
        namn = "kalla"

        def hamta(self, ticker, idag=None, kand_meta=None):
            bd = Bolagsdata(ticker=ticker, hamtad=idag or IDAG, namn="Nordea")
            bd.valuta, bd.rapportvaluta = "SEK", "EUR"
            bd.satt("kurs", 190.0, "kalla")
            return bd

    h = bygg(inst, Kalla())
    try:
        h.hamta(["NDA-SE.ST"], idag=IDAG)
        tillbaka = h.lager.las("NDA-SE.ST", IDAG)
        assert tillbaka.rapportvaluta == "EUR"
        assert tillbaka.valuta == "SEK"
        assert tillbaka.namn == "Nordea"
    finally:
        h.stang()
