"""Manuell komplettering via CSV-filer.

Åtta nyckeltal finns inte i yfinance och går inte att räkna fram ur det
som finns: substansvärden, förvaltningsresultat, belåningsgrad,
räntetäckningsgrad och bankernas kapitaltäckning. De skriver du in för
hand, en gång per kvartal eller år, i CSV-filerna i data/manuellt/.

Format: en rad per bolag och år, en kolumn per nyckeltal.

    ticker,ar,substansvarde_per_aktie
    INVE-B.ST,2025,385.50
    INVE-B.ST,2024,342.10

Kolumnen "ar" är det räkenskapsår uppgiften gäller. Systemet bygger två
saker av varje kolumn: historiken (alla år) och det senaste värdet.

Filerna kan redigeras i Excel, Numbers eller Google Sheets -- spara som
CSV. Rader som börjar med # är kommentarer. Tomma celler hoppas över,
de blir aldrig nollor.
"""

from __future__ import annotations

import csv
import datetime as dt
from collections import defaultdict
from pathlib import Path

from aktieanalys.data.bolagsdata import FALT, MANUELLA_FALT, Bolagsdata
from aktieanalys.logg import hamta

logg = hamta(__name__)

OBLIGATORISKA_KOLUMNER = ("ticker", "ar")


class CsvFel(Exception):
    """Fel i en CSV-fil. Meddelandet namnger fil, rad och kolumn."""


def tillatna_kolumner() -> list[str]:
    """Nyckeltalskolumner en CSV-fil får innehålla.

    Historikfälten (..._historik) står inte med: de byggs automatiskt av
    årsraderna, så du skriver aldrig dem själv.
    """
    return sorted(f for f in MANUELLA_FALT if not f.endswith("_historik"))


