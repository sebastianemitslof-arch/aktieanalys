"""Backtest: kör reglerna historiskt, med bara den data som fanns då.

LÄS DET HÄR INNAN DU TOLKAR RESULTATET.

Backtestet är en rimlighetskontroll av reglerna, inte ett bevis för att
de fungerar. Tre skäl, och de är inte små:

1. yfinance levererar inte historiska fundamenta med uppgift om NÄR de
   offentliggjordes. Modulen antar därför en rapportfördröjning: ett
   räkenskapsår blir "känt" först några månader efter bokslutet. Det är
   ett antagande, inte ett faktum.

2. Bara fyra räkenskapsår finns att tillgå. Med kvartalsvisa
   beslutsfönster och tolv månaders minsta innehavstid blir det en
   handfull beslut per kategori. Det är för få för att säga något
   statistiskt.

3. Det kvalitativa lagret går inte att rekonstruera. Historiska nyheter
   går inte att hämta med datum, så backtestet körs helt utan Claude.
   Riskflaggor och undantaget vid fundamental försämring finns alltså
   inte med.

Det backtestet DÄREMOT visar, och som är värt något: att reglerna går
att köra, hur ofta de utlöser byten, och vad omsättningen kostar.

Kursdata är äkta historisk. Det arkiv du bygger upp från och med nu blir
med tiden den tillförlitliga källan -- om några år går det här att göra
på riktigt.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

import pandas as pd

from aktieanalys.byten import bedom_byten, berakna_kostnad, spara_veckans_poang
from aktieanalys.data.bolagsdata import FALT, Bolagsdata
from aktieanalys.data.harledda import Harledare
from aktieanalys.data.lager import Lager
from aktieanalys.konfig import Installningar
from aktieanalys.logg import hamta
from aktieanalys.poang.modell import poangsatt
from aktieanalys.universum import valj_urval

logg = hamta(__name__)

#: Fält vars värde ska tas ur motsvarande årsserie i stället för ur
#: dagens ögonblicksbild. Utan den här tabellen skulle backtestet
#: använda dagens nettoskuld på 2023 års poäng.
UR_HISTORIK = {
    "rorelseresultat": "rorelseresultat_historik",
    "nettoresultat": "nettoresultat_historik",
    "omsattning": "omsattning_historik",
    "vinst_per_aktie": "vinst_per_aktie_historik",
    "ebitda": "ebitda_historik",
    "rantekostnad": "rantekostnad_historik",
    "nettoskuld": "nettoskuld_historik",
    "eget_kapital": "eget_kapital_historik",
    "investerat_kapital": "investerat_kapital_historik",
    "totala_tillgangar": "totala_tillgangar_historik",
    "rantebarande_skulder": "rantebarande_skulder_historik",
    "antal_aktier": "antal_aktier_historik",
    "substansvarde_per_aktie": "substansvarde_per_aktie_historik",
    "forvaltningsresultat_per_aktie": "forvaltningsresultat_per_aktie_historik",
}

#: Fält som INTE går att rekonstruera historiskt, och som därför tas
#: från dagens data. Varje sådant fält är en känd förorening.
EJ_REKONSTRUERBARA: dict[str, str] = {
    # Tom så länge volymhistoriken finns. Saknas den för ett bolag
    # utesluts bolaget i stället för att få dagens omsättning påklistrad
    # -- se _daglig_omsattning nedan.
}

#: Fält som medvetet INTE fylls i historiskt, och konsekvensen av det.
MEDVETET_UTELAMNADE = {
    "cet1_kvot": (
        "bankernas kapitaltäckning är inskriven för innevarande år och "
        "saknar historik. Den utelämnas hellre än används med dagens "
        "siffra: bankerna bedöms då på 85 procent av poängvikten, utan "
        "pelaren finansiell styrka"
    ),
}


@dataclass
class Affar:
    datum: dt.date
    kategori: str
    typ: str
    ticker: str
    antal: float
    kurs: float
    belopp: float
    kostnad: float


@dataclass
class Innehavspost:
    ticker: str
    antal: float
    kopdatum: dt.date
    anskaffning: float


@dataclass
class Slumpjamforelse:
    """Var resultatet hamnar bland portföljer valda på måfå.

    Det här är det enda som gör ett backtest med sex innehav och ett
    par år tolkbart. Ligger resultatet mitt i fördelningen är det inte
    skilt från tur, hur bra avkastningen än ser ut.
    """

    antal: int
    median: float
    percentil_5: float
    percentil_95: float
    percentil_for_resultatet: float
    andel_som_slog_resultatet: float
    likaviktad_korg: float | None = None

    @property
    def skild_fran_tur(self) -> bool:
        """Konventionell gräns: resultatet ska ligga över percentil 95."""
        return self.percentil_for_resultatet >= 95.0

    def omdome(self) -> str:
        if self.skild_fran_tur:
            return (
                f"Resultatet ligger på percentil "
                f"{self.percentil_for_resultatet:.0f} -- över den gräns där "
                f"det börjar gå att skilja från tur. Det är fortfarande EN "
                f"period och sex innehav."
            )
        return (
            f"Resultatet ligger på percentil "
            f"{self.percentil_for_resultatet:.0f}. "
            f"{self.andel_som_slog_resultatet:.0f} procent av rena slumpval "
            f"gjorde bättre ifrån sig. Det går alltså INTE att skilja från "
            f"tur."
        )


@dataclass
class Backtestresultat:
    start: dt.date
    slut: dt.date
    startkapital: float
    varden: pd.Series = field(default_factory=lambda: pd.Series(dtype=float))
    index: dict[str, pd.Series] = field(default_factory=dict)
    affarer: list[Affar] = field(default_factory=list)
    varningar: list[str] = field(default_factory=list)
    rapportfordrojning: int = 3
    slump: Slumpjamforelse | None = None

    @property
    def slutvarde(self) -> float:
        return float(self.varden.iloc[-1]) if len(self.varden) else self.startkapital

    @property
    def avkastning(self) -> float:
        return (self.slutvarde / self.startkapital - 1) * 100

    @property
    def ar(self) -> float:
        return max((self.slut - self.start).days / 365.25, 0.01)

    @property
    def arsavkastning(self) -> float:
        if self.slutvarde <= 0:
            return -100.0
        return ((self.slutvarde / self.startkapital) ** (1 / self.ar) - 1) * 100

    def indexavkastning(self, namn: str) -> float | None:
        serie = self.index.get(namn)
        if serie is None or len(serie) < 2:
            return None
        return (float(serie.iloc[-1]) / float(serie.iloc[0]) - 1) * 100

    def total_kostnad(self) -> float:
        return sum(a.kostnad for a in self.affarer)

    def per_ar(self) -> dict[int, dict[str, float]]:
        """Antal byten och omsättning per kalenderår."""
        ut: dict[int, dict[str, float]] = {}
        for ar in range(self.start.year, self.slut.year + 1):
            affarer = [a for a in self.affarer if a.datum.year == ar]
            sald = sum(a.belopp for a in affarer if a.typ == "salj")
            i_ar = self.varden[self.varden.index.year == ar] if len(self.varden) else []
            medelvarde = float(i_ar.mean()) if len(i_ar) else self.startkapital
            ut[ar] = {
                "byten": sum(1 for a in affarer if a.typ == "salj"),
                "kop": sum(1 for a in affarer if a.typ == "kop"),
                "omsattning_procent": sald / medelvarde * 100 if medelvarde else 0.0,
                "kostnad": sum(a.kostnad for a in affarer),
            }
        return ut


# =====================================================================
#  Rekonstruktion: vad visste vi då?
# =====================================================================
def ar_kant(ar: int, datum: dt.date, fordrojning_manader: int) -> bool:
    """Var räkenskapsåret offentliggjort vid datumet?

    Antagande: ett bokslut för år Y publiceras fordrojning_manader efter
    årsskiftet. Med tre månader blir 2023 känt från 1 april 2024.
    Svenska bolag rapporterar i praktiken i februari, så tre månader är
    försiktigt -- vilket är rätt håll att fela åt.
    """
    manad = 1 + fordrojning_manader
    ar_publicering = ar + 1 + (manad - 1) // 12
    manad = (manad - 1) % 12 + 1
    return datum >= dt.date(ar_publicering, manad, 1)


def historisk_bolagsdata(
    bd: Bolagsdata,
    datum: dt.date,
    fordrojning_manader: int,
    mattuppsattning: str | None = None,
) -> Bolagsdata | None:
    """Bygger ett Bolagsdata som det såg ut vid ett tidigare datum.

    Returnerar None om bolaget inte hade tillräckligt med data då.
    """
    kurser = bd.varde("kurshistorik")
    if kurser is None or len(kurser) == 0:
        return None
    fram_till = kurser[kurser.index <= pd.Timestamp(datum)]
    if len(fram_till) < 30:
        return None

    ny = Bolagsdata(
        ticker=bd.ticker, hamtad=datum, namn=bd.namn,
        valuta=bd.valuta, rapportvaluta=bd.rapportvaluta,
    )
    ny.satt("kurshistorik", fram_till, "backtest: kapad vid datumet")
    kurs = float(fram_till.iloc[-1])
    ny.satt("kurs", kurs, "backtest: kurs vid datumet")

    # --- årsserier, kapade till det som var känt då ------------------
    kanda: dict[str, list[tuple[int, float]]] = {}
    for falt, definition in FALT.items():
        if definition.typ != "arslista" or not bd.har(falt):
            continue
        serie = [
            (ar, v) for ar, v in bd.varde(falt)
            if ar_kant(ar, datum, fordrojning_manader)
        ]
        if serie:
            kanda[falt] = serie
            ny.satt(falt, serie, f"backtest: {len(serie)} år kända {datum}")
        else:
            ny.markera_saknat(
                falt, f"inget räkenskapsår var offentliggjort {datum}"
            )

    # --- enskilda tal ur senaste kända året --------------------------
    for falt, historikfalt in UR_HISTORIK.items():
        serie = kanda.get(historikfalt)
        if serie:
            ny.satt(falt, serie[0][1], f"backtest: {serie[0][0]} års bokslut")
        else:
            ny.markera_saknat(falt, "inget känt räkenskapsår att hämta ur")

    # --- härledda tal ------------------------------------------------
    aktier = ny.varde("antal_aktier")
    if aktier:
        ny.satt("borsvarde", kurs * aktier, "backtest: kurs x antal aktier")
        if ny.har("nettoskuld"):
            ny.satt("bolagsvarde", kurs * aktier + ny.varde("nettoskuld"),
                    "backtest: börsvärde + nettoskuld")
        if ny.har("eget_kapital"):
            ny.satt("eget_kapital_per_aktie", ny.varde("eget_kapital") / aktier,
                    "backtest: eget kapital / antal aktier")

    # --- likviditet ur den historiska volymen -----------------------
    oms = _daglig_omsattning(bd, datum, fram_till)
    if oms is not None:
        ny.satt("daglig_omsattning", oms, "backtest: kurs x volym vid datumet")
    else:
        ny.markera_saknat(
            "daglig_omsattning",
            "ingen volymhistorik vid den tidpunkten -- bolaget utesluts "
            "hellre än att få dagens omsättning påklistrad",
        )

    # --- fält som inte går att rekonstruera --------------------------
    for falt in EJ_REKONSTRUERBARA:
        if not ny.har(falt) and bd.har(falt):
            ny.satt(falt, bd.varde(falt), f"backtest: DAGENS värde ({falt})")

    if mattuppsattning:
        Harledare().harled(ny, mattuppsattning)
    return ny


# =====================================================================
#  Simuleringen
# =====================================================================
def _daglig_omsattning(
    bd: Bolagsdata, datum: dt.date, kurser: pd.Series, dagar: int = 60
) -> float | None:
    """Genomsnittlig omsättning i kronor per dag fram till datumet."""
    volymer = bd.varde("volymhistorik")
    if volymer is None or len(volymer) == 0:
        return None
    fram_till = volymer[volymer.index <= pd.Timestamp(datum)].tail(dagar)
    if len(fram_till) < 10:
        return None
    gemensamma = kurser.reindex(fram_till.index).dropna()
    if len(gemensamma) < 10:
        return None
    belopp = (gemensamma * fram_till.reindex(gemensamma.index)).dropna()
    return float(belopp.mean()) if len(belopp) else None


def _kurs_vid(bd: Bolagsdata, datum: dt.date) -> float | None:
    serie = bd.varde("kurshistorik")
    if serie is None:
        return None
    fram_till = serie[serie.index <= pd.Timestamp(datum)]
    return float(fram_till.iloc[-1]) if len(fram_till) else None


def hamta_index(
    tickers: dict[str, str], start: dt.date, slut: dt.date, lager: Lager
) -> dict[str, pd.Series]:
    """Hämtar jämförelseindex, och cachar dem i kurstabellen."""
    from aktieanalys.data.valuta import naiva_datum

    ut: dict[str, pd.Series] = {}
    for namn, ticker in tickers.items():
        serie = lager.las_kurser(ticker)
        if len(serie) < 100:
            try:
                import logging

                import yfinance as yf

                logging.getLogger("yfinance").setLevel(logging.CRITICAL)
                df = yf.Ticker(ticker).history(period="10y", auto_adjust=True)
                if df is not None and not df.empty:
                    serie = naiva_datum(df["Close"].dropna())
                    lager.spara_kurser(ticker, serie)
                    logg.info("hämtade %d dagar för %s", len(serie), ticker)
            except Exception as e:
                logg.warning("kunde inte hämta %s: %s", ticker, e)
                continue
        if len(serie):
            kapad = serie[(serie.index >= pd.Timestamp(start))
                          & (serie.index <= pd.Timestamp(slut))]
            if len(kapad) > 1:
                ut[namn] = kapad
    return ut


def kor_backtest(
    inst: Installningar,
    data: dict[str, Bolagsdata],
    lager: Lager,
    start: dt.date,
    slut: dt.date,
    startkapital: float = 300_000.0,
    fordrojning_manader: int = 3,
    jamforelseindex: dict[str, str] | None = None,
) -> Backtestresultat:
    """Kör reglerna vecka för vecka genom historien."""
    import tempfile

    resultat = Backtestresultat(
        start=start, slut=slut, startkapital=startkapital,
        rapportfordrojning=fordrojning_manader,
    )
    resultat.varningar = [
        "Fundamenta saknar publiceringsdatum. En rapportfördröjning på "
        f"{fordrojning_manader} månader ANTAS.",
        "Det kvalitativa lagret kördes inte -- historiska nyheter går inte "
        "att hämta med datum. Riskflaggor och undantaget vid fundamental "
        "försämring saknas därför helt.",
        "Bara fyra räkenskapsår finns. Antalet beslut är för litet för att "
        "säga något statistiskt.",
    ] + [f"{falt}: {orsak}" for falt, orsak in MEDVETET_UTELAMNADE.items()] \
      + [f"{falt}: {orsak}" for falt, orsak in EJ_REKONSTRUERBARA.items()
         if any(bd.har(falt) for bd in data.values())]

    kategorier = inst.kategorinamn
    per_kategori = startkapital / len(kategorier)
    innehav: dict[str, Innehavspost | None] = {k: None for k in kategorier}
    kassa: dict[str, float] = {k: per_kategori for k in kategorier}

    varden: dict[pd.Timestamp, float] = {}
    veckor = pd.date_range(start, slut, freq="W-FRI")
    logg.info("backtest över %d veckor, %d kategorier", len(veckor), len(kategorier))

    with tempfile.TemporaryDirectory() as tmp:
        simlager = Lager(f"{tmp}/sim.sqlite")
        try:
            for n, tidpunkt in enumerate(veckor, start=1):
                datum = tidpunkt.date()
                if n % 20 == 0:
                    logg.info("  vecka %d/%d (%s)", n, len(veckor), datum)

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
                if not historisk:
                    continue

                # Portföljen som den ser ut just nu, så att reglerna
                # bedömer rätt innehav
                _satt_innehav(inst, innehav)

                urval = valj_urval(inst, historisk, datum)
                tavla = poangsatt(inst, urval, historisk, datum)
                spara_veckans_poang(simlager, tavla)
                beslut = bedom_byten(inst, tavla, historisk, simlager, None, datum)

                for kategori, kb in beslut.kategorier.items():
                    if not kb.foreslas or not kb.utmanare:
                        continue
                    _genomfor(
                        inst, kategori, kb, historisk, innehav, kassa,
                        datum, resultat,
                    )

                varden[tidpunkt] = _vardera(innehav, kassa, historisk, datum)
        finally:
            simlager.stang()
            _satt_innehav(inst, {k: None for k in kategorier})

    resultat.varden = pd.Series(varden).sort_index()
    resultat.slut = resultat.varden.index[-1].date() if len(resultat.varden) else slut
    if jamforelseindex:
        resultat.index = hamta_index(jamforelseindex, start, resultat.slut, lager)
    resultat.slump = jamfor_med_slumpen(
        inst, data, start, resultat.slut, resultat.avkastning
    )
    logg.info(
        "backtest klart: %.1f %% totalt, %d affärer",
        resultat.avkastning, len(resultat.affarer),
    )
    return resultat


def jamfor_med_slumpen(
    inst: Installningar,
    data: dict[str, Bolagsdata],
    start: dt.date,
    slut: dt.date,
    resultat_procent: float,
    antal: int = 10_000,
    fro: int = 42,
) -> Slumpjamforelse | None:
    """Drar portföljer på måfå och ser var resultatet hamnar.

    En portfölj med sex innehav över ett par år har enorm spridning.
    Utan den här jämförelsen är avkastningssiffran oläsbar: den säger
    ingenting om reglerna är bra, bara vad som råkade hända.
    """
    import numpy as np

    per_kategori: list[np.ndarray] = []
    alla: list[float] = []
    for kategori, kat in inst.universum.kategorier.items():
        faktorer = []
        for ticker in kat.bolag:
            bd = data.get(ticker)
            serie = bd.varde("kurshistorik") if bd else None
            if serie is None:
                continue
            kapad = serie[(serie.index >= pd.Timestamp(start))
                          & (serie.index <= pd.Timestamp(slut))]
            if len(kapad) > 100 and float(kapad.iloc[0]) > 0:
                faktorer.append(float(kapad.iloc[-1] / kapad.iloc[0]))
        if faktorer:
            per_kategori.append(np.array(faktorer))
            alla.extend(faktorer)

    if not per_kategori:
        return None

    slumpen = np.random.default_rng(fro)
    utfall = np.array([
        np.mean([slumpen.choice(v) for v in per_kategori]) for _ in range(antal)
    ])
    utfall = (utfall - 1) * 100

    return Slumpjamforelse(
        antal=antal,
        median=float(np.median(utfall)),
        percentil_5=float(np.percentile(utfall, 5)),
        percentil_95=float(np.percentile(utfall, 95)),
        percentil_for_resultatet=float((utfall < resultat_procent).mean() * 100),
        andel_som_slog_resultatet=float((utfall > resultat_procent).mean() * 100),
        likaviktad_korg=(float(np.mean(alla)) - 1) * 100 if alla else None,
    )


def _satt_innehav(inst: Installningar, innehav: dict[str, Innehavspost | None]) -> None:
    """Skriver den simulerade portföljen till inställningarna.

    Bytesreglerna läser innehavet ur inst.portfolj, så simuleringen
    måste peka om det inför varje vecka.
    """
    from aktieanalys.konfig import Innehav

    nytt = {}
    for kategori in inst.kategorinamn:
        post = innehav.get(kategori)
        nytt[kategori] = (
            Innehav(ticker=post.ticker, kopdatum=post.kopdatum, antal=post.antal)
            if post else Innehav()
        )
    object.__setattr__(inst.portfolj, "innehav", nytt)


def _genomfor(
    inst: Installningar,
    kategori: str,
    kb,
    historisk: dict[str, Bolagsdata],
    innehav: dict[str, Innehavspost | None],
    kassa: dict[str, float],
    datum: dt.date,
    resultat: Backtestresultat,
) -> None:
    """Säljer det gamla och köper det nya, med kostnader."""
    c = inst.config.kostnader
    gammal = innehav.get(kategori)
    ny_ticker = kb.utmanare
    ny_kurs = _kurs_vid(historisk[ny_ticker], datum) if ny_ticker in historisk else None
    if not ny_kurs or ny_kurs <= 0:
        return

    if gammal is not None:
        gammal_kurs = _kurs_vid(historisk.get(gammal.ticker), datum) if \
            gammal.ticker in historisk else None
        if not gammal_kurs:
            return
        belopp = gammal.antal * gammal_kurs
        kostnad = max(c.courtage_min_kr, belopp * c.courtage_procent / 100)
        kostnad += belopp * c.spread_golv_procent / 100 / 2
        kassa[kategori] += belopp - kostnad
        resultat.affarer.append(Affar(
            datum, kategori, "salj", gammal.ticker, gammal.antal,
            gammal_kurs, belopp, kostnad,
        ))

    belopp = kassa[kategori]
    if belopp <= 0:
        return
    kostnad = max(c.courtage_min_kr, belopp * c.courtage_procent / 100)
    kostnad += belopp * c.spread_golv_procent / 100 / 2
    att_kopa = belopp - kostnad
    antal = att_kopa / ny_kurs

    kassa[kategori] = 0.0
    innehav[kategori] = Innehavspost(ny_ticker, antal, datum, att_kopa)
    resultat.affarer.append(Affar(
        datum, kategori, "kop", ny_ticker, antal, ny_kurs, att_kopa, kostnad,
    ))


def _vardera(
    innehav: dict[str, Innehavspost | None],
    kassa: dict[str, float],
    historisk: dict[str, Bolagsdata],
    datum: dt.date,
) -> float:
    summa = sum(kassa.values())
    for kategori, post in innehav.items():
        if post is None:
            continue
        kurs = _kurs_vid(historisk.get(post.ticker), datum)
        summa += post.antal * kurs if kurs else post.anskaffning
    return summa
