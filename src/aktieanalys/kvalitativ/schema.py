"""Vad Claude får svara, och vad som händer om svaret inte håller måttet.

Det kvalitativa lagret har ett smalt mandat, och schemat är det som
upprätthåller det:

* Claude får FLAGGA RISKER och JUSTERA poängen några procent.
* Claude får INTE föreslå bolag. Varje anrop gäller ett bolag, och svaret
  har inget fält för att nämna ett annat -- modellen kan alltså inte
  smyga in ett förslag ens om den vill.
* Justeringen klamras på vår sida till max_justering ur config.yaml.
  Svarar modellen med ett större tal kapas det, och att det kapades
  antecknas -- ett försök att överskrida mandatet är i sig värt att se.

Håller svaret inte schemat används den rena sifferpoängen. Ett ogiltigt
svar får aldrig bli en gissning.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field


class Risktyp(str, Enum):
    VINSTVARNING = "vinstvarning"
    VD_BYTE = "vd_byte"
    ENGANGSPOSTER = "engangsposter"
    REGULATORISK = "regulatorisk"
    SKULDSATTNING = "skuldsattning"
    REDOVISNING = "redovisning"
    MARKNAD = "marknad"
    OVRIGT = "ovrigt"


class Allvarlighet(str, Enum):
    LAG = "lag"
    MEDEL = "medel"
    HOG = "hog"


SVENSKA = {
    Risktyp.VINSTVARNING: "vinstvarning",
    Risktyp.VD_BYTE: "VD- eller ledningsbyte",
    Risktyp.ENGANGSPOSTER: "engångsposter",
    Risktyp.REGULATORISK: "regulatorisk risk",
    Risktyp.SKULDSATTNING: "skuldsättning",
    Risktyp.REDOVISNING: "redovisningsfråga",
    Risktyp.MARKNAD: "marknads- eller efterfrågerisk",
    Risktyp.OVRIGT: "övrigt",
    Allvarlighet.LAG: "låg",
    Allvarlighet.MEDEL: "medel",
    Allvarlighet.HOG: "hög",
}


class Risk(BaseModel):
    """En flaggad risk."""

    model_config = ConfigDict(extra="forbid")

    typ: Risktyp
    allvarlighet: Allvarlighet
    beskrivning: str = Field(min_length=10, max_length=400)

    def som_text(self) -> str:
        return (
            f"[{SVENSKA[self.allvarlighet]}] {SVENSKA[self.typ]}: "
            f"{self.beskrivning}"
        )


class LlmSvar(BaseModel):
    """Exakt det modellen får svara med. Inget mer ryms i schemat."""

    model_config = ConfigDict(extra="forbid")

    justering_procent: float = Field(
        ge=-100.0,
        le=100.0,
        description=(
            "Justering av bolagets poäng i procent. Negativt sänker. "
            "Använd 0 när ingenting av vikt framkommit."
        ),
    )
    motivering: str = Field(
        min_length=10,
        max_length=600,
        description="Kort motivering på svenska till justeringen.",
    )
    risker: list[Risk] = Field(
        default_factory=list,
        max_length=6,
        description="Flaggade risker. Tom lista när inget anmärkningsvärt finns.",
    )
    fundamental_forsamring: bool = Field(
        default=False,
        description=(
            "Sant endast vid tydlig fundamental försämring, t.ex. vinstvarning "
            "eller indragen prognos. Flaggan får bytesreglerna att åsidosätta "
            "kravet på minsta innehavstid, så den ska användas sparsamt."
        ),
    )


@dataclass
class Bedomning:
    """Resultatet för ett bolag, efter vår egen kontroll."""

    ticker: str
    justering: float = 0.0
    """Faktisk justering i procent, efter klamring."""
    motivering: str = ""
    risker: list[Risk] = field(default_factory=list)
    fundamental_forsamring: bool = False

    anvand: bool = False
    """Om bedömningen faktiskt påverkade poängen."""
    orsak: str | None = None
    """Varför bedömningen inte användes, om den inte gjorde det."""
    klamrad_fran: float | None = None
    """Modellens ursprungliga tal, om det kapades."""

    antal_nyheter: int = 0
    tokens_in: int = 0
    tokens_ut: int = 0

    @property
    def har_risker(self) -> bool:
        return bool(self.risker)

    @property
    def hogsta_allvarlighet(self) -> Allvarlighet | None:
        if not self.risker:
            return None
        ordning = [Allvarlighet.LAG, Allvarlighet.MEDEL, Allvarlighet.HOG]
        return max(self.risker, key=lambda r: ordning.index(r.allvarlighet)).allvarlighet

    def justerad_poang(self, poang: float | None) -> float | None:
        """Poängen efter justering, begränsad till 0--100."""
        if poang is None or not self.anvand:
            return poang
        return max(0.0, min(100.0, poang * (1 + self.justering / 100)))

    def sammanfattning(self) -> str:
        if not self.anvand:
            return f"{self.ticker}: ingen justering ({self.orsak})"
        tecken = "+" if self.justering >= 0 else ""
        bit = f"{self.ticker}: {tecken}{self.justering:.1f} %"
        if self.risker:
            bit += f", {len(self.risker)} risk(er)"
        if self.fundamental_forsamring:
            bit += ", FUNDAMENTAL FÖRSÄMRING"
        return bit


def validera(
    svar: LlmSvar, ticker: str, max_justering_andel: float
) -> Bedomning:
    """Kontrollerar modellens svar och klamrar justeringen.

    max_justering_andel är ur config.yaml, t.ex. 0.10 för tio procent.
    """
    grans = max_justering_andel * 100
    justering = svar.justering_procent
    klamrad = None

    if justering > grans:
        klamrad, justering = justering, grans
    elif justering < -grans:
        klamrad, justering = justering, -grans

    if klamrad is not None:
        import logging

        logging.getLogger("aktieanalys.kvalitativ").warning(
            "%s: modellen föreslog %.1f %%, kapat till %.1f %% "
            "(mandatet är ±%.0f %%)",
            ticker, klamrad, justering, grans,
        )

    return Bedomning(
        ticker=ticker,
        justering=justering,
        motivering=svar.motivering.strip(),
        risker=list(svar.risker),
        fundamental_forsamring=svar.fundamental_forsamring,
        anvand=True,
        klamrad_fran=klamrad,
    )


def utan_bedomning(ticker: str, orsak: str) -> Bedomning:
    """Fallet när inget svar gick att använda. Poängen lämnas orörd."""
    return Bedomning(ticker=ticker, anvand=False, orsak=orsak)
