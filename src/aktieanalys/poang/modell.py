"""Poängmodellen: från nyckeltal till rangordning.

Två steg:

  1. RANGORDNA   varje mått görs om till percentilrank 0-100 INOM
                 kategorin. Ett fastighetsbolag jämförs aldrig med ett
                 läkemedelsbolag, och ingen absolut nivå spelar roll --
                 bara ordningen mellan bolagen.

  2. VÄG SAMMAN  ranken vägs ihop inom varje pelare, och pelarna vägs
                 ihop till en totalpoäng, med vikterna ur config.yaml.

Saknad data hanteras genom OMVIKTNING, inte genom nollor. Saknar ett
bolag ett av tre värderingsmått räknas värderingspelaren på de två som
finns, med deras vikter uppräknade. En nolla hade betytt "sämst i
klassen", vilket är ett helt annat påstående än "vi vet inte".

Men omviktning har en gräns. Går mindre än min_andel_poangvikt av
vikten att sätta får bolaget ingen poäng alls och listas som obedömbart.
Annars hade ett bolag kunnat ranka först i sin kategori på enbart
momentum, vilket ser ut som ett svar utan att vara det.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

import pandas as pd

from aktieanalys.data.bolagsdata import Bolagsdata
from aktieanalys.konfig import Installningar
from aktieanalys.logg import hamta
from aktieanalys.poang.berakning import berakna
from aktieanalys.poang.matt import MATT, Riktning
from aktieanalys.universum import Urval

logg = hamta(__name__)

NEUTRAL_RANK = 50.0
"""Rank när det bara finns ett bolag att jämföra med -- varken bra eller dåligt."""


# =====================================================================
#  Resultatstrukturer
# =====================================================================
@dataclass
class Mattvarde:
    namn: str
    varde: float | None = None
    rank: float | None = None
    vikt: float = 0.0
    anvand_vikt: float = 0.0
    orsak: str | None = None

    @property
    def finns(self) -> bool:
        return self.rank is not None


@dataclass
class Pelarpoang:
    namn: str
    vikt: float
    poang: float | None = None
    matt: list[Mattvarde] = field(default_factory=list)
    andel_vikt: float = 0.0
    """Hur stor del av pelarens måttvikt som gick att sätta."""

    @property
    def finns(self) -> bool:
        return self.poang is not None


@dataclass
class Bolagspoang:
    ticker: str
    kategori: str
    namn: str | None = None
    totalpoang: float | None = None
    pelare: dict[str, Pelarpoang] = field(default_factory=dict)
    andel_vikt: float = 0.0
    bedombar: bool = False
    orsak: str | None = None
    ar_innehav: bool = False

    def saknade_matt(self) -> list[str]:
        return [
            m.namn for p in self.pelare.values() for m in p.matt if not m.finns
        ]

    def rank_for(self, mattnamn: str) -> float | None:
        for p in self.pelare.values():
            for m in p.matt:
                if m.namn == mattnamn:
                    return m.rank
        return None


@dataclass
class Kategoripoang:
    namn: str
    mattuppsattning: str
    bolag: list[Bolagspoang] = field(default_factory=list)
    """Bedömbara bolag, bäst först."""
    obedombara: list[Bolagspoang] = field(default_factory=list)
    anvandbara_matt: list[str] = field(default_factory=list)
    bortvalda_matt: dict[str, str] = field(default_factory=dict)
    innehav: str | None = None
    innehav_varningar: list[str] = field(default_factory=list)

    def topp(self, antal: int) -> list[Bolagspoang]:
        return self.bolag[:antal]

    def innehavspoang(self) -> Bolagspoang | None:
        for b in self.bolag + self.obedombara:
            if b.ar_innehav:
                return b
        return None

    def placering(self, ticker: str) -> int | None:
        """Placering från 1 och uppåt. None om bolaget inte är bedömbart."""
        for n, b in enumerate(self.bolag, start=1):
            if b.ticker == ticker:
                return n
        return None

    @property
    def rangordningsbar(self) -> bool:
        return len(self.bolag) >= 2


@dataclass
class Poangtavla:
    datum: dt.date
    kategorier: dict[str, Kategoripoang] = field(default_factory=dict)

    @property
    def antal_bedomda(self) -> int:
        return sum(len(k.bolag) for k in self.kategorier.values())

    @property
    def antal_obedombara(self) -> int:
        return sum(len(k.obedombara) for k in self.kategorier.values())

    def sammanfattning(self) -> str:
        delar = [f"{self.antal_bedomda} bolag poängsatta"]
        if self.antal_obedombara:
            delar.append(f"{self.antal_obedombara} obedömbara")
        ej = [n for n, k in self.kategorier.items() if not k.rangordningsbar]
        if ej:
            delar.append(f"{len(ej)} kategorier utan rangordning")
        return ", ".join(delar)


# =====================================================================
#  Steg 1: percentilrank
# =====================================================================
def percentilrank(
    varden: dict[str, float], hogre_ar_battre: bool
) -> dict[str, float]:
    """Gör om värden till rank 0-100 inom gruppen.

    Bäst får 100, sämst får 0, lika värden delar på snittplaceringen.
    Skalan är oberoende av hur många bolag som jämförs, så en kategori
    med sju bolag går att jämföra med en som har sexton.
    """
    if not varden:
        return {}
    if len(varden) == 1:
        return {t: NEUTRAL_RANK for t in varden}

    serie = pd.Series(varden, dtype=float)
    # ascending=True ger högsta värdet högsta rangnumret.
    rang = serie.rank(ascending=hogre_ar_battre, method="average")
    skala = (rang - 1.0) / (len(serie) - 1.0) * 100.0
    return {t: float(v) for t, v in skala.items()}


# =====================================================================
#  Steg 2: väg samman
# =====================================================================
def _vag_samman(
    delar: list[tuple[float, float]]
) -> tuple[float | None, float]:
    """Viktat medelvärde av (poäng, vikt), med vikterna omviktade.

    Returnerar poängen och hur stor del av den ursprungliga vikten som
    gick att använda.
    """
    total_vikt = sum(v for _, v in delar)
    tillgangliga = [(p, v) for p, v in delar if p is not None]
    anvand_vikt = sum(v for _, v in tillgangliga)
    if total_vikt == 0 or anvand_vikt == 0:
        return None, 0.0
    poang = sum(p * v for p, v in tillgangliga) / anvand_vikt
    return poang, anvand_vikt / total_vikt


def _poangsatt_bolag(
    ticker: str,
    kategori: str,
    bd: Bolagsdata,
    varden: dict[str, dict[str, float]],
    rankningar: dict[str, dict[str, float]],
    inst: Installningar,
    anvandbara: list[str],
    ar_innehav: bool,
) -> Bolagspoang:
    upps = inst.mattuppsattning_for(kategori)
    bp = Bolagspoang(
        ticker=ticker, kategori=kategori, namn=bd.namn, ar_innehav=ar_innehav
    )

    pelardelar: list[tuple[float, float]] = []
    for pelarnamn, matt in upps.som_dict().items():
        pelarvikt = inst.config.poang.vikter.som_dict()[pelarnamn]
        pelare = Pelarpoang(namn=pelarnamn, vikt=pelarvikt)

        mattdelar: list[tuple[float, float]] = []
        for mattnamn, vikt in matt.items():
            mv = Mattvarde(namn=mattnamn, vikt=vikt)
            if mattnamn not in anvandbara:
                mv.orsak = "måttet används inte i kategorin"
            else:
                mv.varde = varden.get(mattnamn, {}).get(ticker)
                mv.rank = rankningar.get(mattnamn, {}).get(ticker)
                if mv.rank is None:
                    saknade = bd.saknade_av(MATT[mattnamn].kraver)
                    mv.orsak = (
                        f"saknar {', '.join(saknade)}" if saknade
                        else "talet gick inte att räkna ut på de värden som finns"
                    )
            pelare.matt.append(mv)
            # Mått som inte används alls i kategorin räknas inte med i
            # nämnaren -- de är bortvalda för alla bolag, inte saknade
            # för just det här.
            if mv.orsak != "måttet används inte i kategorin":
                mattdelar.append((mv.rank, vikt))

        pelare.poang, pelare.andel_vikt = _vag_samman(mattdelar)
        for mv in pelare.matt:
            if mv.finns and pelare.andel_vikt > 0:
                anvand = sum(
                    m.vikt for m in pelare.matt
                    if m.finns and m.orsak != "måttet används inte i kategorin"
                )
                mv.anvand_vikt = mv.vikt / anvand if anvand else 0.0
        bp.pelare[pelarnamn] = pelare
        pelardelar.append((pelare.poang, pelarvikt))

    bp.totalpoang, bp.andel_vikt = _vag_samman(pelardelar)

    grans = inst.config.poang.min_andel_poangvikt
    if bp.totalpoang is None:
        bp.bedombar = False
        bp.orsak = "inget mått gick att räkna ut"
    elif bp.andel_vikt < grans:
        bp.bedombar = False
        bp.orsak = (
            f"bara {bp.andel_vikt:.0%} av poängvikten gick att sätta, "
            f"kravet är {grans:.0%} (saknar {', '.join(bp.saknade_matt())})"
        )
        bp.totalpoang = None
    else:
        bp.bedombar = True
    return bp


# =====================================================================
#  Huvudfunktionen
# =====================================================================
def poangsatt(
    inst: Installningar,
    urval: Urval,
    data: dict[str, Bolagsdata],
    datum: dt.date | None = None,
) -> Poangtavla:
    """Räknar ut poäng och rangordning för hela universumet."""
    datum = datum or urval.datum
    min_ar = inst.config.poang.min_ar_historik
    tavla = Poangtavla(datum=datum)

    for kategori, ku in urval.kategorier.items():
        kp = Kategoripoang(
            namn=kategori,
            mattuppsattning=ku.mattuppsattning,
            anvandbara_matt=list(ku.anvandbara_matt),
            bortvalda_matt=dict(ku.bortvalda_matt),
            innehav=ku.innehav,
            innehav_varningar=list(ku.innehav_varningar),
        )

        # --- räkna ut alla mått för alla bolag ---
        varden: dict[str, dict[str, float]] = {}
        for mattnamn in ku.anvandbara_matt:
            per_bolag = {}
            for ticker in ku.med:
                bd = data.get(ticker)
                if bd is None:
                    continue
                v = berakna(mattnamn, bd, min_ar)
                if v is not None:
                    per_bolag[ticker] = v
            varden[mattnamn] = per_bolag

        # --- rangordna inom kategorin ---
        rankningar: dict[str, dict[str, float]] = {
            mattnamn: percentilrank(
                per_bolag, MATT[mattnamn].riktning is Riktning.HOGRE_BATTRE
            )
            for mattnamn, per_bolag in varden.items()
        }

        # --- väg samman per bolag ---
        for ticker in ku.med:
            bd = data.get(ticker)
            if bd is None:
                continue
            bp = _poangsatt_bolag(
                ticker, kategori, bd, varden, rankningar, inst,
                ku.anvandbara_matt, ticker == ku.innehav,
            )
            (kp.bolag if bp.bedombar else kp.obedombara).append(bp)

        kp.bolag.sort(key=lambda b: b.totalpoang, reverse=True)
        kp.obedombara.sort(key=lambda b: b.ticker)

        if not kp.rangordningsbar:
            logg.warning(
                "%s: bara %d bedömbara bolag, ingen meningsfull rangordning",
                kategori, len(kp.bolag),
            )
        tavla.kategorier[kategori] = kp

    logg.info("poängsättning klar: %s", tavla.sammanfattning())
    return tavla
