"""Läser och validerar de tre konfigurationsfilerna.

Hela poängen med den här modulen är att fel i YAML-filerna ska upptäckas
HÄR, med ett felmeddelande på svenska som pekar på rätt fil och rad-nyckel,
i stället för att ge en felaktig analys längre fram.

Används så:

    from aktieanalys.konfig import las_installningar
    inst = las_installningar()          # läser katalogen config/
    print(inst.config.poang.vikter.kvalitet)
"""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

from aktieanalys.poang.matt import MATT, Pelare, okand_matt

VIKTTOLERANS = 0.001
"""Hur mycket en viktsumma får avvika från 1.0 (avrundning i decimaltal)."""


class KonfigFel(Exception):
    """Fel i en konfigurationsfil. Meddelandet är skrivet för att läsas."""


# =====================================================================
#  Grundklass: okända nycklar är alltid fel
# =====================================================================
class _Bas(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


# =====================================================================
#  config.yaml
# =====================================================================
class Universuminstallningar(_Bas):
    min_borsvarde_mdr: float = Field(gt=0)
    min_daglig_omsattning_mkr: float = Field(gt=0)
    handelsdagar_for_omsattning: int = Field(ge=5, le=500)
    max_saknade_matt_per_bolag: int = Field(ge=0)


class Vikter(_Bas):
    kvalitet: float = Field(ge=0, le=1)
    vardering: float = Field(ge=0, le=1)
    finansiell_styrka: float = Field(ge=0, le=1)
    momentum: float = Field(ge=0, le=1)

    def som_dict(self) -> dict[str, float]:
        return {
            Pelare.KVALITET.value: self.kvalitet,
            Pelare.VARDERING.value: self.vardering,
            Pelare.FINANSIELL_STYRKA.value: self.finansiell_styrka,
            Pelare.MOMENTUM.value: self.momentum,
        }


class Mattuppsattning(_Bas):
    """Vilka mått en kategori bedöms på, och hur de väger inom varje pelare."""

    kvalitet: dict[str, float]
    vardering: dict[str, float]
    finansiell_styrka: dict[str, float]
    momentum: dict[str, float]

    def som_dict(self) -> dict[str, dict[str, float]]:
        return {
            Pelare.KVALITET.value: self.kvalitet,
            Pelare.VARDERING.value: self.vardering,
            Pelare.FINANSIELL_STYRKA.value: self.finansiell_styrka,
            Pelare.MOMENTUM.value: self.momentum,
        }

    def alla_matt(self) -> list[str]:
        return [m for pelare in self.som_dict().values() for m in pelare]


class Poanginstallningar(_Bas):
    vikter: Vikter
    antal_ar_historik: int = Field(ge=2, le=20)
    min_ar_historik: int = Field(ge=2, le=20)
    min_bolag_for_rank: int = Field(ge=2)
    min_andel_poangvikt: float = Field(default=0.6, gt=0, le=1)
    mattuppsattningar: dict[str, Mattuppsattning]

    @model_validator(mode="after")
    def _historikordning(self) -> Poanginstallningar:
        if self.min_ar_historik > self.antal_ar_historik:
            raise ValueError(
                f"min_ar_historik ({self.min_ar_historik}) kan inte vara större "
                f"än antal_ar_historik ({self.antal_ar_historik})"
            )
        return self

    @field_validator("mattuppsattningar")
    @classmethod
    def _minst_en(cls, v: dict[str, Mattuppsattning]) -> dict[str, Mattuppsattning]:
        if not v:
            raise ValueError("minst en mattuppsattning måste finnas")
        return v


class Kvalitativtinstallningar(_Bas):
    aktiverat: bool
    modell: str = Field(min_length=1)
    max_justering: float = Field(gt=0, le=0.5)
    antal_utmanare: int = Field(ge=1, le=10)
    max_nyheter_per_bolag: int = Field(ge=0, le=50)


class Beslutsfonster(_Bas):
    fran: str
    till: str

    @field_validator("fran", "till")
    @classmethod
    def _format(cls, v: str) -> str:
        if not re.fullmatch(r"\d{2}-\d{2}", v):
            raise ValueError(f"datumet {v!r} måste skrivas som MM-DD, t.ex. \"02-15\"")
        manad, dag = int(v[:2]), int(v[3:])
        try:
            dt.date(2024, manad, dag)  # skottår: tillåter 02-29
        except ValueError:
            raise ValueError(f"{v!r} är inget giltigt datum") from None
        return v

    def innehaller(self, datum: dt.date) -> bool:
        return self.fran <= datum.strftime("%m-%d") <= self.till


class Bytesinstallningar(_Bas):
    min_poangforbattring_procent: float = Field(ge=0, le=500)
    antal_veckor_i_rad: int = Field(ge=1, le=52)
    min_innehavstid_manader: int = Field(ge=0, le=120)
    tillat_undantag_vid_riskflagga: bool
    endast_byten_i_beslutsfonster: bool
    beslutsfonster: list[Beslutsfonster]

    @field_validator("beslutsfonster")
    @classmethod
    def _ordning(cls, v: list[Beslutsfonster]) -> list[Beslutsfonster]:
        for f in v:
            if f.fran > f.till:
                raise ValueError(
                    f"beslutsfönstret {f.fran}--{f.till} slutar före det börjar. "
                    "Ett fönster kan inte sträcka sig över ett årsskifte -- "
                    "dela upp det i två fönster."
                )
        return v


class Kostnadsinstallningar(_Bas):
    courtage_procent: float = Field(ge=0, le=5)
    courtage_min_kr: float = Field(ge=0)
    spread_golv_procent: float = Field(ge=0, le=5)
    isk: bool


class Mejlinstallningar(_Bas):
    till: str = ""
    fran: str = ""
    smtp_server: str = ""
    smtp_port: int = Field(default=587, ge=1, le=65535)

    @field_validator("till", "fran")
    @classmethod
    def _adress(cls, v: str) -> str:
        if v and "@" not in v:
            raise ValueError(f"{v!r} ser inte ut som en mejladress")
        return v


class Rapportinstallningar(_Bas):
    spara_fil: bool
    katalog: str = Field(min_length=1)
    skicka_mejl: bool
    mejl: Mejlinstallningar


class Datainstallningar(_Bas):
    databas: str = Field(min_length=1)
    manuell_katalog: str = Field(min_length=1)
    cache_dagar: int = Field(ge=0, le=365)
    max_forsok: int = Field(ge=1, le=10)
    paus_mellan_forsok_sek: float = Field(ge=0, le=60)
    paus_mellan_bolag_sek: float = Field(default=0.7, ge=0, le=60)


class Logginstallningar(_Bas):
    niva: Literal["DEBUG", "INFO", "WARNING", "ERROR"]
    fil: str = Field(min_length=1)


class Config(_Bas):
    universum: Universuminstallningar
    poang: Poanginstallningar
    kvalitativt: Kvalitativtinstallningar
    byten: Bytesinstallningar
    kostnader: Kostnadsinstallningar
    rapport: Rapportinstallningar
    data: Datainstallningar
    loggning: Logginstallningar


# =====================================================================
#  universum.yaml
# =====================================================================
class Kategori(_Bas):
    mattuppsattning: str = Field(min_length=1)
    bolag: list[str]

    @field_validator("bolag")
    @classmethod
    def _tickers(cls, v: list[str]) -> list[str]:
        if not v:
            raise ValueError("kategorin måste innehålla minst ett bolag")
        for t in v:
            if not t or t != t.strip():
                raise ValueError(f"tickern {t!r} har blanksteg i början eller slutet")
            if not t.endswith(".ST"):
                raise ValueError(
                    f"tickern {t!r} saknar ändelsen .ST. Universumet är Nasdaq "
                    "Stockholm, och svenska aktier skrivs t.ex. VOLV-B.ST"
                )
        dubbletter = sorted({t for t in v if v.count(t) > 1})
        if dubbletter:
            raise ValueError(f"samma ticker står flera gånger: {', '.join(dubbletter)}")
        return v


class Universumfil(_Bas):
    kategorier: dict[str, Kategori]

    @field_validator("kategorier")
    @classmethod
    def _minst_en(cls, v: dict[str, Kategori]) -> dict[str, Kategori]:
        if not v:
            raise ValueError("minst en kategori måste finnas")
        return v


# =====================================================================
#  portfolj.yaml
# =====================================================================
class Innehav(_Bas):
    ticker: str | None = None
    kopdatum: dt.date | None = None
    antal: float = Field(default=0, ge=0)
    anskaffningskurs: float = Field(default=0, ge=0)

    @field_validator("ticker", mode="before")
    @classmethod
    def _tom_blir_none(cls, v: Any) -> Any:
        if isinstance(v, str) and not v.strip():
            return None
        return v.strip() if isinstance(v, str) else v

    @field_validator("kopdatum", mode="before")
    @classmethod
    def _tomt_datum_blir_none(cls, v: Any) -> Any:
        if isinstance(v, str) and not v.strip():
            return None
        return v

    @property
    def finns(self) -> bool:
        return self.ticker is not None

    def innehavstid_manader(self, idag: dt.date | None = None) -> float | None:
        """Hur länge innehavet ägts, i månader. None om inget innehav."""
        if self.kopdatum is None:
            return None
        idag = idag or dt.date.today()
        return (idag - self.kopdatum).days / 30.44


class Portfoljfil(_Bas):
    innehav: dict[str, Innehav]


# =====================================================================
#  Allt tillsammans
# =====================================================================
@dataclass(frozen=True)
class Installningar:
    config: Config
    universum: Universumfil
    portfolj: Portfoljfil
    rotkatalog: Path

    # -- bekväma sökvägar, alltid absoluta --------------------------------
    @property
    def databasvag(self) -> Path:
        return self.rotkatalog / self.config.data.databas

    @property
    def manuell_katalog(self) -> Path:
        return self.rotkatalog / self.config.data.manuell_katalog

    @property
    def rapportkatalog(self) -> Path:
        return self.rotkatalog / self.config.rapport.katalog

    @property
    def loggfil(self) -> Path:
        return self.rotkatalog / self.config.loggning.fil

    # -- uppslag ---------------------------------------------------------
    @property
    def kategorinamn(self) -> list[str]:
        return list(self.universum.kategorier)

    def alla_tickers(self) -> list[str]:
        return [t for k in self.universum.kategorier.values() for t in k.bolag]

    def kategori_for(self, ticker: str) -> str | None:
        for namn, kat in self.universum.kategorier.items():
            if ticker in kat.bolag:
                return namn
        return None

    def mattuppsattning_for(self, kategori: str) -> Mattuppsattning:
        namn = self.universum.kategorier[kategori].mattuppsattning
        return self.config.poang.mattuppsattningar[namn]

    def innehav_i(self, kategori: str) -> Innehav:
        return self.portfolj.innehav.get(kategori, Innehav())

    def i_beslutsfonster(self, datum: dt.date | None = None) -> bool:
        datum = datum or dt.date.today()
        return any(f.innehaller(datum) for f in self.config.byten.beslutsfonster)


# =====================================================================
#  Inläsning
# =====================================================================
def _las_yaml(vag: Path) -> dict[str, Any]:
    if not vag.exists():
        raise KonfigFel(
            f"Hittar inte konfigurationsfilen {vag}.\n"
            "Kontrollera att du står i projektmappen när du kör kommandot."
        )
    try:
        innehall = yaml.safe_load(vag.read_text(encoding="utf-8"))
    except yaml.YAMLError as e:
        raise KonfigFel(
            f"{vag.name} går inte att läsa som YAML. Vanligaste orsaken är "
            f"fel indrag eller en tabb i stället för mellanslag.\n\n{e}"
        ) from None
    if innehall is None:
        raise KonfigFel(f"{vag.name} är tom.")
    if not isinstance(innehall, dict):
        raise KonfigFel(f"{vag.name} måste börja med inställningar, inte en lista.")
    return innehall


_FELTEXTER = {
    "extra_forbidden": "okänd inställning -- kontrollera stavningen",
    "missing": "inställningen saknas och måste fyllas i",
    "float_parsing": "måste vara ett tal (decimaltal skrivs med punkt, t.ex. 0.25)",
    "int_parsing": "måste vara ett heltal",
    "bool_parsing": 'måste vara true eller false',
    "bool_type": 'måste vara true eller false',
    "string_type": "måste vara text",
    "date_from_datetime_parsing": "måste vara ett datum som 2024-03-15",
    "date_parsing": "måste vara ett datum som 2024-03-15",
    "date_type": "måste vara ett datum som 2024-03-15",
}


def _formatera(fil: str, fel: ValidationError) -> str:
    rader = []
    for f in fel.errors():
        nyckel = ".".join(str(d) for d in f["loc"]) or "(filens rot)"
        text = _FELTEXTER.get(f["type"])
        if text is None:
            text = f["msg"]
            for prefix in ("Value error, ", "Assertion failed, "):
                if text.startswith(prefix):
                    text = text[len(prefix) :]
        rader.append(f"  * {nyckel}: {text}")
    return f"Fel i {fil}:\n" + "\n".join(rader)


def _validera(modell: type[BaseModel], data: dict[str, Any], fil: str) -> Any:
    try:
        return modell.model_validate(data)
    except ValidationError as e:
        raise KonfigFel(_formatera(fil, e)) from None


def _kontrollera_viktsummor(config: Config) -> list[str]:
    fel: list[str] = []

    summa = sum(config.poang.vikter.som_dict().values())
    if abs(summa - 1.0) > VIKTTOLERANS:
        fel.append(
            f"  * poang.vikter summerar till {summa:.4g}, inte 1.0. "
            f"Justera vikterna så att de tillsammans blir 1.0."
        )

    for namn, upps in config.poang.mattuppsattningar.items():
        for pelare, matt in upps.som_dict().items():
            if not matt:
                fel.append(
                    f"  * poang.mattuppsattningar.{namn}.{pelare} är tom. "
                    f"Varje pelare behöver minst ett mått (sätt vikten för "
                    f"pelaren till 0 i poang.vikter om du vill stänga av den)."
                )
                continue
            s = sum(matt.values())
            if abs(s - 1.0) > VIKTTOLERANS:
                fel.append(
                    f"  * poang.mattuppsattningar.{namn}.{pelare} summerar till "
                    f"{s:.4g}, inte 1.0."
                )
    return fel


def _kontrollera_mattnamn(config: Config) -> list[str]:
    fel: list[str] = []
    for namn, upps in config.poang.mattuppsattningar.items():
        for pelare, matt in upps.som_dict().items():
            for mattnamn, vikt in matt.items():
                plats = f"poang.mattuppsattningar.{namn}.{pelare}.{mattnamn}"
                if mattnamn not in MATT:
                    fel.append(f"  * {plats}: {okand_matt(mattnamn)}")
                    continue
                if MATT[mattnamn].pelare.value != pelare:
                    fel.append(
                        f"  * {plats}: måttet {mattnamn!r} hör till pelaren "
                        f"{MATT[mattnamn].pelare.value!r}, inte {pelare!r}. "
                        f"Flytta raden dit."
                    )
                if not 0 <= vikt <= 1:
                    fel.append(f"  * {plats}: vikten måste ligga mellan 0 och 1")
    return fel


def _kontrollera_universum(config: Config, universum: Universumfil) -> list[str]:
    fel: list[str] = []
    kanda = set(config.poang.mattuppsattningar)

    for namn, kat in universum.kategorier.items():
        if kat.mattuppsattning not in kanda:
            fel.append(
                f"  * kategorier.{namn}.mattuppsattning: {kat.mattuppsattning!r} "
                f"finns inte i config.yaml. Välj en av: {', '.join(sorted(kanda))}"
            )

    sett: dict[str, str] = {}
    for namn, kat in universum.kategorier.items():
        for t in kat.bolag:
            if t in sett:
                fel.append(
                    f"  * tickern {t} står i både {sett[t]!r} och {namn!r}. "
                    f"Ett bolag får bara höra till en kategori."
                )
            else:
                sett[t] = namn
    return fel


def _kontrollera_portfolj(
    universum: Universumfil, portfolj: Portfoljfil
) -> list[str]:
    fel: list[str] = []
    for kategori, innehav in portfolj.innehav.items():
        if kategori not in universum.kategorier:
            fel.append(
                f"  * innehav.{kategori}: kategorin finns inte i universum.yaml. "
                f"Välj en av: {', '.join(universum.kategorier)}"
            )
            continue
        if not innehav.finns:
            continue
        if innehav.ticker not in universum.kategorier[kategori].bolag:
            fel.append(
                f"  * innehav.{kategori}.ticker: {innehav.ticker!r} finns inte i "
                f"kategorin {kategori!r} i universum.yaml. Lägg till bolaget där, "
                f"eller flytta innehavet till rätt kategori."
            )
        if innehav.kopdatum is None:
            fel.append(
                f"  * innehav.{kategori}.kopdatum: måste fyllas i när det finns "
                f"ett innehav -- regeln om minsta innehavstid bygger på det."
            )
        elif innehav.kopdatum > dt.date.today():
            fel.append(
                f"  * innehav.{kategori}.kopdatum: {innehav.kopdatum} ligger i "
                f"framtiden."
            )
    return fel


def las_installningar(katalog: str | Path = "config") -> Installningar:
    """Läser och validerar config/, och returnerar allt i ett objekt.

    Höjer KonfigFel med ett läsbart svenskt meddelande om något är fel.
    """
    katalog = Path(katalog)
    rot = katalog.resolve().parent

    config: Config = _validera(
        Config, _las_yaml(katalog / "config.yaml"), "config/config.yaml"
    )
    universum: Universumfil = _validera(
        Universumfil, _las_yaml(katalog / "universum.yaml"), "config/universum.yaml"
    )
    portfolj: Portfoljfil = _validera(
        Portfoljfil, _las_yaml(katalog / "portfolj.yaml"), "config/portfolj.yaml"
    )

    fel = _kontrollera_viktsummor(config) + _kontrollera_mattnamn(config)
    if fel:
        raise KonfigFel("Fel i config/config.yaml:\n" + "\n".join(fel))

    if fel := _kontrollera_universum(config, universum):
        raise KonfigFel("Fel i config/universum.yaml:\n" + "\n".join(fel))

    if fel := _kontrollera_portfolj(universum, portfolj):
        raise KonfigFel("Fel i config/portfolj.yaml:\n" + "\n".join(fel))

    return Installningar(
        config=config, universum=universum, portfolj=portfolj, rotkatalog=rot
    )


def fran_dictar(
    config: dict[str, Any],
    universum: dict[str, Any],
    portfolj: dict[str, Any],
    rotkatalog: Path | None = None,
) -> Installningar:
    """Samma validering men från färdiga dictar. Används av testerna."""
    c: Config = _validera(Config, config, "config.yaml")
    u: Universumfil = _validera(Universumfil, universum, "universum.yaml")
    p: Portfoljfil = _validera(Portfoljfil, portfolj, "portfolj.yaml")

    fel = _kontrollera_viktsummor(c) + _kontrollera_mattnamn(c)
    if fel:
        raise KonfigFel("Fel i config.yaml:\n" + "\n".join(fel))
    if fel := _kontrollera_universum(c, u):
        raise KonfigFel("Fel i universum.yaml:\n" + "\n".join(fel))
    if fel := _kontrollera_portfolj(u, p):
        raise KonfigFel("Fel i portfolj.yaml:\n" + "\n".join(fel))
    return Installningar(
        config=c, universum=u, portfolj=p, rotkatalog=rotkatalog or Path.cwd()
    )
