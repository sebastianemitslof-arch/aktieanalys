"""Datakontraktet: fältkatalogen och klassen Bolagsdata.

Det här är gränssnittet mellan datakällan och poängmodellen. Byter du
yfinance mot Börsdata eller ett Google Sheets-flöde är det bara en ny
klass som ska fylla ett Bolagsdata -- poängmodellen märker ingenting.

Grundregeln i hela filen: **ett värde som inte finns är None, aldrig 0.**
En nolla betyder "sämst i klassen", vilket är ett helt annat påstående
än "vi vet inte". Varje saknat fält får dessutom en orsak, så att
veckorapporten kan skriva ut varför.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import Any, Literal

Falttyp = Literal["tal", "arslista", "kursserie"]
"""tal       = ett enda tal, t.ex. kurs eller nettoskuld
arslista  = ett värde per räkenskapsår, t.ex. [(2025, 1234.0), (2024, 1100.0)]
kursserie = daglig kurshistorik (en pandas-serie med datum som index)"""

Faltkalla = Literal["marknad", "resultat", "balans", "manuell"]
"""Varifrån fältet normalt kommer. "manuell" betyder att det inte finns i
yfinance och måste fyllas i för hand i en CSV-fil."""


@dataclass(frozen=True)
class Faltdef:
    namn: str
    typ: Falttyp
    kalla: Faltkalla
    enhet: str
    beskrivning: str


def _f(namn: str, typ: Falttyp, kalla: Faltkalla, enhet: str, beskrivning: str) -> Faltdef:
    return Faltdef(namn, typ, kalla, enhet, beskrivning)


#: Alla datafält systemet känner till. Måtten i poang/matt.py får bara
#: kräva fält som står här -- ett test kontrollerar det.
FALT: dict[str, Faltdef] = {
    d.namn: d
    for d in [
        # ---------------- Marknadsdata ----------------
        _f("kurs", "tal", "marknad", "kr", "Senaste betalkurs."),
        _f("borsvarde", "tal", "marknad", "kr", "Börsvärde (antal aktier gånger kurs)."),
        _f("daglig_omsattning", "tal", "marknad", "kr/dag",
           "Genomsnittlig omsättning per handelsdag. Likviditetsfilter."),
        _f("kurshistorik", "kursserie", "marknad", "kr",
           "Daglig stängningskurs, justerad för utdelning och split. "
           "Används för momentum och för historiska värderingsjämförelser."),
        _f("saljkurs", "tal", "marknad", "kr", "Högsta köpvilja (bid). För spreadberäkning."),
        _f("kopkurs", "tal", "marknad", "kr", "Lägsta säljvilja (ask). För spreadberäkning."),

        # ---------------- Resultaträkning ----------------
        _f("omsattning", "tal", "resultat", "kr", "Nettoomsättning, senaste räkenskapsår."),
        _f("rorelseresultat", "tal", "resultat", "kr", "Rörelseresultat (EBIT)."),
        _f("ebitda", "tal", "resultat", "kr", "Rörelseresultat före avskrivningar."),
        _f("nettoresultat", "tal", "resultat", "kr", "Resultat efter skatt."),
        _f("vinst_per_aktie", "tal", "resultat", "kr", "Vinst per aktie, rullande tolv månader."),
        _f("omsattning_historik", "arslista", "resultat", "kr",
           "Nettoomsättning per år. Behövs för att räkna marginalhistorik."),
        _f("rorelseresultat_historik", "arslista", "resultat", "kr",
           "Rörelseresultat per år. Grund för marginalstabilitet och "
           "historisk EV/EBIT."),
        _f("nettoresultat_historik", "arslista", "resultat", "kr",
           "Resultat efter skatt per år. Grund för vinststabilitet."),
        _f("vinst_per_aktie_historik", "arslista", "resultat", "kr",
           "Vinst per aktie per år. Grund för historisk P/E."),
        _f("rantekostnad", "tal", "resultat", "kr",
           "Räntekostnad, som positivt tal. Nämnare i räntetäckningsgraden "
           "och avdrag i det härledda förvaltningsresultatet."),
        _f("rantekostnad_historik", "arslista", "resultat", "kr",
           "Räntekostnad per år, som positiva tal."),

        # ---------------- Balansräkning ----------------
        _f("eget_kapital", "tal", "balans", "kr", "Eget kapital hänförligt till aktieägarna."),
        _f("eget_kapital_per_aktie", "tal", "balans", "kr", "Eget kapital delat med antal aktier."),
        _f("investerat_kapital", "tal", "balans", "kr",
           "Eget kapital plus räntebärande skulder. Nämnaren i ROIC."),
        _f("nettoskuld", "tal", "balans", "kr",
           "Räntebärande skulder minus kassa. Negativt tal betyder nettokassa."),
        _f("antal_aktier", "tal", "balans", "st", "Antal utestående aktier."),
        _f("bolagsvarde", "tal", "balans", "kr",
           "Börsvärde plus nettoskuld (enterprise value). Täljaren i EV/EBIT."),
        _f("totala_tillgangar", "tal", "balans", "kr",
           "Balansomslutning. För ett fastighetsbolag är fastigheterna "
           "nästan hela tillgångssidan, vilket gör måttet användbart som "
           "nämnare i belåningsgraden."),
        _f("rantebarande_skulder", "tal", "balans", "kr",
           "Räntebärande skulder, brutto (utan avdrag för kassa). Täljaren "
           "i belåningsgraden."),
        _f("eget_kapital_historik", "arslista", "balans", "kr",
           "Eget kapital per år. Grund för härledd substansvärdeshistorik."),
        _f("antal_aktier_historik", "arslista", "balans", "st",
           "Antal aktier per år. Behövs för att räkna historiska tal PER "
           "AKTIE korrekt -- bolag som nyemitterat skulle annars få sin "
           "historik räknad på dagens aktieantal."),

        # ---------------- Manuellt kompletterade ----------------
        # Finns inte i yfinance. Fylls i via CSV-filerna i data/manuellt/.
        _f("substansvarde_per_aktie", "tal", "manuell", "kr",
           "Substansvärde per aktie. För investmentbolag det rapporterade "
           "substansvärdet, för fastighetsbolag långsiktigt substansvärde "
           "(EPRA NRV)."),
        _f("substansvarde_per_aktie_historik", "arslista", "manuell", "kr",
           "Substansvärde per aktie per år. Grund för substansvärdets "
           "utveckling och för snittrabatten."),
        _f("substansvarde_totalt", "tal", "manuell", "kr", "Substansvärde för hela bolaget."),
        _f("forvaltningsresultat_per_aktie", "tal", "manuell", "kr",
           "Förvaltningsresultat per aktie. Fastighetsbolagets löpande "
           "intjäning, utan orealiserade värdeförändringar."),
        _f("forvaltningsresultat_per_aktie_historik", "arslista", "manuell", "kr",
           "Förvaltningsresultat per aktie per år."),
        _f("belaningsgrad", "tal", "manuell", "andel",
           "Räntebärande skulder mot fastighetsvärde (LTV). 0.45 = 45 procent."),
        _f("rantetackningsgrad", "tal", "manuell", "gånger",
           "Hur många gånger driftnettot täcker räntekostnaderna."),
        _f("cet1_kvot", "tal", "manuell", "andel",
           "Kärnprimärkapitalrelation för banker. 0.18 = 18 procent."),
    ]
}

MANUELLA_FALT: tuple[str, ...] = tuple(
    n for n, d in FALT.items() if d.kalla == "manuell"
)


class OkantFalt(KeyError):
    """Ett fältnamn som inte finns i FALT. Alltid ett programmeringsfel."""


def _kontrollera(falt: str) -> Faltdef:
    if falt not in FALT:
        import difflib

        forslag = difflib.get_close_matches(falt, FALT, n=3, cutoff=0.5)
        extra = f" Menade du {' eller '.join(forslag)}?" if forslag else ""
        raise OkantFalt(f"okänt datafält {falt!r}.{extra}")
    return FALT[falt]


@dataclass
class Bolagsdata:
    """Allt systemet vet om ett bolag vid en given tidpunkt.

    Värden ligger i en dict i stället för som namngivna attribut. Det gör
    att "vilka fält saknas" går att svara på generellt, och att nya fält
    kan läggas till utan att databasen behöver byggas om.
    """

    ticker: str
    hamtad: dt.date
    namn: str | None = None
    valuta: str | None = None
    rapportvaluta: str | None = None
    """Valutan räkenskaperna redovisas i. Skiljer sig den från valuta blir
    nyckeltal som blandar kurs och räkenskaper fel utan omräkning --
    AstraZeneca handlas i kronor men redovisar i dollar."""

    varden: dict[str, Any] = field(default_factory=dict)
    """Fält som har ett värde. Fält som saknas ligger INTE här med None --
    de finns inte alls i dicten, och orsaken står i saknade."""

    kallor: dict[str, str] = field(default_factory=dict)
    """Vilken källa varje fält kom från, t.ex. "yfinance" eller
    "csv:substansvarden.csv". Gör att rapporten kan visa härkomst."""

    saknade: dict[str, str] = field(default_factory=dict)
    """Fält som saknas, med orsak i klartext."""

    faltdatum: dict[str, dt.date] = field(default_factory=dict)
    """Vilket hämtdatum varje fält kommer från. Fylls när data läses ur
    databasen. Ett fält kan vara äldre än de andra om datakällan slutade
    leverera det en vecka -- då behålls det gamla värdet, och rapporten
    kan visa att det börjar bli gammalt."""

    fel: list[str] = field(default_factory=list)
    """Tekniska problem vid hämtningen (timeout, tomt svar, undantag)."""

    # -- läsa ------------------------------------------------------------
    def varde(self, falt: str) -> Any | None:
        """Värdet, eller None om det saknas. Höjer fel vid okänt fältnamn."""
        _kontrollera(falt)
        return self.varden.get(falt)

    def har(self, falt: str) -> bool:
        _kontrollera(falt)
        return falt in self.varden

    def har_alla(self, falt: tuple[str, ...] | list[str]) -> bool:
        return all(self.har(f) for f in falt)

    def saknade_av(self, falt: tuple[str, ...] | list[str]) -> list[str]:
        """Vilka av de angivna fälten som saknas. Används av poängmodellen."""
        return [f for f in falt if not self.har(f)]

    # -- skriva ----------------------------------------------------------
    def satt(self, falt: str, varde: Any, kalla: str) -> None:
        """Sätter ett värde. None och NaN behandlas som 'saknas'."""
        _kontrollera(falt)
        if varde is None:
            self.markera_saknat(falt, "värdet saknades i källan")
            return
        if isinstance(varde, float) and varde != varde:  # NaN
            self.markera_saknat(falt, "källan svarade NaN")
            return
        self.varden[falt] = varde
        self.kallor[falt] = kalla
        self.saknade.pop(falt, None)

    def markera_saknat(self, falt: str, orsak: str) -> None:
        _kontrollera(falt)
        if falt in self.varden:
            return  # redan ifyllt från annan källa -- rör det inte
        self.saknade[falt] = orsak

    def anteckna_fel(self, meddelande: str) -> None:
        self.fel.append(meddelande)

    # -- sammanfattning för rapporten ------------------------------------
    @property
    def antal_varden(self) -> int:
        return len(self.varden)

    def alder_i_dagar(self, falt: str, idag: dt.date | None = None) -> int | None:
        """Hur gammalt ett fälts värde är. None om datumet inte är känt."""
        _kontrollera(falt)
        datum = self.faltdatum.get(falt)
        return None if datum is None else ((idag or dt.date.today()) - datum).days

    def aldre_an(self, dagar: int, idag: dt.date | None = None) -> dict[str, int]:
        """Fält vars värden är äldre än så här många dagar."""
        ut = {}
        for falt in self.varden:
            alder = self.alder_i_dagar(falt, idag)
            if alder is not None and alder > dagar:
                ut[falt] = alder
        return ut

    def sammanfattning(self) -> str:
        bit = f"{self.ticker}: {self.antal_varden} fält"
        if self.saknade:
            bit += f", {len(self.saknade)} saknas"
        if self.fel:
            bit += f", {len(self.fel)} fel"
        return bit
