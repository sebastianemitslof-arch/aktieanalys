"""Hämtaren -- binder ihop cache, datakälla och CSV-komplettering.

Ordningen är alltid densamma för varje bolag:

1. Finns färsk data i databasen? Använd den. yfinance anropas inte.
2. Annars: fråga datakällan.
3. Lägg på de manuella CSV-värdena (de skriver aldrig över hämtad data).
4. Spara allt i databasen med dagens datum.
5. Logga hur det gick.

Steg 3 körs även när data kommer ur cachen, så att en rättad CSV-fil får
effekt direkt utan att du behöver tvinga om en hämtning.
"""

from __future__ import annotations

import datetime as dt
import time
from dataclasses import dataclass, field

from aktieanalys.data.bolagsdata import MANUELLA_FALT, Bolagsdata
from aktieanalys.data.csv_komplettering import CsvKomplettering
from aktieanalys.data.lager import Lager
from aktieanalys.data.provider import Provider
from aktieanalys.data.valuta import Valutaomraknare
from aktieanalys.data.yfinance_provider import YfinanceProvider
from aktieanalys.konfig import Installningar
from aktieanalys.logg import hamta

logg = hamta(__name__)


@dataclass
class Hamtrapport:
    """Vad som hände under en hämtning. Underlag till veckorapporten."""

    datum: dt.date
    data: dict[str, Bolagsdata] = field(default_factory=dict)
    fran_cache: list[str] = field(default_factory=list)
    nyhamtade: list[str] = field(default_factory=list)
    utan_svar: dict[str, str] = field(default_factory=dict)
    csv_sammanfattning: str = ""
    okanda_csv_tickers: list[str] = field(default_factory=list)

    @property
    def antal(self) -> int:
        return len(self.data)

    def bolag_med_fel(self) -> dict[str, list[str]]:
        return {t: bd.fel for t, bd in self.data.items() if bd.fel}

    def sammanfattning(self) -> str:
        delar = [f"{self.antal} bolag"]
        if self.nyhamtade:
            delar.append(f"{len(self.nyhamtade)} nyhämtade")
        if self.fran_cache:
            delar.append(f"{len(self.fran_cache)} ur cachen")
        if self.utan_svar:
            delar.append(f"{len(self.utan_svar)} utan svar")
        return ", ".join(delar)


class Hamtare:
    def __init__(
        self,
        inst: Installningar,
        provider: Provider | None = None,
        lager: Lager | None = None,
        csv: CsvKomplettering | None = None,
    ) -> None:
        self.inst = inst
        c = inst.config
        # Lagret skapas först: valutaomräknaren cachar sina kurser där,
        # så USDSEK och EURSEK hämtas en gång och inte per bolag.
        self.lager = lager or Lager(inst.databasvag)
        self.provider = provider or YfinanceProvider(
            max_forsok=c.data.max_forsok,
            paus_sek=c.data.paus_mellan_forsok_sek,
            handelsdagar_for_omsattning=c.universum.handelsdagar_for_omsattning,
            ar_historik=c.poang.antal_ar_historik,
            valutaomraknare=Valutaomraknare(
                lager=self.lager,
                max_forsok=c.data.max_forsok,
                paus_sek=c.data.paus_mellan_forsok_sek,
                ar_historik=c.poang.antal_ar_historik,
            ),
        )
        self.csv = csv if csv is not None else CsvKomplettering(inst.manuell_katalog)

    # =================================================================
    def hamta(
        self,
        tickers: list[str] | None = None,
        tvinga: bool = False,
        idag: dt.date | None = None,
    ) -> Hamtrapport:
        idag = idag or dt.date.today()
        tickers = tickers if tickers is not None else self.inst.alla_tickers()
        cache_dagar = self.inst.config.data.cache_dagar

        rapport = Hamtrapport(
            datum=idag,
            csv_sammanfattning=self.csv.sammanfattning(),
            okanda_csv_tickers=self.csv.okanda_tickers(self.inst.alla_tickers()),
        )
        if rapport.okanda_csv_tickers:
            logg.warning(
                "CSV-filerna innehåller tickers som inte finns i universum.yaml: %s",
                ", ".join(rapport.okanda_csv_tickers),
            )

        paus = self.inst.config.data.paus_mellan_bolag_sek
        for nr, ticker in enumerate(tickers, start=1):
            logg.info("[%d/%d] %s", nr, len(tickers), ticker)
            try:
                bd = self._ett_bolag(ticker, idag, tvinga, cache_dagar, rapport)
            except Exception as e:
                # Sista skyddsnätet. Går något oväntat fel -- i providern,
                # i databasen eller i CSV-påläggningen -- ska de övriga
                # bolagen ändå hämtas och veckorapporten ändå gå ut.
                logg.exception("%s: oväntat fel, hoppar över bolaget", ticker)
                rapport.utan_svar[ticker] = (
                    f"oväntat fel: {type(e).__name__}: {e}"
                )
                continue
            if bd is not None:
                rapport.data[ticker] = bd
            if paus and nr < len(tickers):
                time.sleep(paus)
        logg.info("hämtning klar: %s", rapport.sammanfattning())
        return rapport

    # =================================================================
    def _ett_bolag(
        self,
        ticker: str,
        idag: dt.date,
        tvinga: bool,
        cache_dagar: int,
        rapport: Hamtrapport,
    ) -> Bolagsdata | None:
        bd: Bolagsdata | None = None
        ur_cache = False

        if not tvinga and self.lager.ar_farsk(ticker, cache_dagar, idag):
            bd = self.lager.las(ticker, idag)
            if bd is not None:
                ur_cache = True
                alder = self.lager.alder_i_dagar(ticker, idag)
                logg.debug("%s: ur cachen (%d dagar gammal)", ticker, alder)

        if bd is None:
            # Skicka med vad vi redan vet. Yahoo utelämnar ibland
            # rapportvalutan, och utan den kan räkenskaperna inte
            # valutasäkras.
            bd = self.provider.hamta(
                ticker, idag, kand_meta=self.lager.senast_kanda_meta(ticker, idag)
            )

        fyllda = self.csv.komplettera(bd)
        if fyllda:
            logg.debug("%s: %d fält från CSV", ticker, fyllda)
        self._markera_kvarvarande_manuella(bd)

        if bd.antal_varden == 0:
            orsak = bd.fel[0] if bd.fel else "inga värden kunde hämtas"
            rapport.utan_svar[ticker] = orsak
            self.lager.logga_hamtning(
                ticker, idag, self.provider.namn, "utan_svar", 0, orsak
            )
            logg.warning("%s: %s", ticker, orsak)
            return bd

        self.lager.spara(bd)
        self.lager.logga_hamtning(
            ticker,
            idag,
            "cache" if ur_cache else self.provider.namn,
            "ok" if not bd.fel else "delvis",
            bd.antal_varden,
            " | ".join(bd.fel)[:2000],
        )
        (rapport.fran_cache if ur_cache else rapport.nyhamtade).append(ticker)
        return bd

    @staticmethod
    def _markera_kvarvarande_manuella(bd: Bolagsdata) -> None:
        """Manuella fält som fortfarande saknas får en orsak som pekar på CSV."""
        for falt in MANUELLA_FALT:
            if not bd.har(falt):
                bd.markera_saknat(
                    falt,
                    "saknas -- fyll i data/manuellt/ om kategorin kräver måttet",
                )

    def stang(self) -> None:
        self.lager.stang()
