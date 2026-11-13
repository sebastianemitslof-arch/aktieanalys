"""Tester för måttkatalogen."""

from __future__ import annotations

from aktieanalys.poang.matt import MATT, Pelare, Riktning, matt_i_pelare, okand_matt


def test_alla_matt_ar_hela():
    """Varje mått har namn, pelare, riktning, beskrivning och datakrav."""
    for namn, d in MATT.items():
        assert d.namn == namn, "nyckeln i MATT måste vara samma som måttets namn"
        assert isinstance(d.pelare, Pelare)
        assert isinstance(d.riktning, Riktning)
        assert len(d.beskrivning) > 20, f"{namn} saknar en riktig beskrivning"
        assert d.kraver, f"{namn} måste ange vilka datafält det behöver"


def test_varje_pelare_har_matt():
    """Ingen pelare får vara tom -- då går den inte att vikta."""
    for pelare in Pelare:
        assert matt_i_pelare(pelare), f"pelaren {pelare.value} har inga mått"


def test_sektorsmatt_finns():
    """De branschanpassade måtten användaren bett om finns i katalogen."""
    for namn in [
        "roe", "p_b", "kapitalstyrka",                       # banker
        "forvaltningsresultat_per_aktie_tillvaxt",            # fastighet
        "belaningsgrad", "rantetackningsgrad", "kurs_mot_substans",
        "substansrabatt_mot_historik", "substansvarde_tillvaxt",  # investmentbolag
    ]:
        assert namn in MATT, f"måttet {namn} saknas i katalogen"


def test_momentum_exkluderar_senaste_manaden():
    """Momentummåttet ska vara 12 månader exklusive den senaste."""
    assert "momentum_12m_exkl_1m" in MATT
    assert MATT["momentum_12m_exkl_1m"].riktning is Riktning.HOGRE_BATTRE


def test_vardering_ar_lagre_battre():
    """Värderingsmått där ett lågt tal är bra ska vara märkta så."""
    for namn in ["ev_ebit", "pe", "p_b", "kurs_mot_substans"]:
        assert MATT[namn].riktning is Riktning.LAGRE_BATTRE, namn


def test_okand_matt_ger_forslag():
    """Felstavningar ska ge ett förslag, inte bara ett avslag."""
    assert "roic" in okand_matt("roci")
    assert "okänt mått" in okand_matt("hittepa")
