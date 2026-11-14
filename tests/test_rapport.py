"""Tester för veckorapporten: innehåll, HTML och mejl.

Inga nätanrop och inga mejl skickas. SMTP mockas.
"""

from __future__ import annotations

import datetime as dt
import smtplib

import pytest

from aktieanalys.byten import bedom_byten
from aktieanalys.data.lager import Lager
from aktieanalys.kvalitativ.klient import Kvalitativtresultat
from aktieanalys.kvalitativ.schema import Allvarlighet, Bedomning, Risk, Risktyp
from aktieanalys.rapport.bygg import bygg_rapport, forra_rapportdatum
from aktieanalys.rapport.mejl import LOSENORD_MILJOVARIABEL, skicka
from aktieanalys.rapport.rendera import (
    amnesrad,
    nasta_beslutsfonster,
    rendera,
    spara,
)
from aktieanalys.text import datum_text, kort_datum, manad_dag
from aktieanalys.universum import valj_urval
from tests.test_byten import I_FONSTER, UTANFOR, bygg, fyll_historik, scenario


@pytest.fixture
def lager(tmp_path):
    with Lager(tmp_path / "r.sqlite") as l:
        yield l


def rapportera(inst, lager, kvalitativt=None, datum=I_FONSTER):
    tavla, data = scenario(inst, datum=datum)
    urval = valj_urval(inst, data, datum)
    beslut = bedom_byten(inst, tavla, data, lager, kvalitativt, datum)
    return bygg_rapport(inst, urval, tavla, data, beslut, lager, kvalitativt)


def riskflagga(ticker="B1.ST", forsamring=False) -> Kvalitativtresultat:
    r = Kvalitativtresultat(modell="claude-opus-5-5")
    r.bedomningar[ticker] = Bedomning(
        ticker=ticker, justering=-8.0, anvand=True,
        fundamental_forsamring=forsamring,
        motivering="Prognosen drogs in den 2 november.",
        risker=[Risk(typ=Risktyp.VINSTVARNING, allvarlighet=Allvarlighet.HOG,
                     beskrivning="Helårsprognosen drogs in den 2 november.")],
    )
    return r


# =====================================================================
#  Svenska datum
# =====================================================================
def test_datum_blir_svenska():
    assert datum_text(dt.date(2026, 5, 4)) == "4 maj 2026"
    assert kort_datum(dt.date(2026, 11, 20)) == "20 nov"
    assert manad_dag("05-15") == "15 maj"


def test_nasta_beslutsfonster(config_dict, universum_dict, portfolj_dict, tmp_path):
    inst = bygg(config_dict, universum_dict, portfolj_dict, tmp_path)
    assert nasta_beslutsfonster(inst, dt.date(2026, 1, 10)) == "15 februari"
    assert nasta_beslutsfonster(inst, dt.date(2026, 11, 14)) == "15 november"
    # Efter årets sista fönster pekar nästa på nästa år
    assert "2027" in nasta_beslutsfonster(inst, dt.date(2026, 12, 20))


# =====================================================================
#  Innehållet
# =====================================================================
def test_rapporten_visar_innehav_och_topp_tre(
    config_dict, universum_dict, portfolj_dict, tmp_path, lager
):
    inst = bygg(config_dict, universum_dict, portfolj_dict, tmp_path)
    r = rapportera(inst, lager)
    kat = r.kategorier[0]

    assert kat.innehavsrad is not None
    assert kat.innehavsrad.ticker == "B1.ST"
    assert len(kat.rader) == 4, "innehavet plus topp tre"
    assert len({rad.ticker for rad in kat.rader}) == 4, "inga dubbletter"


def test_innehav_i_toppen_ger_inte_dubblett(
    config_dict, universum_dict, portfolj_dict, tmp_path, lager
):
    inst = bygg(config_dict, universum_dict, portfolj_dict, tmp_path, innehav="B6.ST")
    kat = rapportera(inst, lager).kategorier[0]
    assert len(kat.rader) == 3
    assert sum(1 for rad in kat.rader if rad.ar_innehav) == 1


def test_svagt_innehav_visas_anda(
    config_dict, universum_dict, portfolj_dict, tmp_path, lager
):
    """Innehavet ska synas även när det rankar sist."""
    inst = bygg(config_dict, universum_dict, portfolj_dict, tmp_path)
    kat = rapportera(inst, lager).kategorier[0]
    innehav = kat.innehavsrad
    assert innehav.placering > 3


