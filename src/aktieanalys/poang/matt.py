"""Katalog över alla nyckeltal (mått) som poängmodellen kan använda.

Det här är systemets ordlista. Konfigurationsfilen får bara hänvisa till
mått som står här -- stavar du fel i config.yaml får du ett tydligt felmeddelande
i stället för ett tyst fel i poängräkningen.

Varje mått vet själv om ett högt eller lågt värde är bra ("riktning").
Det är en egenskap hos nyckeltalet, inte en åsikt, och ligger därför i koden
och inte i konfigurationsfilen. Vikterna -- hur mycket varje mått ska betyda --
ligger däremot i config.yaml eftersom de är dina val.

Själva räkningen görs i steg 4. Här beskrivs bara vad som finns.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class Riktning(str, Enum):
    """Är ett högt eller ett lågt värde bra?"""

    HOGRE_BATTRE = "hogre_battre"
    LAGRE_BATTRE = "lagre_battre"


class Pelare(str, Enum):
    """De fyra benen i poängmodellen. Varje mått hör till exakt ett ben."""

    KVALITET = "kvalitet"
    VARDERING = "vardering"
    FINANSIELL_STYRKA = "finansiell_styrka"
    MOMENTUM = "momentum"


@dataclass(frozen=True)
class Mattdefinition:
    namn: str
    pelare: Pelare
    riktning: Riktning
    beskrivning: str
    kraver: tuple[str, ...]
    """Vilka rådatafält måttet behöver. Saknas något blir måttet None
    (aldrig 0) och bolaget räknas som att det saknar data för måttet."""


def _m(
    namn: str,
    pelare: Pelare,
    riktning: Riktning,
    beskrivning: str,
    *kraver: str,
) -> Mattdefinition:
    return Mattdefinition(namn, pelare, riktning, beskrivning, tuple(kraver))


H = Riktning.HOGRE_BATTRE
L = Riktning.LAGRE_BATTRE
P = Pelare

#: Alla tillgängliga mått, med namnet som nyckel.
MATT: dict[str, Mattdefinition] = {
    d.namn: d
    for d in [
        # ---------------- Kvalitet ----------------
        _m(
            "roic",
            P.KVALITET,
            H,
            "Avkastning på investerat kapital. Hur mycket bolaget får ut av "
            "det kapital det binder. Kärnmåttet på kvalitet för vanliga bolag.",
            "rorelseresultat",
            "investerat_kapital",
        ),
        _m(
            "roe",
            P.KVALITET,
            H,
            "Avkastning på eget kapital. Huvudmått för banker, där ROIC inte "
            "är meningsfullt eftersom skulder är en del av affären.",
            "nettoresultat",
            "eget_kapital",
        ),
        _m(
            "rorelsemarginal_stabilitet_5ar",
            P.KVALITET,
            H,
            "Hur jämn rörelsemarginalen varit över 5 år. Högt värde = stabil "
            "marginal. Mäter uthållighet, inte nivå.",
            "rorelsemarginal_historik",
        ),
        _m(
            "vinststabilitet_5ar",
            P.KVALITET,
            H,
            "Hur jämnt nettoresultatet varit över 5 år. Används för banker i "
            "stället för marginalstabilitet.",
            "nettoresultat_historik",
        ),
        _m(
            "forvaltningsresultat_per_aktie_tillvaxt",
            P.KVALITET,
            H,
            "Tillväxt i förvaltningsresultat per aktie. Fastighetsbolagets "
            "egentliga intjäning, utan orealiserade värdeförändringar.",
            "forvaltningsresultat_per_aktie_historik",
        ),
        _m(
            "substansvarde_tillvaxt",
            P.KVALITET,
            H,
            "Tillväxt i substansvärde per aktie. Investmentbolagets kvalitet: "
            "hur väl förvaltningen faktiskt skapat värde.",
            "substansvarde_per_aktie_historik",
        ),
        # ---------------- Värdering ----------------
        _m(
            "ev_ebit",
            P.VARDERING,
            L,
            "Bolagsvärde delat med rörelseresultat. Värdering oberoende av "
            "hur bolaget är finansierat.",
            "bolagsvarde",
            "rorelseresultat",
        ),
        _m(
            "pe",
            P.VARDERING,
            L,
            "Kurs delat med vinst per aktie.",
            "kurs",
            "vinst_per_aktie",
        ),
        _m(
            "ev_ebit_mot_egen_historik",
            P.VARDERING,
            L,
            "Dagens EV/EBIT jämfört med bolagets eget 5-årssnitt. Lågt värde = "
            "billigare än bolaget brukar vara.",
            "bolagsvarde",
            "rorelseresultat",
            "ev_ebit_historik",
        ),
        _m(
            "pe_mot_egen_historik",
            P.VARDERING,
            L,
            "Dagens P/E jämfört med bolagets eget 5-årssnitt.",
            "kurs",
            "vinst_per_aktie",
            "pe_historik",
        ),
        _m(
            "p_b",
            P.VARDERING,
            L,
            "Kurs delat med eget kapital per aktie. Huvudsaklig "
            "värderingsmätare för banker.",
            "kurs",
            "eget_kapital_per_aktie",
        ),
        _m(
            "kurs_mot_substans",
            P.VARDERING,
            L,
            "Kurs mot långsiktigt substansvärde (EPRA NRV) för "
            "fastighetsbolag. Under 1,0 = handlas under substans.",
            "kurs",
            "substansvarde_per_aktie",
        ),
        _m(
            "kurs_mot_forvaltningsresultat",
            P.VARDERING,
            L,
            "Kurs mot förvaltningsresultat per aktie. Fastighetsbolagens "
            "motsvarighet till P/E, utan värdeförändringar.",
            "kurs",
            "forvaltningsresultat_per_aktie",
        ),
        _m(
            "substansrabatt_mot_historik",
            P.VARDERING,
            H,
            "Investmentbolagets substansrabatt jämfört med sitt eget "
            "historiska snitt. Högt värde = ovanligt stor rabatt just nu.",
            "kurs",
            "substansvarde_per_aktie",
            "substansrabatt_historik",
        ),
        # ---------------- Finansiell styrka ----------------
        _m(
            "nettoskuld_ebitda",
            P.FINANSIELL_STYRKA,
            L,
            "Nettoskuld delat med rörelseresultat före avskrivningar. "
            "Hur många år det skulle ta att betala av skulden.",
            "nettoskuld",
            "ebitda",
        ),
        _m(
            "kapitalstyrka",
            P.FINANSIELL_STYRKA,
            H,
            "Kärnprimärkapitalrelation (CET1) för banker. Buffert mot "
            "kreditförluster. Kräver manuell komplettering via CSV.",
            "cet1_kvot",
        ),
        _m(
            "belaningsgrad",
            P.FINANSIELL_STYRKA,
            L,
            "Räntebärande skulder mot fastighetsvärde (LTV).",
            "belaningsgrad",
        ),
        _m(
            "rantetackningsgrad",
            P.FINANSIELL_STYRKA,
            H,
            "Hur många gånger driftnettot täcker räntekostnaderna. Under "
            "2,0 är ansträngt för ett fastighetsbolag.",
            "rantetackningsgrad",
        ),
        _m(
            "skuldsattning_mot_substans",
            P.FINANSIELL_STYRKA,
            L,
            "Investmentbolagets nettoskuld i förhållande till substansvärdet.",
            "nettoskuld",
            "substansvarde_totalt",
        ),
        # ---------------- Momentum ----------------
        _m(
            "momentum_12m_exkl_1m",
            P.MOMENTUM,
            H,
            "Kursutveckling de senaste 12 månaderna, utan den senaste "
            "månaden. Den senaste månaden tas bort eftersom den historiskt "
            "tenderar att vända.",
            "kurshistorik",
        ),
    ]
}


def matt_i_pelare(pelare: Pelare) -> list[str]:
    """Namnen på alla mått som hör till en given pelare."""
    return sorted(namn for namn, d in MATT.items() if d.pelare is pelare)


def okand_matt(namn: str) -> str:
    """Felmeddelande med förslag när ett måttnamn inte känns igen."""
    import difflib

    forslag = difflib.get_close_matches(namn, MATT, n=3, cutoff=0.5)
    text = f"okänt mått {namn!r}"
    if forslag:
        text += " -- menade du " + " eller ".join(repr(f) for f in forslag) + "?"
    else:
        text += f". Tillgängliga mått: {', '.join(sorted(MATT))}"
    return text
