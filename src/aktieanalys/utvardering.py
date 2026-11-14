"""Utvärdering: har poängen något samband med efterföljande avkastning?

Backtestet svarar på fel fråga. "Gav portföljen bra avkastning?" har sex
observationer per år och domineras av slump -- vi mätte att det skulle
krävas sjuttiofem år för att se en kant på två procentenheter.

Den här modulen ställer en annan fråga, med hundra gånger fler
datapunkter:

    Rangordnade modellen bolagen i rätt ordning?

För varje mättillfälle poängsätts alla bolag med den data som fanns då,
och poängen jämförs med hur det faktiskt gick de följande tolv
månaderna. Måttet är Spearmans rangkorrelation, i facket kallad
INFORMATION COEFFICIENT.

Tolkning, grovt:

    IC omkring 0      ingen information alls
    IC 0,02 - 0,05    svagt men användbart, vanligt för riktiga modeller
    IC över 0,10      ovanligt starkt, misstänk ett fel i uppställningen

Korrelationen räknas INOM varje kategori, eftersom poängen är relativ
och aldrig jämförbar mellan kategorier.

Två varningar som följer med resultatet:

* Överlappande tolvmånadersfönster är inte oberoende. Mäter man varje
  månad delar grannperioderna elva månaders avkastning, vilket blåser
  upp t-värdet. Därför redovisas även de icke-överlappande perioderna.
* Fyra räkenskapsår räcker till ett par oberoende perioder. Det är
  fortfarande tunt -- men det är ett ärligare mått än portföljens
  avkastning.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

import pandas as pd

from aktieanalys.backtest import historisk_bolagsdata
from aktieanalys.data.bolagsdata import Bolagsdata
from aktieanalys.konfig import Installningar
from aktieanalys.logg import hamta
from aktieanalys.poang.modell import poangsatt
from aktieanalys.universum import valj_urval

logg = hamta(__name__)

PELARE = ("kvalitet", "vardering", "finansiell_styrka", "momentum")


@dataclass
class Period:
    datum: dt.date
    slutdatum: dt.date
    antal_bolag: int
    ic: float | None = None
    ic_per_pelare: dict[str, float | None] = field(default_factory=dict)


@dataclass
class ICresultat:
    perioder: list[Period] = field(default_factory=list)
    horisont_dagar: int = 365
    varningar: list[str] = field(default_factory=list)
    noll: list[float] = field(default_factory=list)
    """IC när poängen slumpas. Bruset som allt annat ska mätas mot."""
    stabilitet: dict[str, float] = field(default_factory=dict)
    """Hur mycket rangordningen rör sig på ett år, per kategori."""

    def nollband(self) -> tuple[float, float, float] | None:
        """Medel samt 5:e och 95:e percentilen för rent brus."""
        if len(self.noll) < 50:
            return None
        s = pd.Series(self.noll)
        return float(s.mean()), float(s.quantile(0.05)), float(s.quantile(0.95))

    def inom_bruset(self) -> bool | None:
        band = self.nollband()
        s = self.sammanfatta(self.oberoende)
        if band is None or s["antal"] == 0:
            return None
        return band[1] <= s["medel"] <= band[2]

    def genomsnittlig_stabilitet(self) -> float | None:
        if not self.stabilitet:
            return None
        return float(pd.Series(list(self.stabilitet.values())).mean())

    def _varden(self, perioder: list[Period]) -> list[float]:
        return [p.ic for p in perioder if p.ic is not None]

    @property
    def oberoende(self) -> list[Period]:
        """Perioder vars mätfönster inte överlappar varandra."""
        ut: list[Period] = []
        for p in sorted(self.perioder, key=lambda x: x.datum):
            if not ut or p.datum >= ut[-1].slutdatum:
                ut.append(p)
        return ut

    def sammanfatta(self, perioder: list[Period] | None = None) -> dict:
        varden = self._varden(perioder if perioder is not None else self.perioder)
        if not varden:
            return {"antal": 0}
        serie = pd.Series(varden)
        medel, sd, n = serie.mean(), serie.std(), len(serie)
        return {
            "antal": n,
            "medel": float(medel),
            "sd": float(sd) if n > 1 else 0.0,
            "t": float(medel / (sd / n**0.5)) if n > 1 and sd > 0 else 0.0,
            "andel_positiva": float((serie > 0).mean() * 100),
        }

    def per_pelare(self) -> dict[str, dict]:
        ut = {}
        for pelare in PELARE:
            varden = [
                p.ic_per_pelare.get(pelare) for p in self.perioder
                if p.ic_per_pelare.get(pelare) is not None
            ]
            if not varden:
                continue
            serie = pd.Series(varden)
            ut[pelare] = {
                "medel": float(serie.mean()),
                "andel_positiva": float((serie > 0).mean() * 100),
                "antal": len(serie),
            }
        return ut

    def omdome(self) -> str:
        s = self.sammanfatta(self.oberoende)
        if s["antal"] < 2:
            return (
                "För få oberoende perioder för att säga någonting. "
                "Historiken räcker inte."
            )
        medel = s["medel"]
        band = self.nollband()
        stabilitet = self.genomsnittlig_stabilitet()

        delar = [f"Genomsnittlig IC {medel:+.3f} över {s['antal']} "
                 f"oberoende perioder."]

        if band is not None:
            if self.inom_bruset():
                delar.append(
                    f"Men slumpad poäng ger {band[1]:+.3f} till {band[2]:+.3f} "
                    f"i samma uppställning, så värdet ligger INOM bruset. "
                    f"Det går inte att skilja från ingenting."
                )
            else:
                delar.append(
                    f"Bruset ligger på {band[1]:+.3f} till {band[2]:+.3f}, "
                    f"så värdet ligger utanför det."
                )

        # Ett genomsnitt kan dölja att enskilda kategorier är frysta.
        # Industri på +0,92 gör samma vad varje år även om snittet ser
        # beskedligt ut.
        frysta = sorted(
            (k for k, v in self.stabilitet.items() if v > 0.8),
            key=lambda k: -self.stabilitet[k],
        )
        if frysta:
            delar.append(
                f"I {', '.join(frysta)} är rangordningen nästan oförändrad "
                f"mellan åren, så där mäts samma vad om och om igen. "
                f"Det verkliga antalet oberoende observationer är för de "
                f"kategorierna närmare ett än {s['antal']}."
            )
        elif stabilitet is not None and stabilitet > 0.5:
            delar.append(
                f"Rangordningen är trögrörlig (stabilitet {stabilitet:+.2f}), "
                f"så perioderna är mindre oberoende än antalet antyder."
            )
        return " ".join(delar)


# =====================================================================
def _framtida_avkastning(
    bd: Bolagsdata, fran: dt.date, till: dt.date
) -> float | None:
    serie = bd.varde("kurshistorik")
    if serie is None or len(serie) == 0:
        return None
    start = serie[serie.index <= pd.Timestamp(fran)]
    slut = serie[serie.index <= pd.Timestamp(till)]
    if len(start) == 0 or len(slut) == 0:
        return None
    a, b = float(start.iloc[-1]), float(slut.iloc[-1])
    if a <= 0 or slut.index[-1] < pd.Timestamp(fran):
        return None
    return b / a - 1


def _rangkorrelation(par: list[tuple[float, float]]) -> float | None:
    """Spearmans rangkorrelation.

    Räknas som Pearson på rangordningarna, vilket är definitionen.
    Pandas inbyggda method="spearman" kräver scipy, och ett beroende
    till för en formel på två rader är inte värt det.
    """
    if len(par) < 5:
        return None
    ram = pd.DataFrame(par, columns=["poang", "avkastning"])
    if ram["poang"].nunique() < 3 or ram["avkastning"].nunique() < 3:
        return None
    varde = ram["poang"].rank().corr(ram["avkastning"].rank())
    return None if pd.isna(varde) else float(varde)


def _slumpa_om(
    kategoripar: list[list[tuple[float, float]]], antal: int
) -> list[float]:
    """IC när poängen kastas om. Ger bruset i exakt samma uppställning."""
    import random

    if not kategoripar or antal <= 0:
        return []
    slumpen = random.Random(len(kategoripar) * 7919)
    ut = []
    for _ in range(antal):
        ics, vikter = [], []
        for par in kategoripar:
            poang = [p for p, _ in par]
            slumpen.shuffle(poang)
            ic = _rangkorrelation(list(zip(poang, (a for _, a in par))))
            if ic is not None:
                ics.append(ic)
                vikter.append(len(par))
        if ics:
            ut.append(sum(i * v for i, v in zip(ics, vikter)) / sum(vikter))
    return ut


def _rangstabilitet(perioder: list[Period], horisont_dagar: int) -> dict[str, float]:
    """Platshållare: stabiliteten mäts separat av mat_rangstabilitet()."""
    return {}


def mat_rangstabilitet(
    inst: Installningar,
    data: dict[str, Bolagsdata],
    forsta: dt.date,
    andra: dt.date,
    fordrojning_manader: int = 3,
) -> dict[str, float]:
    """Hur lika rangordningen är vid två tidpunkter, per kategori.

    Ligger den nära 1 gör modellen samma vad år efter år, och antalet
    verkligt oberoende observationer är mycket mindre än antalet
    mätperioder.
    """
    def poang(datum: dt.date) -> dict[str, tuple[str, float]]:
        historisk = {}
        for ticker, bd in data.items():
            kategori = inst.kategori_for(ticker)
            upps = (
                inst.universum.kategorier[kategori].mattuppsattning
                if kategori else None
            )
            hd = historisk_bolagsdata(bd, datum, fordrojning_manader, upps)
            if hd is not None:
                historisk[ticker] = hd
        tavla = poangsatt(inst, valj_urval(inst, historisk, datum), historisk, datum)
        return {
            b.ticker: (kategori, b.totalpoang)
            for kategori, kp in tavla.kategorier.items() for b in kp.bolag
        }

    a, b = poang(forsta), poang(andra)
    ut = {}
    for kategori in inst.kategorinamn:
        par = [
            (a[t][1], b[t][1]) for t in a
            if t in b and a[t][0] == kategori == b[t][0]
        ]
        korrelation = _rangkorrelation(par)
        if korrelation is not None:
            ut[kategori] = korrelation
    return ut


def mat_information_coefficient(
    inst: Installningar,
    data: dict[str, Bolagsdata],
    start: dt.date,
    slut: dt.date,
    horisont_dagar: int = 365,
    steg_dagar: int = 30,
    fordrojning_manader: int = 3,
    antal_slump: int = 30,
) -> ICresultat:
    """Mäter sambandet mellan poäng och efterföljande avkastning."""
    resultat = ICresultat(horisont_dagar=horisont_dagar)
    sista_matdag = slut - dt.timedelta(days=horisont_dagar)
    if sista_matdag <= start:
        resultat.varningar.append(
            "perioden är kortare än mäthorisonten -- inget går att mäta"
        )
        return resultat

    matdagar = pd.date_range(start, sista_matdag, freq=f"{steg_dagar}D")
    logg.info("mäter IC vid %d tidpunkter", len(matdagar))

    for nr, tidpunkt in enumerate(matdagar, start=1):
        datum = tidpunkt.date()
        slutdatum = datum + dt.timedelta(days=horisont_dagar)
        if nr % 5 == 0:
            logg.info("  %d/%d (%s)", nr, len(matdagar), datum)

        historisk = {}
        for ticker, bd in data.items():
            kategori = inst.kategori_for(ticker)
            upps = (
                inst.universum.kategorier[kategori].mattuppsattning
                if kategori else None
            )
            hd = historisk_bolagsdata(bd, datum, fordrojning_manader, upps)
            if hd is not None:
                historisk[ticker] = hd
        if len(historisk) < 10:
            continue

        tavla = poangsatt(inst, valj_urval(inst, historisk, datum), historisk, datum)

        # Korrelationen räknas inom varje kategori och vägs ihop efter
        # hur många bolag kategorin bidrog med.
        totalt, vikter = [], []
        per_pelare: dict[str, list[tuple[float, float]]] = {p: [] for p in PELARE}
        kategoripar: list[list[tuple[float, float]]] = []
        antal = 0

        for kategori, kp in tavla.kategorier.items():
            par: list[tuple[float, float]] = []
            pelarpar: dict[str, list[tuple[float, float]]] = {p: [] for p in PELARE}
            for bp in kp.bolag:
                avkastning = _framtida_avkastning(data[bp.ticker], datum, slutdatum)
                if avkastning is None:
                    continue
                par.append((bp.totalpoang, avkastning))
                for pelare in PELARE:
                    p = bp.pelare.get(pelare)
                    if p is not None and p.finns:
                        pelarpar[pelare].append((p.poang, avkastning))

            ic = _rangkorrelation(par)
            if ic is not None:
                totalt.append(ic)
                vikter.append(len(par))
                kategoripar.append(par)
                antal += len(par)
            for pelare in PELARE:
                per_pelare[pelare].extend(pelarpar[pelare])

        if not totalt:
            continue

        # Nollfördelningen: samma par, men med poängen omkastad. Det är
        # bruset som allt annat måste mätas mot.
        resultat.noll.extend(_slumpa_om(kategoripar, antal_slump))

        period = Period(datum=datum, slutdatum=slutdatum, antal_bolag=antal)
        period.ic = float(
            sum(i * v for i, v in zip(totalt, vikter)) / sum(vikter)
        )
        for pelare in PELARE:
            period.ic_per_pelare[pelare] = _rangkorrelation(per_pelare[pelare])
        resultat.perioder.append(period)

    resultat.stabilitet = _rangstabilitet(resultat.perioder, horisont_dagar)
    resultat.varningar = [
        f"Överlappande {horisont_dagar}-dagarsfönster är inte oberoende: "
        f"grannperioder delar nästan hela mätfönstret, vilket blåser upp "
        f"t-värdet. Därför redovisas de oberoende perioderna separat.",
        "Fundamenta har ingen publiceringsuppgift; en rapportfördröjning "
        f"på {fordrojning_manader} månader antas.",
        "Poängen som mäts är den rent kvantitativa. Det kvalitativa "
        "lagret går inte att rekonstruera historiskt.",
    ]
    logg.info("IC-mätning klar: %d perioder", len(resultat.perioder))
    return resultat
