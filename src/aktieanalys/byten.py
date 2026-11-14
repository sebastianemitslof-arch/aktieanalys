"""Bytesreglerna: när ett byte alls får föreslås.

Alla villkor måste hålla samtidigt. Syftet är låg omsättning -- ett byte
ska vara ett undantag, inte en veckovana:

  TRÖSKEL     utmanaren minst X procent bättre i totalpoäng
  UTHÅLLIGHET och det i N veckorapporter i rad, så att en enstaka
              veckas brus inte räcker
  INNEHAVSTID innehavet måste ha ägts i minst M månader
  FÖNSTER     byten föreslås bara i beslutsfönstren, efter
              rapportperioderna. Övriga veckor är rapporten bevakning.

Ett undantag finns: flaggar det kvalitativa lagret FUNDAMENTAL
FÖRSÄMRING i innehavet -- vinstvarning, indragen prognos -- åsidosätts
innehavstiden och beslutsfönstret. Tröskeln och uthålligheten gäller
fortfarande; en försämring i det egna innehavet gör inte automatiskt
någon annan värd att köpa.

Rapporten visar ALLA villkor, uppfyllda som ej uppfyllda. Att en
utmanare är 24 procent bättre men bara varit det i en vecka är något du
ska få se, inte något som ska tigas ihjäl.

Kostnaden räknas ut och visas, men dras inte från poängen. Poängen är en
relativ rangordning, inte en avkastningsprognos, så det finns inget
ärligt sätt att kvitta kronor mot poäng. Kostnaden är ett underlag för
ditt beslut, inte en term i en formel.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

from aktieanalys.data.bolagsdata import Bolagsdata
from aktieanalys.data.lager import Lager
from aktieanalys.konfig import Installningar
from aktieanalys.kvalitativ.klient import Kvalitativtresultat
from aktieanalys.logg import hamta
from aktieanalys.poang.modell import Bolagspoang, Poangtavla

logg = hamta(__name__)


@dataclass
class Villkor:
    namn: str
    uppfyllt: bool
    text: str
    undantaget: bool = False
    """Sant när villkoret åsidosatts av en flaggad fundamental försämring."""

    @property
    def ok(self) -> bool:
        return self.uppfyllt or self.undantaget


@dataclass
class Kostnad:
    belopp_kr: float
    courtage_salj: float = 0.0
    courtage_kop: float = 0.0
    spread_procent: float = 0.0
    spread_kr: float = 0.0
    spread_kalla: str = ""
    kand_positionsstorlek: bool = False

    @property
    def total_kr(self) -> float:
        return self.courtage_salj + self.courtage_kop + self.spread_kr

    @property
    def total_procent(self) -> float:
        return self.total_kr / self.belopp_kr * 100 if self.belopp_kr else 0.0

    def hog(self, grans_procent: float) -> bool:
        return self.total_procent > grans_procent


@dataclass
class Kategoribeslut:
    kategori: str
    typ: str
    """byte, forstagangskop, behall eller obedombar"""
    innehav: str | None = None
    innehavspoang: float | None = None
    utmanare: str | None = None
    utmanarpoang: float | None = None
    forbattring_procent: float | None = None
    veckor_i_rad: int = 0
    datum_i_rad: list[dt.date] = field(default_factory=list)
    innehavstid_manader: float | None = None
    villkor: list[Villkor] = field(default_factory=list)
    kostnad: Kostnad | None = None
    foreslas: bool = False
    undantag: str | None = None
    motivering: str = ""

    @property
    def ej_uppfyllda(self) -> list[Villkor]:
        return [v for v in self.villkor if not v.ok]


@dataclass
class Bytesbeslut:
    datum: dt.date
    i_beslutsfonster: bool
    kategorier: dict[str, Kategoribeslut] = field(default_factory=dict)

    def forslag(self) -> list[Kategoribeslut]:
        return [k for k in self.kategorier.values() if k.foreslas]

    def sammanfattning(self) -> str:
        n = len(self.forslag())
        if not self.i_beslutsfonster:
            return (
                f"utanför beslutsfönster -- bevakning, {n} förslag" if n
                else "utanför beslutsfönster -- enbart bevakning"
            )
        return f"{n} bytesförslag" if n else "inga bytesförslag"


# =====================================================================
#  Kostnad
# =====================================================================
def _courtage(belopp: float, procent: float, min_kr: float) -> float:
    return max(min_kr, belopp * procent / 100)


def _spread_procent(bd: Bolagsdata | None) -> float | None:
    """Uppmätt spread ur köp- och säljkurs, i procent."""
    if bd is None:
        return None
    kop, salj = bd.varde("kopkurs"), bd.varde("saljkurs")
    if not kop or not salj or kop <= 0 or salj <= 0:
        return None
    mitt = (kop + salj) / 2
    if mitt <= 0:
        return None
    return abs(kop - salj) / mitt * 100


def berakna_kostnad(
    inst: Installningar,
    innehav_bd: Bolagsdata | None,
    utmanare_bd: Bolagsdata | None,
    antal: float = 0.0,
    saljer: bool = True,
) -> Kostnad:
    """Vad ett byte kostar: courtage på båda affärerna plus spread.

    Kontot är ett ISK, så ingen vinstskatt räknas in.
    """
    c = inst.config.kostnader

    belopp = 0.0
    kand = False
    if antal and innehav_bd and innehav_bd.varde("kurs"):
        belopp = antal * innehav_bd.varde("kurs")
        kand = True
    if not belopp:
        belopp = c.antagen_positionsstorlek_kr

    kostnad = Kostnad(belopp_kr=belopp, kand_positionsstorlek=kand)

    uppmatta = [s for s in (_spread_procent(innehav_bd), _spread_procent(utmanare_bd))
                if s is not None]
    if uppmatta:
        snitt = sum(uppmatta) / len(uppmatta)
        kostnad.spread_procent = max(snitt, c.spread_golv_procent)
        kostnad.spread_kalla = (
            f"uppmätt {snitt:.2f} %" if snitt >= c.spread_golv_procent
            else f"uppmätt {snitt:.2f} %, höjd till golvet {c.spread_golv_procent} %"
        )
    else:
        kostnad.spread_procent = c.spread_golv_procent
        kostnad.spread_kalla = f"golvet {c.spread_golv_procent} % (ingen kursdata)"

    # Halva spreaden per affär: man korsar den i mitten.
    ben = 2 if saljer else 1
    kostnad.spread_kr = belopp * kostnad.spread_procent / 100 / 2 * ben

    if saljer:
        kostnad.courtage_salj = _courtage(belopp, c.courtage_procent, c.courtage_min_kr)
    kostnad.courtage_kop = _courtage(belopp, c.courtage_procent, c.courtage_min_kr)
    return kostnad


# =====================================================================
#  Uthållighet: hur många veckor i rad
# =====================================================================
def rakna_veckor_i_rad(
    lager: Lager,
    kategori: str,
    utmanare: str,
    innehav: str,
    troskel_procent: float,
    idag: dt.date,
    max_veckor: int = 26,
) -> tuple[int, list[dt.date]]:
    """Hur många rapporter i rad utmanaren varit tillräckligt mycket bättre.

    Dagens körning räknas av anroparen, som har den färska poängen.
    Här räknas bara TIDIGARE rapportdatum, nyast först, och räkningen
    bryts vid första veckan då villkoret inte höll.
    """
    antal, datum = 0, []
    for d in lager.rapportdatum(kategori, till=idag - dt.timedelta(days=1)):
        if antal >= max_veckor:
            break
        rader = lager.veckopoang_vid(d, kategori)
        u = rader.get(utmanare, {}).get("poang_slutlig")
        i = rader.get(innehav, {}).get("poang_slutlig")
        if u is None or i is None or i <= 0:
            break
        if u < i * (1 + troskel_procent / 100):
            break
        antal += 1
        datum.append(d)
    return antal, datum


# =====================================================================
#  Huvudfunktionen
# =====================================================================
def bedom_byten(
    inst: Installningar,
    tavla: Poangtavla,
    data: dict[str, Bolagsdata],
    lager: Lager,
    kvalitativt: Kvalitativtresultat | None = None,
    datum: dt.date | None = None,
) -> Bytesbeslut:
    datum = datum or tavla.datum
    c = inst.config.byten
    i_fonster = inst.i_beslutsfonster(datum)
    beslut = Bytesbeslut(datum=datum, i_beslutsfonster=i_fonster)

    for kategori, kp in tavla.kategorier.items():
        kb = _bedom_kategori(
            inst, kategori, kp, data, lager, kvalitativt, datum, i_fonster
        )
        beslut.kategorier[kategori] = kb

    logg.info("bytesbedömning klar: %s", beslut.sammanfattning())
    return beslut


def _poang(bp: Bolagspoang, kvalitativt: Kvalitativtresultat | None) -> float | None:
    """Totalpoäng efter eventuell kvalitativ justering."""
    if bp.totalpoang is None:
        return None
    if kvalitativt and bp.ticker in kvalitativt.bedomningar:
        return kvalitativt.bedomningar[bp.ticker].justerad_poang(bp.totalpoang)
    return bp.totalpoang


def _bedom_kategori(
    inst: Installningar,
    kategori: str,
    kp,
    data: dict[str, Bolagsdata],
    lager: Lager,
    kvalitativt: Kvalitativtresultat | None,
    datum: dt.date,
    i_fonster: bool,
) -> Kategoribeslut:
    c = inst.config.byten
    innehav_konfig = inst.innehav_i(kategori)

    if not kp.rangordningsbar:
        return Kategoribeslut(
            kategori=kategori, typ="obedombar", innehav=innehav_konfig.ticker,
            motivering="kategorin går inte att rangordna -- för få bedömbara bolag",
        )

    # ---- förstagångsköp -------------------------------------------
    if not innehav_konfig.finns:
        basta = kp.bolag[0]
        kb = Kategoribeslut(
            kategori=kategori, typ="forstagangskop",
            utmanare=basta.ticker, utmanarpoang=_poang(basta, kvalitativt),
        )
        kb.villkor.append(Villkor(
            "beslutsfönster", i_fonster,
            "inom beslutsfönster" if i_fonster
            else f"utanför beslutsfönster ({datum:%d %b}) -- enbart bevakning",
        ))
        kb.kostnad = berakna_kostnad(inst, None, data.get(basta.ticker), saljer=False)
        kb.foreslas = all(v.ok for v in kb.villkor)
        kb.motivering = (
            f"inget innehav i kategorin. {basta.ticker} rankar högst "
            f"({kb.utmanarpoang:.1f} poäng)."
        )
        return kb

    # ---- byte ------------------------------------------------------
    innehav_bp = kp.innehavspoang()
    innehavspoang = _poang(innehav_bp, kvalitativt) if innehav_bp else None

    kb = Kategoribeslut(
        kategori=kategori, typ="behall",
        innehav=innehav_konfig.ticker, innehavspoang=innehavspoang,
        innehavstid_manader=innehav_konfig.innehavstid_manader(datum),
    )

    utmanare = [b for b in kp.bolag if b.ticker != innehav_konfig.ticker]
    if not utmanare or innehavspoang is None or innehavspoang <= 0:
        kb.motivering = (
            "innehavet går inte att poängsätta -- ingen jämförelse möjlig"
            if innehavspoang is None else "inga utmanare i kategorin"
        )
        return kb

    basta = max(utmanare, key=lambda b: _poang(b, kvalitativt) or 0.0)
    utmanarpoang = _poang(basta, kvalitativt)
    forbattring = (utmanarpoang - innehavspoang) / innehavspoang * 100

    kb.utmanare = basta.ticker
    kb.utmanarpoang = utmanarpoang
    kb.forbattring_procent = forbattring

    # Undantaget: fundamental försämring i det EGNA innehavet
    forsamring = bool(
        kvalitativt
        and c.tillat_undantag_vid_riskflagga
        and kvalitativt.bedomningar.get(innehav_konfig.ticker)
        and kvalitativt.bedomningar[innehav_konfig.ticker].fundamental_forsamring
    )
    if forsamring:
        kb.undantag = (
            "fundamental försämring flaggad i innehavet -- kraven på "
            "innehavstid och beslutsfönster åsidosätts"
        )

    # -- villkor 1: tröskeln (gäller alltid, även vid undantag) ------
    kb.villkor.append(Villkor(
        "tröskel",
        forbattring >= c.min_poangforbattring_procent,
        f"utmanaren är {forbattring:+.1f} % bättre "
        f"(kravet är +{c.min_poangforbattring_procent:.0f} %)",
    ))

    # -- villkor 2: uthållighet (gäller alltid) ----------------------
    tidigare, datumlista = rakna_veckor_i_rad(
        lager, kategori, basta.ticker, innehav_konfig.ticker,
        c.min_poangforbattring_procent, datum,
    )
    denna_vecka = 1 if forbattring >= c.min_poangforbattring_procent else 0
    kb.veckor_i_rad = denna_vecka + (tidigare if denna_vecka else 0)
    kb.datum_i_rad = ([datum] + datumlista) if denna_vecka else []
    kb.villkor.append(Villkor(
        "uthållighet",
        kb.veckor_i_rad >= c.antal_veckor_i_rad,
        f"{kb.veckor_i_rad} av {c.antal_veckor_i_rad} veckorapporter i rad",
    ))

    # -- villkor 3: innehavstid (kan åsidosättas) --------------------
    tid = kb.innehavstid_manader
    kb.villkor.append(Villkor(
        "innehavstid",
        tid is not None and tid >= c.min_innehavstid_manader,
        f"{tid:.1f} av {c.min_innehavstid_manader} månader"
        if tid is not None else "köpdatum saknas",
        undantaget=forsamring,
    ))

    # -- villkor 4: beslutsfönster (kan åsidosättas) -----------------
    if c.endast_byten_i_beslutsfonster:
        kb.villkor.append(Villkor(
            "beslutsfönster", i_fonster,
            "inom beslutsfönster" if i_fonster
            else f"utanför beslutsfönster ({datum:%d %b}) -- enbart bevakning",
            undantaget=forsamring,
        ))

    kb.kostnad = berakna_kostnad(
        inst,
        data.get(innehav_konfig.ticker),
        data.get(basta.ticker),
        antal=innehav_konfig.antal,
        saljer=True,
    )

    kb.foreslas = all(v.ok for v in kb.villkor)
    kb.typ = "byte" if kb.foreslas else "behall"

    if kb.foreslas:
        kb.motivering = (
            f"{basta.ticker} har {forbattring:+.1f} % högre poäng än "
            f"{innehav_konfig.ticker} och har haft det i {kb.veckor_i_rad} "
            f"rapporter i rad."
        )
    else:
        brister = ", ".join(v.text for v in kb.ej_uppfyllda)
        kb.motivering = f"inget byte: {brister}"
    return kb


# =====================================================================
#  Loggning av veckans poäng
# =====================================================================
def spara_veckans_poang(
    lager: Lager,
    tavla: Poangtavla,
    kvalitativt: Kvalitativtresultat | None = None,
) -> int:
    """Skriver veckans poäng till databasen. Grunden för uthållighetsregeln."""
    rader = []
    for kategori, kp in tavla.kategorier.items():
        for n, bp in enumerate(kp.bolag, start=1):
            bedomning = (
                kvalitativt.bedomningar.get(bp.ticker) if kvalitativt else None
            )
            rader.append({
                "ticker": bp.ticker,
                "kategori": kategori,
                "poang_kvantitativ": bp.totalpoang,
                "poang_slutlig": _poang(bp, kvalitativt),
                "placering": n,
                "ar_innehav": bp.ar_innehav,
                "justering": bedomning.justering if bedomning and bedomning.anvand else 0.0,
            })
    return lager.spara_veckopoang(tavla.datum, rader)
