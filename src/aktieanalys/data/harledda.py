"""Härledda mått -- nyckeltal som räknas fram ur råposter.

Fastighetsbolagens nyckeltal (förvaltningsresultat, belåningsgrad,
räntetäckningsgrad, substansvärde) är **alternativa nyckeltal**, inte
IFRS-poster. De finns därför inte i någon fri maskinläsbar källa; varje
bolag definierar och publicerar dem i sin egen rapport.

De går däremot att närma sig ur de råposter yfinance faktiskt ger. Det
är vad den här modulen gör. Noggrannheten är UPPMÄTT mot Castellums
egen nyckeltalsredovisning för 2025, inte antagen:

    belåningsgrad            37,3 %      verkligt 37 %        nästan exakt
    substansvärde per aktie  159,1 kr    verkligt 160 kr      0,6 % fel
    förvaltningsresultat     3 945 mkr   verkligt 4 606 mkr   14 % för lågt
    räntetäckningsgrad       2,76        verkligt 3           för lågt

Förvaltningsresultatet blir för lågt eftersom räntekostnaden är brutto:
den verkliga definitionen använder finansnetto och räknar in resultat
från samägda bolag. Avvikelsen går i samma riktning för alla bolag, som
redovisar under samma IFRS-regler, så RANGORDNINGEN inom kategorin --
det enda poängmodellen använder -- påverkas betydligt mindre än nivån.

Två regler:

1. Härledningar fyller bara fält som fortfarande SAKNAS. Skriver du in
   verkliga tal i data/manuellt/ vinner alltid de.
2. Varje härlett värde märks "härlett:" i källan, så att du i rapporten
   kan se vilka tal som är proxyvärden och vilka som är rapporterade.

Substansvärde härleds ENDAST för fastighetsbolag. För investmentbolag
vore samma härledning systematiskt fel: Investor och Industrivärden
redovisar innehav till marknadsvärde, så eget kapital ÄR substansvärdet,
medan Latour redovisar industrirörelsen till bokfört värde och har ett
eget kapital på en bråkdel av substansvärdet. Att rangordna dem mot
varandra på bokfört eget kapital hade gjort Latour till Sveriges
dyraste bolag. Investmentbolagens substansvärden måste komma från
bolagens egen rapportering.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from aktieanalys.data.bolagsdata import Bolagsdata
from aktieanalys.logg import hamta

logg = hamta(__name__)

#: Måttuppsättningar där substansvärde och förvaltningsresultat får
#: härledas. Lägg aldrig till "investmentbolag" här -- se modulens
#: dokumentation.
FASTIGHET = ("fastighet",)


@dataclass(frozen=True)
class Harledning:
    falt: str
    galler_for: tuple[str, ...]
    kraver: tuple[str, ...]
    noggrannhet: str
    rakna: Callable[[Bolagsdata], Any | None]

    def mojlig(self, bd: Bolagsdata, mattuppsattning: str) -> bool:
        return mattuppsattning in self.galler_for and bd.har_alla(self.kraver)


# =====================================================================
#  Hjälpfunktioner
# =====================================================================
def _aktier_senaste(bd: Bolagsdata) -> float | None:
    """Antal aktier enligt RÄKENSKAPERNA, inte enligt Yahoos info.

    Uppmätt på Castellum: info påstår 426,3 miljoner aktier medan
    balansräkningen säger 492,1 miljoner. Det senare stämmer med
    bolagets eget förvaltningsresultat per aktie. Tal per aktie som
    räknas ur räkenskaperna måste därför använda räkenskapernas eget
    aktieantal, annars blir de 13 procent fel.
    """
    serie = bd.varde("antal_aktier_historik")
    if serie:
        return serie[0][1]
    return bd.varde("antal_aktier")


def _per_aktie(
    belopp: list[tuple[int, float]], aktier: list[tuple[int, float]]
) -> list[tuple[int, float]] | None:
    """Parar ihop belopp och aktieantal år för år.

    År som saknar aktieantal utesluts -- de får inte räknas på ett
    annat års aktieantal.
    """
    per_ar = dict(aktier)
    ut = [
        (ar, v / per_ar[ar])
        for ar, v in belopp
        if per_ar.get(ar) not in (None, 0)
    ]
    return ut or None


def _differens(
    a: list[tuple[int, float]], b: list[tuple[int, float]]
) -> list[tuple[int, float]] | None:
    """a minus b, år för år. År som bara finns i en av serierna utesluts."""
    andra = dict(b)
    ut = [(ar, v - andra[ar]) for ar, v in a if ar in andra]
    return ut or None


# =====================================================================
#  Härledningarna
# =====================================================================
def _belaningsgrad(bd: Bolagsdata) -> float | None:
    tillgangar = bd.varde("totala_tillgangar")
    if not tillgangar:
        return None
    return bd.varde("rantebarande_skulder") / tillgangar


def _rantetackningsgrad(bd: Bolagsdata) -> float | None:
    ranta = bd.varde("rantekostnad")
    if not ranta or ranta <= 0:
        return None
    return bd.varde("rorelseresultat") / ranta


def _forvaltningsresultat_per_aktie(bd: Bolagsdata) -> float | None:
    aktier = _aktier_senaste(bd)
    if not aktier:
        return None
    return (bd.varde("rorelseresultat") - bd.varde("rantekostnad")) / aktier


# Förvaltningsresultatets HISTORIK härleds medvetet inte. Uppmätt mot
# Castellum pekar proxyn åt fel håll: verkligheten gick 9,69 -> 9,78 ->
# 9,36 kronor per aktie, proxyn gick 8,00 -> 7,50 -> 8,02. Nivåfelet är
# jämnt och går att leva med, men riktningsfelet gör ett TILLVÄXTMÅTT
# aktivt vilseledande -- räntekostnaden svängde kraftigt 2023-2024 och
# skillnaden mellan bruttoränta och finansnetto slår olika år för år.
#
# Fastighetskategorins kvalitetsmått är därför substansvärdets
# utveckling, som härleds med 0,6 procents uppmätt fel. Vill du använda
# förvaltningsresultatets tillväxt i stället: skriv in de verkliga talen
# i data/manuellt/fastighetsdata.csv och byt måttet i config.yaml.


def _substansvarde_per_aktie(bd: Bolagsdata) -> float | None:
    aktier = _aktier_senaste(bd)
    if not aktier:
        return None
    return bd.varde("eget_kapital") / aktier


def _substansvarde_historik(bd: Bolagsdata) -> list[tuple[int, float]] | None:
    return _per_aktie(
        bd.varde("eget_kapital_historik"), bd.varde("antal_aktier_historik")
    )


def _substansvarde_totalt(bd: Bolagsdata) -> float | None:
    return bd.varde("eget_kapital")


HARLEDNINGAR: tuple[Harledning, ...] = (
    Harledning(
        "belaningsgrad", FASTIGHET,
        ("rantebarande_skulder", "totala_tillgangar"),
        "uppmätt mot Castellum 2025: 37,3 % mot rapporterade 37 % -- nästan exakt",
        _belaningsgrad,
    ),
    Harledning(
        "rantetackningsgrad", FASTIGHET,
        ("rorelseresultat", "rantekostnad"),
        "uppmätt mot Castellum 2025: 2,76 mot rapporterade 3 -- för lågt, "
        "eftersom räntekostnaden är brutto i stället för finansnetto",
        _rantetackningsgrad,
    ),
    Harledning(
        "forvaltningsresultat_per_aktie", FASTIGHET,
        ("rorelseresultat", "rantekostnad"),
        "uppmätt mot Castellum 2025: 14 % för lågt i nivå; rangordningen "
        "inom kategorin påverkas mindre",
        _forvaltningsresultat_per_aktie,
    ),
    Harledning(
        "substansvarde_per_aktie", FASTIGHET,
        ("eget_kapital",),
        "uppmätt mot Castellum 2025: 159,1 kr mot rapporterat EPRA NRV "
        "160 kr -- 0,6 % fel",
        _substansvarde_per_aktie,
    ),
    Harledning(
        "substansvarde_per_aktie_historik", FASTIGHET,
        ("eget_kapital_historik", "antal_aktier_historik"),
        "som ovan, år för år med respektive års aktieantal",
        _substansvarde_historik,
    ),
    Harledning(
        "substansvarde_totalt", FASTIGHET,
        ("eget_kapital",),
        "eget kapital som proxy för substansvärde",
        _substansvarde_totalt,
    ),
)


class Harledare:
    """Fyller fält som saknas med härledda värden."""

    namn = "harledning"

    def __init__(self, harledningar: tuple[Harledning, ...] = HARLEDNINGAR) -> None:
        self.harledningar = harledningar

    def harled(self, bd: Bolagsdata, mattuppsattning: str) -> int:
        """Fyller saknade fält för ett bolag. Returnerar antalet ifyllda.

        Fält som redan har ett värde rörs inte -- verkliga tal ur
        data/manuellt/ vinner alltid över härledda.
        """
        fyllda = 0
        for h in self.harledningar:
            if mattuppsattning not in h.galler_for:
                continue
            if bd.har(h.falt):
                continue  # redan ifyllt, från Yahoo eller CSV
            if not bd.har_alla(h.kraver):
                saknas = ", ".join(bd.saknade_av(h.kraver))
                bd.markera_saknat(
                    h.falt,
                    f"kunde inte härledas -- saknar {saknas}. Fyll i värdet "
                    f"i data/manuellt/ om kategorin kräver måttet.",
                )
                continue

            varde = h.rakna(bd)
            if varde is None:
                bd.markera_saknat(
                    h.falt, "härledningen gick inte att räkna ut på de värden som finns"
                )
                continue
            bd.satt(
                h.falt,
                varde,
                f"härlett ur {', '.join(h.kraver)} (proxyvärde: {h.noggrannhet})",
            )
            fyllda += 1
        if fyllda:
            logg.debug("%s: %d härledda fält", bd.ticker, fyllda)
        return fyllda

    @staticmethod
    def ar_harlett(bd: Bolagsdata, falt: str) -> bool:
        """Är fältets värde ett proxyvärde? Rapporten ska kunna visa det."""
        return bd.kallor.get(falt, "").startswith("härlett")
