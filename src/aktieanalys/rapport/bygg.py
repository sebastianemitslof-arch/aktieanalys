"""Sätter samman veckorapportens innehåll.

Rapporten ska vara kort och läsbar. Ordningen är vald efter vad du
behöver veta först:

  1. Vad som föreslås, om något
  2. Vilka risker som flaggats
  3. Ställningen per kategori, med förändring mot förra veckan
  4. Vad som saknas i underlaget

Det sista ligger sist men är inte oviktigt: ser du att ett bolag
uteslutits tre veckor i rad vet du att något behöver fyllas i.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

from aktieanalys.byten import Bytesbeslut, Kategoribeslut
from aktieanalys.data.bolagsdata import Bolagsdata
from aktieanalys.data.lager import Lager
from aktieanalys.konfig import Installningar
from aktieanalys.kvalitativ.klient import Kvalitativtresultat
from aktieanalys.kvalitativ.schema import Bedomning
from aktieanalys.logg import hamta
from aktieanalys.poang.modell import Bolagspoang, Poangtavla
from aktieanalys.universum import Urval

logg = hamta(__name__)


@dataclass
class Rad:
    """Ett bolag i kategoritabellen."""

    ticker: str
    namn: str | None
    poang: float | None
    placering: int
    """Placering på kvantitativ poäng, i hela kategorin. Jämförbar vecka
    till vecka, för kategorins storlek ändras inte varje vecka."""
    ordning: int = 0
    """Placering i tabellen, på slutlig poäng efter riskjustering. Det är
    den ordning besluten följer, och därför den som visas som #."""
    ar_innehav: bool = False
    forandring_poang: float | None = None
    forandring_placering: int | None = None
    ar_ny: bool = False
    justering: float = 0.0
    risker: list = field(default_factory=list)
    saknade_matt: list[str] = field(default_factory=list)

    @property
    def visningsnamn(self) -> str:
        return self.namn or self.ticker


@dataclass
class Kategorirapport:
    namn: str
    rader: list[Rad] = field(default_factory=list)
    beslut: Kategoribeslut | None = None
    innehav: str | None = None
    innehav_varningar: list[str] = field(default_factory=list)
    bortvalda_matt: dict[str, str] = field(default_factory=dict)
    rangordningsbar: bool = True

    @property
    def innehavsrad(self) -> Rad | None:
        return next((r for r in self.rader if r.ar_innehav), None)


@dataclass
class Datakvalitet:
    uteslutna: list[tuple[str, str, str]] = field(default_factory=list)
    """(ticker, kategori, orsak)"""
    utan_data: dict[str, str] = field(default_factory=dict)
    obedombara: list[tuple[str, str, str]] = field(default_factory=list)
    gamla_varden: list[tuple[str, str, int]] = field(default_factory=list)
    """(ticker, fält, ålder i dagar)"""
    harledda_bolag: list[str] = field(default_factory=list)

    @property
    def nagot_att_visa(self) -> bool:
        return bool(
            self.uteslutna or self.utan_data or self.obedombara or self.gamla_varden
        )


@dataclass
class Veckorapport:
    datum: dt.date
    forra_datum: dt.date | None = None
    kategorier: list[Kategorirapport] = field(default_factory=list)
    beslut: Bytesbeslut | None = None
    kvalitativt: Kvalitativtresultat | None = None
    datakvalitet: Datakvalitet = field(default_factory=Datakvalitet)
    antal_bolag: int = 0
    installningar: Installningar | None = None

    @property
    def forslag(self) -> list[Kategoribeslut]:
        return self.beslut.forslag() if self.beslut else []

    @property
    def i_beslutsfonster(self) -> bool:
        return self.beslut.i_beslutsfonster if self.beslut else False

    @property
    def riskflaggor(self) -> dict[str, Bedomning]:
        if not self.kvalitativt:
            return {}
        return self.kvalitativt.med_risker()

    @property
    def rubrik(self) -> str:
        if self.forslag:
            return f"{len(self.forslag)} förslag"
        if not self.i_beslutsfonster:
            return "bevakning"
        return "inga förslag"

    def sammanfattning(self) -> str:
        delar = [f"{self.antal_bolag} bolag"]
        if self.forslag:
            delar.append(f"{len(self.forslag)} förslag")
        elif not self.i_beslutsfonster:
            delar.append("utanför beslutsfönster")
        if self.riskflaggor:
            delar.append(f"{len(self.riskflaggor)} med riskflagga")
        return ", ".join(delar)