def test_forandring_mot_forra_veckan(
    config_dict, universum_dict, portfolj_dict, tmp_path, lager
):
    inst = bygg(config_dict, universum_dict, portfolj_dict, tmp_path)
    tavla, data = scenario(inst)
    forra = I_FONSTER - dt.timedelta(days=7)
    lager.spara_veckopoang(forra, [
        {"ticker": b.ticker, "kategori": "Industri",
         "poang_slutlig": b.totalpoang - 5.0, "poang_kvantitativ": b.totalpoang,
         "placering": n + 1, "ar_innehav": b.ar_innehav}
        for n, b in enumerate(tavla.kategorier["Industri"].bolag, start=1)
    ])

    r = rapportera(inst, lager)
    assert r.forra_datum == forra
    rad = r.kategorier[0].rader[1]
    assert rad.forandring_poang == pytest.approx(5.0, abs=0.01)
    assert rad.forandring_placering == 1   # klättrat ett steg


def test_nytt_bolag_i_toppen_markeras(
    config_dict, universum_dict, portfolj_dict, tmp_path, lager
):
    inst = bygg(config_dict, universum_dict, portfolj_dict, tmp_path)
    lager.spara_veckopoang(I_FONSTER - dt.timedelta(days=7), [
        {"ticker": "B1.ST", "kategori": "Industri", "poang_slutlig": 40.0,
         "placering": 1, "ar_innehav": True}])

    r = rapportera(inst, lager)
    nya = [rad for rad in r.kategorier[0].rader if rad.ar_ny]
    assert nya, "bolag som saknades förra veckan ska märkas som nya"


def test_ingen_forandring_utan_historik(
    config_dict, universum_dict, portfolj_dict, tmp_path, lager
):
    inst = bygg(config_dict, universum_dict, portfolj_dict, tmp_path)
    r = rapportera(inst, lager)
    assert r.forra_datum is None
    assert all(not rad.ar_ny for rad in r.kategorier[0].rader)


def test_kvalitativ_justering_syns_i_raden(
    config_dict, universum_dict, portfolj_dict, tmp_path, lager
):
    inst = bygg(config_dict, universum_dict, portfolj_dict, tmp_path)
    r = rapportera(inst, lager, riskflagga())
    innehav = r.kategorier[0].innehavsrad
    assert innehav.justering == -8.0
    assert innehav.risker


def test_datakvalitet_samlar_uteslutna(
    config_dict, universum_dict, portfolj_dict, tmp_path, lager
):
    inst = bygg(config_dict, universum_dict, portfolj_dict, tmp_path)
    tavla, data = scenario(inst)
    data["B3.ST"].varden["borsvarde"] = 1e9      # under gränsen
    urval = valj_urval(inst, data, I_FONSTER)
    beslut = bedom_byten(inst, tavla, data, lager, None, I_FONSTER)
    r = bygg_rapport(inst, urval, tavla, data, beslut, lager)

    assert any(t == "B3.ST" for t, _, _ in r.datakvalitet.uteslutna)
    assert r.datakvalitet.nagot_att_visa


def test_forra_rapportdatum_hoppar_over_idag(lager):
    lager.spara_veckopoang(I_FONSTER, [
        {"ticker": "A.ST", "kategori": "K", "poang_slutlig": 1.0,
         "placering": 1, "ar_innehav": False}])
    assert forra_rapportdatum(lager, I_FONSTER) is None


# =====================================================================
#  HTML
# =====================================================================
def test_html_innehaller_det_viktiga(
    config_dict, universum_dict, portfolj_dict, tmp_path, lager
):
    inst = bygg(config_dict, universum_dict, portfolj_dict, tmp_path)
    fyll_historik(lager)
    html = rendera(rapportera(inst, lager, riskflagga(forsamring=True)))

    assert "<!doctype html>" in html.lower()
    assert "Veckorapport 20 november 2026" in html
    assert "B1.ST" in html and "B6.ST" in html
    assert "Flaggade risker" in html
    assert "fundamental försämring" in html
    assert "ISK" in html
    assert "handlar aldrig" in html, "ansvarsfriskrivningen ska finnas med"


def test_html_visar_bytesforslag_med_villkor(
    config_dict, universum_dict, portfolj_dict, tmp_path, lager
):
    inst = bygg(config_dict, universum_dict, portfolj_dict, tmp_path)
    fyll_historik(lager)
    r = rapportera(inst, lager)
    assert r.forslag
    html = rendera(r)

    assert "Förslag" in html
    assert "Kostnad" in html
    assert "veckorapporter i rad" in html


