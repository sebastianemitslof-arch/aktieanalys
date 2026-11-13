"""Urvalet: vilka bolag som får vara med, och vilka som utesluts och varför.

Tre spärrar, i den här ordningen:

  1. STORLEK      börsvärde och daglig omsättning mot gränserna i config.yaml
  2. ANVÄNDBARA   vilka mått som alls går att rangordna på i kategorin
     MÅTT
  3. DATA         bolag som saknar data som de andra i kategorin har

Ordningen mellan steg 2 och 3 är viktig. Saknas ett mått för ALLA bolag i
en kategori är det måttet som ska falla bort ur modellen, inte bolagen ur
kategorin. Annars hade varje bank uteslutits bara för att ingen har
kapitaltäckningen ifylld -- och kategorin hade blivit tom i stället för
att bedömas på de fem mått som faktiskt går att räkna.

Ingenting försvinner tyst. Varje uteslutet bolag och varje bortvalt mått
bär sin orsak i klartext, hela vägen ut i veckorapporten.

Nuvarande innehav behandlas särskilt: de utesluts ALDRIG. Att ett innehav
har krympt under gränsen eller tappat data är i sig något du behöver få
veta, inte något som ska tystas genom att bolaget försvinner ur rapporten.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

from aktieanalys.data.bolagsdata import Bolagsdata
from aktieanalys.konfig import Installningar
from aktieanalys.logg import hamta
from aktieanalys.poang.matt import MATT

logg = hamta(__name__)

MDR = 1e9
MKR = 1e6


@dataclass
class Uteslutning:
    """Ett bolag som inte får vara med, och varför."""

    ticker: str
    kategori: str
    orsaker: list[str]

    @property
    def orsak(self) -> str:
        return "; ".join(self.orsaker)


@dataclass
class Kategoriurval:
    namn: str
    mattuppsattning: str
    med: list[str] = field(default_factory=list)
    uteslutna: list[Uteslutning] = field(default_factory=list)
    anvandbara_matt: list[str] = field(default_factory=list)
    bortvalda_matt: dict[str, str] = field(default_factory=dict)
    innehav: str | None = None
    innehav_varningar: list[str] = field(default_factory=list)
    """Varningar om det egna innehavet. Innehavet utesluts aldrig, men
    om det inte längre klarar gränserna ska det stå i rapporten."""

    @property
    def antal(self) -> int:
        return len(self.med)

    @property
    def rangordningsbar(self) -> bool:
        """Går kategorin att rangordna alls?"""
        return bool(self.anvandbara_matt) and self.antal >= 2

    def saknade_pelare(self, inst: Installningar) -> list[str]:
        """Pelare som inte har ett enda användbart mått kvar."""
        upps = inst.config.poang.mattuppsattningar[self.mattuppsattning]
        tomma = []
        for pelare, matt in upps.som_dict().items():
            if not any(m in self.anvandbara_matt for m in matt):
                tomma.append(pelare)
        return tomma


@dataclass
class Urval:
    datum: dt.date
    kategorier: dict[str, Kategoriurval] = field(default_factory=dict)
    utan_data: dict[str, str] = field(default_factory=dict)
    """Bolag som inte finns i databasen alls -- hämta data först."""

    @property
    def antal_med(self) -> int:
        return sum(k.antal for k in self.kategorier.values())

    @property
    def antal_uteslutna(self) -> int:
        return sum(len(k.uteslutna) for k in self.kategorier.values())

    def alla_uteslutna(self) -> list[Uteslutning]:
        return [u for k in self.kategorier.values() for u in k.uteslutna]

    def alla_med(self) -> list[str]:
        return [t for k in self.kategorier.values() for t in k.med]

    def sammanfattning(self) -> str:
        delar = [f"{self.antal_med} bolag med"]
        if self.antal_uteslutna:
            delar.append(f"{self.antal_uteslutna} uteslutna")
        if self.utan_data:
            delar.append(f"{len(self.utan_data)} utan data")
        ej = [n for n, k in self.kategorier.items() if not k.rangordningsbar]
        if ej:
            delar.append(f"{len(ej)} kategorier går inte att rangordna")
        return ", ".join(delar)


# =====================================================================
#  Spärr 1: storlek och likviditet
# =====================================================================
def _storleksorsaker(bd: Bolagsdata, inst: Installningar) -> list[str]:
    c = inst.config.universum
    orsaker: list[str] = []

    if bd.valuta and bd.valuta != "SEK":
        orsaker.append(
            f"handlas i {bd.valuta}, men storleksgränserna är satta i kronor"
        )

    borsvarde = bd.varde("borsvarde")
    if borsvarde is None:
        orsaker.append("börsvärde saknas, går inte att jämföra med storleksgränsen")
    elif borsvarde < c.min_borsvarde_mdr * MDR:
        orsaker.append(
            f"börsvärde {borsvarde / MDR:.1f} mdr kr, under gränsen "
            f"{c.min_borsvarde_mdr:.0f} mdr"
        )

    omsattning = bd.varde("daglig_omsattning")
    if omsattning is None:
        orsaker.append("daglig omsättning saknas, går inte att jämföra med gränsen")
    elif omsattning < c.min_daglig_omsattning_mkr * MKR:
        orsaker.append(
            f"omsättning {omsattning / MKR:.1f} mkr/dag, under gränsen "
            f"{c.min_daglig_omsattning_mkr:.0f} mkr"
        )
    return orsaker


# =====================================================================
#  Spärr 2: vilka mått som alls går att rangordna på
# =====================================================================
def _anvandbara_matt(
    matt: list[str], bolag: dict[str, Bolagsdata], min_bolag: int
) -> tuple[list[str], dict[str, str]]:
    """Ett mått behöver tillräckligt många bolag för att en percentilrank
    ska betyda något. En rank bland två bolag säger ingenting."""
    anvandbara: list[str] = []
    bortvalda: dict[str, str] = {}

    for m in matt:
        har = [t for t, bd in bolag.items() if bd.har_alla(MATT[m].kraver)]
        if len(har) >= min_bolag:
            anvandbara.append(m)
            continue

        if not har:
            saknade = sorted({
                f for bd in bolag.values() for f in bd.saknade_av(MATT[m].kraver)
            })
            bortvalda[m] = (
                f"inget bolag i kategorin har data för måttet "
                f"(saknar {', '.join(saknade[:4])})"
            )
        else:
            bortvalda[m] = (
                f"bara {len(har)} av {len(bolag)} bolag har data, "
                f"minst {min_bolag} krävs för en meningsfull rangordning"
            )
    return anvandbara, bortvalda


# =====================================================================
#  Huvudfunktionen
# =====================================================================
def valj_urval(
    inst: Installningar,
    data: dict[str, Bolagsdata],
    datum: dt.date | None = None,
) -> Urval:
    """Går igenom universumet och avgör vilka bolag som får vara med.

    data är vad datalagret känner till, per ticker. Bolag som inte finns
    där hamnar i urval.utan_data.
    """
    datum = datum or dt.date.today()
    urval = Urval(datum=datum)
    maxsaknade = inst.config.universum.max_saknade_matt_per_bolag
    minbolag = inst.config.poang.min_bolag_for_rank

    for kategori, kat in inst.universum.kategorier.items():
        innehav = inst.innehav_i(kategori)
        ku = Kategoriurval(
            namn=kategori,
            mattuppsattning=kat.mattuppsattning,
            innehav=innehav.ticker if innehav.finns else None,
        )

        # --- spärr 1: storlek -----------------------------------------
        kvar: dict[str, Bolagsdata] = {}
        for ticker in kat.bolag:
            bd = data.get(ticker)
            if bd is None or bd.antal_varden == 0:
                urval.utan_data[ticker] = (
                    "ingen data i databasen -- kör hamta-data"
                    if bd is None else "hämtningen gav inga värden"
                )
                if ticker == ku.innehav:
                    ku.innehav_varningar.append(
                        "INNEHAVET saknar data helt och kan inte bedömas"
                    )
                continue

            orsaker = _storleksorsaker(bd, inst)
            if orsaker and ticker != ku.innehav:
                ku.uteslutna.append(Uteslutning(ticker, kategori, orsaker))
                continue
            if orsaker:
                ku.innehav_varningar += [
                    f"INNEHAVET klarar inte längre urvalskraven: {o}" for o in orsaker
                ]
            kvar[ticker] = bd

        # --- spärr 2: användbara mått ---------------------------------
        upps = inst.mattuppsattning_for(kategori)
        ku.anvandbara_matt, ku.bortvalda_matt = _anvandbara_matt(
            upps.alla_matt(), kvar, minbolag
        )

        # --- spärr 3: data per bolag ----------------------------------
        for ticker, bd in kvar.items():
            saknade_matt = [
                m for m in ku.anvandbara_matt if not bd.har_alla(MATT[m].kraver)
            ]
            if len(saknade_matt) > maxsaknade and ticker != ku.innehav:
                ku.uteslutna.append(
                    Uteslutning(
                        ticker,
                        kategori,
                        [
                            f"saknar data för {len(saknade_matt)} mått "
                            f"({', '.join(saknade_matt)}), högst "
                            f"{maxsaknade} tillåts"
                        ],
                    )
                )
                continue
            if len(saknade_matt) > maxsaknade:
                ku.innehav_varningar.append(
                    f"INNEHAVET saknar data för {len(saknade_matt)} mått "
                    f"({', '.join(saknade_matt)}) och bedöms på ofullständigt underlag"
                )
            ku.med.append(ticker)

        if ku.bortvalda_matt:
            logg.info(
                "%s: %d mått går inte att använda (%s)",
                kategori, len(ku.bortvalda_matt), ", ".join(ku.bortvalda_matt),
            )
        if not ku.rangordningsbar:
            logg.warning(
                "%s går inte att rangordna: %d bolag, %d användbara mått",
                kategori, ku.antal, len(ku.anvandbara_matt),
            )
        urval.kategorier[kategori] = ku

    logg.info("urval klart: %s", urval.sammanfattning())
    return urval


def las_och_valj(
    inst: Installningar, datum: dt.date | None = None
) -> tuple[Urval, dict[str, Bolagsdata]]:
    """Läser databasen och gör urvalet. Hämtar inget från nätet.

    datum styr punkt-i-tiden-läsningen, så att backtestet kan göra
    urvalet med bara den data som fanns vid varje tidpunkt.
    """
    from aktieanalys.data.lager import Lager

    datum = datum or dt.date.today()
    with Lager(inst.databasvag) as lager:
        data = {}
        for ticker in inst.alla_tickers():
            bd = lager.las(ticker, datum)
            if bd is not None:
                data[ticker] = bd
    return valj_urval(inst, data, datum), data