# =====================================================================
def forra_rapportdatum(lager: Lager, datum: dt.date) -> dt.date | None:
    """Senaste rapportdatum före dagens."""
    tidigare = lager.rapportdatum(till=datum - dt.timedelta(days=1))
    return tidigare[0] if tidigare else None


def bygg_rapport(
    inst: Installningar,
    urval: Urval,
    tavla: Poangtavla,
    data: dict[str, Bolagsdata],
    beslut: Bytesbeslut,
    lager: Lager,
    kvalitativt: Kvalitativtresultat | None = None,
    antal_utmanare: int = 3,
) -> Veckorapport:
    datum = tavla.datum
    forra = forra_rapportdatum(lager, datum)
    rapport = Veckorapport(
        datum=datum,
        forra_datum=forra,
        beslut=beslut,
        kvalitativt=kvalitativt,
        installningar=inst,
    )

    for kategori, kp in tavla.kategorier.items():
        kr = Kategorirapport(
            namn=kategori,
            beslut=beslut.kategorier.get(kategori),
            innehav=kp.innehav,
            innehav_varningar=list(kp.innehav_varningar),
            bortvalda_matt=dict(kp.bortvalda_matt),
            rangordningsbar=kp.rangordningsbar,
        )

        forra_rader = lager.veckopoang_vid(forra, kategori) if forra else {}

        # Innehavet först, sedan toppen -- utan dubbletter
        visa: list[Bolagspoang] = []
        innehav_bp = kp.innehavspoang()
        if innehav_bp is not None:
            visa.append(innehav_bp)
        for bp in kp.topp(antal_utmanare):
            if bp.ticker not in {b.ticker for b in visa}:
                visa.append(bp)

        for bp in visa:
            bedomning = (
                kvalitativt.bedomningar.get(bp.ticker) if kvalitativt else None
            )
            poang = bp.totalpoang
            if bedomning and bedomning.anvand:
                poang = bedomning.justerad_poang(bp.totalpoang)

            rad = Rad(
                ticker=bp.ticker,
                namn=bp.namn,
                poang=poang,
                placering=kp.placering(bp.ticker) or 0,
                ar_innehav=bp.ar_innehav,
                justering=bedomning.justering if bedomning and bedomning.anvand else 0.0,
                risker=list(bedomning.risker) if bedomning else [],
                saknade_matt=bp.saknade_matt(),
            )

            tidigare = forra_rader.get(bp.ticker)
            if tidigare is None:
                rad.ar_ny = bool(forra)
            else:
                if poang is not None and tidigare["poang_slutlig"] is not None:
                    rad.forandring_poang = poang - tidigare["poang_slutlig"]
                if rad.placering and tidigare["placering"]:
                    rad.forandring_placering = tidigare["placering"] - rad.placering
            kr.rader.append(rad)

        # Tabellen sorteras på slutlig poäng, inte på den kvantitativa.
        # Annars kan nummer 2 stå med högre poäng än nummer 1, vilket
        # hände 2026-11-15: RUSTA 69,7 under EVO 69,2. Innehavet behåller
        # sin plats i ordningen -- det markeras ändå särskilt.
        kr.rader.sort(key=lambda r: (r.poang is None, -(r.poang or 0.0)))
        for n, rad in enumerate(kr.rader, start=1):
            rad.ordning = n

        rapport.kategorier.append(kr)
        rapport.antal_bolag += len(kp.bolag)

    rapport.datakvalitet = _datakvalitet(inst, urval, tavla, data, datum)
    logg.info("rapport byggd: %s", rapport.sammanfattning())
    return rapport


def _datakvalitet(
    inst: Installningar,
    urval: Urval,
    tavla: Poangtavla,
    data: dict[str, Bolagsdata],
    datum: dt.date,
) -> Datakvalitet:
    from aktieanalys.data.harledda import Harledare

    dk = Datakvalitet(utan_data=dict(urval.utan_data))

    for u in urval.alla_uteslutna():
        dk.uteslutna.append((u.ticker, u.kategori, u.orsak))

    for kategori, kp in tavla.kategorier.items():
        for bp in kp.obedombara:
            dk.obedombara.append((bp.ticker, kategori, bp.orsak or ""))

    # Värden som börjar bli gamla -- datakällan kan ha slutat leverera dem
    for ticker, bd in data.items():
        for falt, alder in bd.aldre_an(21, datum).items():
            dk.gamla_varden.append((ticker, falt, alder))
        if any(Harledare.ar_harlett(bd, f) for f in bd.varden):
            dk.harledda_bolag.append(ticker)

    dk.gamla_varden.sort(key=lambda x: -x[2])
    return dk