def test_html_utanfor_fonster_sager_bevakning(
    config_dict, universum_dict, portfolj_dict, tmp_path, lager
):
    inst = bygg(config_dict, universum_dict, portfolj_dict, tmp_path)
    html = rendera(rapportera(inst, lager, datum=UTANFOR))
    assert "enbart bevakning" in html
    assert "Nästa fönster" in html


def test_html_escapar_bolagsnamn(
    config_dict, universum_dict, portfolj_dict, tmp_path, lager
):
    """Ett bolagsnamn från en extern källa får inte bli HTML."""
    inst = bygg(config_dict, universum_dict, portfolj_dict, tmp_path)
    tavla, data = scenario(inst)
    for bp in tavla.kategorier["Industri"].bolag:
        bp.namn = "<script>fel()</script>"
    urval = valj_urval(inst, data, I_FONSTER)
    beslut = bedom_byten(inst, tavla, data, lager, None, I_FONSTER)
    html = rendera(bygg_rapport(inst, urval, tavla, data, beslut, lager))

    assert "<script>fel()" not in html
    assert "&lt;script&gt;" in html


def test_html_ar_rimligt_kort(
    config_dict, universum_dict, portfolj_dict, tmp_path, lager
):
    """Gmail klipper mejl över ungefär 100 kB."""
    inst = bygg(config_dict, universum_dict, portfolj_dict, tmp_path)
    html = rendera(rapportera(inst, lager, riskflagga()))
    assert len(html.encode("utf-8")) < 100_000


def test_rapporten_sparas_med_datum_som_filnamn(
    config_dict, universum_dict, portfolj_dict, tmp_path, lager
):
    inst = bygg(config_dict, universum_dict, portfolj_dict, tmp_path)
    r = rapportera(inst, lager)
    vag = spara(r, rendera(r))
    assert vag.name == f"{I_FONSTER.isoformat()}.html"
    assert vag.read_text(encoding="utf-8").startswith("<!doctype html>")


# =====================================================================
#  Ämnesraden
# =====================================================================
def test_amnesrad_med_forslag(
    config_dict, universum_dict, portfolj_dict, tmp_path, lager
):
    inst = bygg(config_dict, universum_dict, portfolj_dict, tmp_path)
    fyll_historik(lager)
    amne = amnesrad(rapportera(inst, lager))
    assert "1 förslag" in amne and "Industri" in amne


def test_amnesrad_vid_fundamental_forsamring(
    config_dict, universum_dict, portfolj_dict, tmp_path, lager
):
    inst = bygg(config_dict, universum_dict, portfolj_dict, tmp_path)
    amne = amnesrad(rapportera(inst, lager, riskflagga(forsamring=True), UTANFOR))
    assert "fundamental försämring" in amne
    assert "B1.ST" in amne


def test_amnesrad_vid_bara_risker(
    config_dict, universum_dict, portfolj_dict, tmp_path, lager
):
    inst = bygg(config_dict, universum_dict, portfolj_dict, tmp_path)
    amne = amnesrad(rapportera(inst, lager, riskflagga(), UTANFOR))
    assert "riskflagg" in amne


def test_amnesrad_nar_inget_hant(
    config_dict, universum_dict, portfolj_dict, tmp_path, lager
):
    inst = bygg(config_dict, universum_dict, portfolj_dict, tmp_path)
    amne = amnesrad(rapportera(inst, lager, datum=UTANFOR))
    assert "inget att agera på" in amne
    assert "20 nov" in amne or "14 nov" in amne


# =====================================================================
#  Mejl
# =====================================================================
def mejlkonfig(config_dict, universum_dict, portfolj_dict, tmp_path, **extra):
    config_dict["rapport"]["skicka_mejl"] = True
    config_dict["rapport"]["mejl"].update(
        {"till": "mig@example.com", "fran": "mig@example.com",
         "smtp_server": "smtp.example.com", "smtp_port": 587} | extra
    )
    return bygg(config_dict, universum_dict, portfolj_dict, tmp_path)


class FalskSmtp:
    senaste = None

    def __init__(self, server, port, timeout=None, context=None):
        FalskSmtp.senaste = self
        self.inloggad = False
        self.brev = None

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def starttls(self, context=None):
        pass

    def login(self, anvandare, losenord):
        self.inloggad = True

    def send_message(self, brev):
        self.brev = brev