class CsvKomplettering:
    """Läser CSV-filerna i en katalog och lägger värdena på Bolagsdata.

    Läses in en gång vid start. Fel i en fil höjs som CsvFel med
    radnummer -- en felskriven siffra ska stoppa körningen, inte smyga
    in i analysen.
    """

    namn = "csv"

    def __init__(self, katalog: Path | str, idag: dt.date | None = None) -> None:
        self.katalog = Path(katalog)
        self.idag = idag or dt.date.today()
        # ticker -> falt -> {ar: varde}
        self.varden: dict[str, dict[str, dict[int, float]]] = defaultdict(
            lambda: defaultdict(dict)
        )
        self.kallor: dict[tuple[str, str], str] = {}
        self.filer: list[str] = []
        self._las_katalog()

    # =================================================================
    #  Inläsning
    # =================================================================
    def _las_katalog(self) -> None:
        if not self.katalog.exists():
            logg.info("ingen katalog %s -- hoppar över manuell komplettering",
                      self.katalog)
            return
        for fil in sorted(self.katalog.glob("*.csv")):
            self._las_fil(fil)
        if self.filer:
            logg.info("läste %d CSV-fil(er): %s", len(self.filer), ", ".join(self.filer))

    def _las_fil(self, fil: Path) -> None:
        rader = [
            r for r in fil.read_text(encoding="utf-8-sig").splitlines()
            if r.strip() and not r.lstrip().startswith("#")
        ]
        if not rader:
            logg.info("%s innehåller inga datarader", fil.name)
            return

        lasare = csv.DictReader(rader)
        rubriker = [r.strip() for r in (lasare.fieldnames or [])]
        if not rubriker:
            raise CsvFel(f"{fil.name}: filen saknar rubrikrad")

        for kolumn in OBLIGATORISKA_KOLUMNER:
            if kolumn not in rubriker:
                raise CsvFel(
                    f"{fil.name}: kolumnen {kolumn!r} måste finnas. "
                    f"Rubrikraden ska börja med: {','.join(OBLIGATORISKA_KOLUMNER)}"
                )

        nyckeltal = [r for r in rubriker if r not in OBLIGATORISKA_KOLUMNER]
        if not nyckeltal:
            raise CsvFel(
                f"{fil.name}: filen har inga nyckeltalskolumner. Lägg till minst "
                f"en av: {', '.join(tillatna_kolumner())}"
            )
        for kolumn in nyckeltal:
            if kolumn in FALT and kolumn not in MANUELLA_FALT:
                raise CsvFel(
                    f"{fil.name}: kolumnen {kolumn!r} hämtas automatiskt från "
                    f"yfinance och ska inte fyllas i för hand."
                )
            if kolumn not in tillatna_kolumner():
                import difflib

                forslag = difflib.get_close_matches(kolumn, tillatna_kolumner(), n=2, cutoff=0.4)
                extra = f" Menade du {' eller '.join(forslag)}?" if forslag else ""
                raise CsvFel(
                    f"{fil.name}: okänd kolumn {kolumn!r}.{extra}\n"
                    f"Tillåtna nyckeltalskolumner: {', '.join(tillatna_kolumner())}"
                )

        sedda: set[tuple[str, int]] = set()
        # radnummer 2 = första raden efter rubriken, som i ett kalkylprogram
        for radnr, rad in enumerate(lasare, start=2):
            ticker = (rad.get("ticker") or "").strip()
            if not ticker:
                raise CsvFel(f"{fil.name} rad {radnr}: ticker saknas")

            ar_text = (rad.get("ar") or "").strip()
            try:
                ar = int(ar_text)
            except ValueError:
                raise CsvFel(
                    f"{fil.name} rad {radnr}: ar är {ar_text!r}, måste vara ett "
                    f"årtal som 2025"
                ) from None
            if not 1990 <= ar <= self.idag.year + 1:
                raise CsvFel(
                    f"{fil.name} rad {radnr}: årtalet {ar} ligger utanför det "
                    f"rimliga (1990--{self.idag.year + 1})"
                )
            if (ticker, ar) in sedda:
                raise CsvFel(
                    f"{fil.name} rad {radnr}: {ticker} år {ar} står flera gånger. "
                    f"Slå ihop raderna."
                )
            sedda.add((ticker, ar))

            for kolumn in nyckeltal:
                text = (rad.get(kolumn) or "").strip()
                if not text:
                    continue  # tom cell = uppgift saknas, inte noll
                try:
                    varde = float(text.replace(",", ".").replace(" ", ""))
                except ValueError:
                    raise CsvFel(
                        f"{fil.name} rad {radnr}, kolumn {kolumn}: {text!r} är "
                        f"inget tal. Använd punkt som decimaltecken och inga "
                        f"tusenavskiljare."
                    ) from None
                self.varden[ticker][kolumn][ar] = varde
                self.kallor[(ticker, kolumn)] = f"csv:{fil.name}"

        self.filer.append(fil.name)

    # =================================================================
    #  Användning
    # =================================================================
    def komplettera(self, bd: Bolagsdata) -> int:
        """Lägger de manuella värdena på ett Bolagsdata.

        Returnerar antalet fält som fylldes. Rör inte fält som redan har
        ett värde -- CSV-filen kompletterar, den skriver inte över.
        """
        per_falt = self.varden.get(bd.ticker)
        if not per_falt:
            return 0

        fyllda = 0
        for falt, per_ar in per_falt.items():
            if not per_ar:
                continue
            kalla = self.kallor.get((bd.ticker, falt), "csv")
            serie = sorted(per_ar.items(), key=lambda x: x[0], reverse=True)

            # Senaste årets värde som enskilt tal
            if not bd.har(falt):
                bd.satt(falt, serie[0][1], f"{kalla} ({serie[0][0]})")
                fyllda += 1

            # Hela serien som historik, om fältet har ett historikfält
            historikfalt = f"{falt}_historik"
            if historikfalt in FALT and not bd.har(historikfalt):
                bd.satt(historikfalt, serie, f"{kalla} ({len(serie)} år)")
                fyllda += 1
        return fyllda

    def tickers(self) -> list[str]:
        return sorted(self.varden)

    def okanda_tickers(self, universum: list[str]) -> list[str]:
        """Tickers i CSV-filerna som inte finns i universum.yaml.

        Inget fel -- men värt att påpeka, det är oftast ett stavfel.
        """
        return sorted(set(self.varden) - set(universum))

    def sammanfattning(self) -> str:
        if not self.filer:
            return "inga CSV-filer med manuell data"
        antal = sum(len(per_ar) for f in self.varden.values() for per_ar in f.values())
        return (
            f"{len(self.filer)} fil(er), {len(self.varden)} bolag, {antal} värden"
        )