def test_mejl_skickas(config_dict, universum_dict, portfolj_dict, tmp_path,
                      monkeypatch):
    inst = mejlkonfig(config_dict, universum_dict, portfolj_dict, tmp_path)
    monkeypatch.setenv(LOSENORD_MILJOVARIABEL, "hemligt")
    monkeypatch.setattr(smtplib, "SMTP", FalskSmtp)

    utskick = skicka(inst, "Ämne", "<html>hej</html>")
    assert utskick.skickat
    brev = FalskSmtp.senaste.brev
    assert brev["Subject"] == "Ämne"
    assert brev["To"] == "mig@example.com"
    # Både HTML och en läsbar textdel
    assert brev.get_body(("html",)) is not None
    assert brev.get_body(("plain",)) is not None


def test_mejl_utan_losenord_skickas_inte(
    config_dict, universum_dict, portfolj_dict, tmp_path, monkeypatch
):
    inst = mejlkonfig(config_dict, universum_dict, portfolj_dict, tmp_path)
    monkeypatch.delenv(LOSENORD_MILJOVARIABEL, raising=False)
    utskick = skicka(inst, "Ämne", "<html>hej</html>")

    assert not utskick.skickat
    assert LOSENORD_MILJOVARIABEL in utskick.orsak
    assert "app-specifikt" in utskick.orsak


def test_mejl_utan_mottagare_skickas_inte(
    config_dict, universum_dict, portfolj_dict, tmp_path, monkeypatch
):
    inst = mejlkonfig(config_dict, universum_dict, portfolj_dict, tmp_path, till="")
    monkeypatch.setenv(LOSENORD_MILJOVARIABEL, "hemligt")
    utskick = skicka(inst, "Ämne", "<html>hej</html>")
    assert not utskick.skickat
    assert "mottagare" in utskick.orsak


def test_avstangt_mejl_respekteras(
    config_dict, universum_dict, portfolj_dict, tmp_path, monkeypatch
):
    inst = mejlkonfig(config_dict, universum_dict, portfolj_dict, tmp_path)
    config_dict["rapport"]["skicka_mejl"] = False
    inst = bygg(config_dict, universum_dict, portfolj_dict, tmp_path)
    monkeypatch.setenv(LOSENORD_MILJOVARIABEL, "hemligt")
    assert not skicka(inst, "Ämne", "<html/>").skickat


def test_torrkorning_skickar_inget(
    config_dict, universum_dict, portfolj_dict, tmp_path, monkeypatch
):
    inst = mejlkonfig(config_dict, universum_dict, portfolj_dict, tmp_path)
    monkeypatch.setenv(LOSENORD_MILJOVARIABEL, "hemligt")
    monkeypatch.setattr(smtplib, "SMTP", FalskSmtp)
    FalskSmtp.senaste = None

    utskick = skicka(inst, "Ämne", "<html/>", torrkor=True)
    assert not utskick.skickat
    assert FalskSmtp.senaste is None


def test_felaktigt_losenord_ger_begripligt_besked(
    config_dict, universum_dict, portfolj_dict, tmp_path, monkeypatch
):
    inst = mejlkonfig(config_dict, universum_dict, portfolj_dict, tmp_path)
    monkeypatch.setenv(LOSENORD_MILJOVARIABEL, "fel")

    class Avvisar(FalskSmtp):
        def login(self, a, l):
            raise smtplib.SMTPAuthenticationError(535, b"nej")

    monkeypatch.setattr(smtplib, "SMTP", Avvisar)
    utskick = skicka(inst, "Ämne", "<html/>")
    assert not utskick.skickat
    assert "app-specifikt lösenord" in utskick.orsak


def test_natverksfel_stoppar_inte_korningen(
    config_dict, universum_dict, portfolj_dict, tmp_path, monkeypatch
):
    """Rapporten är sparad som fil. Ett mejlfel får inte höja undantag."""
    inst = mejlkonfig(config_dict, universum_dict, portfolj_dict, tmp_path)
    monkeypatch.setenv(LOSENORD_MILJOVARIABEL, "hemligt")

    def kraschar(*a, **kw):
        raise OSError("nätet nere")

    monkeypatch.setattr(smtplib, "SMTP", kraschar)
    utskick = skicka(inst, "Ämne", "<html/>")
    assert not utskick.skickat
    assert "nätverksfel" in utskick.orsak


def test_port_465_anvander_ssl(
    config_dict, universum_dict, portfolj_dict, tmp_path, monkeypatch
):
    inst = mejlkonfig(config_dict, universum_dict, portfolj_dict, tmp_path,
                      smtp_port=465)
    monkeypatch.setenv(LOSENORD_MILJOVARIABEL, "hemligt")
    monkeypatch.setattr(smtplib, "SMTP_SSL", FalskSmtp)
    assert skicka(inst, "Ämne", "<html/>").skickat
