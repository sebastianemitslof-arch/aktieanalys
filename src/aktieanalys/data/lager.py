"""SQLite-lagret: cache och arkiv i samma fil.

Filen data/cache.sqlite har två roller:

* **Cache.** Data som hämtats nyligen hämtas inte igen. yfinance är
  inofficiellt och tål inte att pressas.
* **Arkiv.** Varje hämtning sparas MED DATUM och skrivs aldrig över.
  Det är det som gör backtestet hederligt på sikt: frågar man databasen
  "vad visste vi den 3 maj?" får man svaret som gällde då, inte dagens
  siffror.

Radera aldrig filen. Den är den enda delen av projektet som inte går
att återskapa.

Tabellerna:
  fundamenta  ett värde per bolag, fält och hämtdatum
  bolagsmeta  namn och valutor per bolag och hämtdatum
  kurser      daglig stängningskurs och volym
  saknat      vilka fält som saknades, med orsak
  hamtlogg    varje hämtningsförsök och hur det gick
"""

from __future__ import annotations

import datetime as dt
import json
import sqlite3
from pathlib import Path
from typing import Any

import pandas as pd

from aktieanalys.data.bolagsdata import FALT, Bolagsdata
from aktieanalys.logg import hamta

logg = hamta(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS fundamenta (
    ticker     TEXT NOT NULL,
    datum      TEXT NOT NULL,
    falt       TEXT NOT NULL,
    varde      REAL,
    varde_json TEXT,
    kalla      TEXT,
    PRIMARY KEY (ticker, datum, falt)
);
CREATE INDEX IF NOT EXISTS ix_fundamenta_falt ON fundamenta (ticker, falt, datum);

CREATE TABLE IF NOT EXISTS kurser (
    ticker     TEXT NOT NULL,
    datum      TEXT NOT NULL,
    stangning  REAL NOT NULL,
    volym      REAL,
    PRIMARY KEY (ticker, datum)
);

CREATE TABLE IF NOT EXISTS bolagsmeta (
    ticker        TEXT NOT NULL,
    datum         TEXT NOT NULL,
    namn          TEXT,
    valuta        TEXT,
    rapportvaluta TEXT,
    PRIMARY KEY (ticker, datum)
);

CREATE TABLE IF NOT EXISTS saknat (
    ticker TEXT NOT NULL,
    datum  TEXT NOT NULL,
    falt   TEXT NOT NULL,
    orsak  TEXT NOT NULL,
    PRIMARY KEY (ticker, datum, falt)
);

CREATE TABLE IF NOT EXISTS hamtlogg (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker      TEXT NOT NULL,
    datum       TEXT NOT NULL,
    tidpunkt    TEXT NOT NULL,
    kalla       TEXT NOT NULL,
    status      TEXT NOT NULL,
    antal_falt  INTEGER,
    meddelande  TEXT
);
CREATE INDEX IF NOT EXISTS ix_hamtlogg_datum ON hamtlogg (datum);
"""


class Lager:
    """Läser och skriver databasen. Används som context manager."""

    def __init__(self, vag: Path | str) -> None:
        self.vag = Path(vag)
        self.vag.parent.mkdir(parents=True, exist_ok=True)
        # Medvetet INTE WAL-läge: det skapar extrafiler (-wal, -shm) som
        # gör att databasen inte går att committa rent i GitHub Actions.
        self.db = sqlite3.connect(self.vag)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)
        self.db.commit()

    def __enter__(self) -> Lager:
        return self

    def __exit__(self, *_: object) -> None:
        self.stang()

    def stang(self) -> None:
        self.db.commit()
        self.db.close()

    # =================================================================
    #  Skriva
    # =================================================================
    def spara(self, bd: Bolagsdata) -> None:
        """Sparar ett Bolagsdata med sitt hämtdatum.

        Körs analysen två gånger samma dag skrivs dagens rader över --
        men gårdagens rader lämnas orörda. Historiken byggs per dag.
        """
        datum = bd.hamtad.isoformat()
        with self.db:
            for falt, varde in bd.varden.items():
                definition = FALT[falt]
                if definition.typ == "kursserie":
                    self.spara_kurser(bd.ticker, varde, commit=False)
                    continue
                tal, text = None, None
                if definition.typ == "arslista":
                    text = json.dumps(varde)
                else:
                    tal = float(varde)
                self.db.execute(
                    "INSERT OR REPLACE INTO fundamenta "
                    "(ticker, datum, falt, varde, varde_json, kalla) "
                    "VALUES (?,?,?,?,?,?)",
                    (bd.ticker, datum, falt, tal, text, bd.kallor.get(falt)),
                )

            self.db.execute(
                "INSERT OR REPLACE INTO bolagsmeta "
                "(ticker, datum, namn, valuta, rapportvaluta) VALUES (?,?,?,?,?)",
                (bd.ticker, datum, bd.namn, bd.valuta, bd.rapportvaluta),
            )
            self.db.execute(
                "DELETE FROM saknat WHERE ticker=? AND datum=?", (bd.ticker, datum)
            )
            for falt, orsak in bd.saknade.items():
                self.db.execute(
                    "INSERT OR REPLACE INTO saknat (ticker, datum, falt, orsak) "
                    "VALUES (?,?,?,?)",
                    (bd.ticker, datum, falt, orsak),
                )

    def spara_kurser(
        self, ticker: str, serie: pd.Series, commit: bool = True
    ) -> int:
        """Sparar kurshistorik. Serien ska ha datum som index."""
        if serie is None or len(serie) == 0:
            return 0
        rader = [
            (ticker, pd.Timestamp(d).date().isoformat(), float(v), None)
            for d, v in serie.items()
            if pd.notna(v)
        ]
        self.db.executemany(
            "INSERT OR REPLACE INTO kurser (ticker, datum, stangning, volym) "
            "VALUES (?,?,?,?)",
            rader,
        )
        if commit:
            self.db.commit()
        return len(rader)

    def logga_hamtning(
        self,
        ticker: str,
        datum: dt.date,
        kalla: str,
        status: str,
        antal_falt: int = 0,
        meddelande: str = "",
    ) -> None:
        with self.db:
            self.db.execute(
                "INSERT INTO hamtlogg "
                "(ticker, datum, tidpunkt, kalla, status, antal_falt, meddelande) "
                "VALUES (?,?,?,?,?,?,?)",
                (
                    ticker,
                    datum.isoformat(),
                    dt.datetime.now().isoformat(timespec="seconds"),
                    kalla,
                    status,
                    antal_falt,
                    meddelande[:2000],
                ),
            )

    # =================================================================
    #  Läsa
    # =================================================================
    def las(
        self,
        ticker: str,
        per_datum: dt.date | None = None,
        med_kurser: bool = True,
    ) -> Bolagsdata | None:
        """Allt vi visste om bolaget per ett visst datum.

        Det här är den punkt-i-tiden-läsning backtestet bygger på: för
        varje fält hämtas det SENASTE värdet som fanns den dagen eller
        tidigare. Ingen framtidsinformation kan läcka in.

        Returnerar None om bolaget inte finns i databasen alls.
        """
        per = (per_datum or dt.date.today()).isoformat()
        rader = self.db.execute(
            """
            SELECT f.falt, f.varde, f.varde_json, f.kalla, f.datum
            FROM fundamenta f
            WHERE f.ticker = ? AND f.datum <= ?
              AND f.datum = (
                  SELECT MAX(datum) FROM fundamenta
                  WHERE ticker = f.ticker AND falt = f.falt AND datum <= ?
              )
            """,
            (ticker, per, per),
        ).fetchall()

        kurser = self.las_kurser(ticker, per_datum) if med_kurser else None
        if not rader and (kurser is None or kurser.empty):
            return None

        senaste = max((r["datum"] for r in rader), default=per)
        bd = Bolagsdata(ticker=ticker, hamtad=dt.date.fromisoformat(senaste))
        meta = self.senast_kanda_meta(ticker, per_datum)
        bd.namn = meta.get("namn") or ticker
        bd.valuta = meta.get("valuta")
        bd.rapportvaluta = meta.get("rapportvaluta")
        for r in rader:
            falt = r["falt"]
            if falt not in FALT:
                logg.warning(
                    "hoppar över fältet %s för %s: finns inte längre i "
                    "fältkatalogen", falt, ticker,
                )
                continue
            varde = (
                json.loads(r["varde_json"])
                if FALT[falt].typ == "arslista"
                else r["varde"]
            )
            if FALT[falt].typ == "arslista" and varde is not None:
                varde = [tuple(x) for x in varde]
            bd.satt(falt, varde, r["kalla"] or "databas")
            bd.faltdatum[falt] = dt.date.fromisoformat(r["datum"])

        if kurser is not None and not kurser.empty:
            bd.satt("kurshistorik", kurser, "databas")

        for r in self.db.execute(
            "SELECT falt, orsak FROM saknat WHERE ticker=? AND datum=?",
            (ticker, senaste),
        ):
            if r["falt"] in FALT:
                bd.markera_saknat(r["falt"], r["orsak"])
        return bd

    def senast_kanda_meta(
        self, ticker: str, per_datum: dt.date | None = None
    ) -> dict[str, str | None]:
        """Senast kända namn och valutor för ett bolag.

        Yahoo utelämnar ibland financialCurrency ur sitt svar. Utan den
        går det inte att veta om räkenskaperna behöver räknas om till
        kronor -- och att då gissa "SEK" ger tysta tiopotensfel. Därför
        sparas valutan, och den senast kända används som reserv.
        """
        per = (per_datum or dt.date.today()).isoformat()
        rad = self.db.execute(
            "SELECT namn, valuta, rapportvaluta FROM bolagsmeta "
            "WHERE ticker=? AND datum<=? AND rapportvaluta IS NOT NULL "
            "ORDER BY datum DESC LIMIT 1",
            (ticker, per),
        ).fetchone()
        if rad is None:
            rad = self.db.execute(
                "SELECT namn, valuta, rapportvaluta FROM bolagsmeta "
                "WHERE ticker=? AND datum<=? ORDER BY datum DESC LIMIT 1",
                (ticker, per),
            ).fetchone()
        if rad is None:
            return {}
        return {
            "namn": rad["namn"],
            "valuta": rad["valuta"],
            "rapportvaluta": rad["rapportvaluta"],
        }

    def las_kurser(
        self, ticker: str, till_datum: dt.date | None = None
    ) -> pd.Series:
        """Kurshistoriken, eventuellt avskuren vid ett datum."""
        if till_datum is None:
            rader = self.db.execute(
                "SELECT datum, stangning FROM kurser WHERE ticker=? ORDER BY datum",
                (ticker,),
            ).fetchall()
        else:
            rader = self.db.execute(
                "SELECT datum, stangning FROM kurser WHERE ticker=? AND datum<=? "
                "ORDER BY datum",
                (ticker, till_datum.isoformat()),
            ).fetchall()
        if not rader:
            return pd.Series(dtype=float)
        return pd.Series(
            [r["stangning"] for r in rader],
            index=pd.to_datetime([r["datum"] for r in rader]),
            name=ticker,
            dtype=float,
        )

    def alder_i_dagar(self, ticker: str, idag: dt.date | None = None) -> int | None:
        """Hur gammal den senaste hämtningen är. None om bolaget är okänt."""
        rad = self.db.execute(
            "SELECT MAX(datum) AS d FROM fundamenta WHERE ticker=?", (ticker,)
        ).fetchone()
        if rad is None or rad["d"] is None:
            return None
        return ((idag or dt.date.today()) - dt.date.fromisoformat(rad["d"])).days

    def ar_farsk(
        self, ticker: str, max_alder_dagar: int, idag: dt.date | None = None
    ) -> bool:
        alder = self.alder_i_dagar(ticker, idag)
        return alder is not None and alder <= max_alder_dagar

    # =================================================================
    #  Översikt
    # =================================================================
    def statistik(self) -> dict[str, Any]:
        q = lambda sql: self.db.execute(sql).fetchone()[0]  # noqa: E731
        return {
            "bolag": q("SELECT COUNT(DISTINCT ticker) FROM fundamenta"),
            "varden": q("SELECT COUNT(*) FROM fundamenta"),
            "kursrader": q("SELECT COUNT(*) FROM kurser"),
            "saknade_varden": q("SELECT COUNT(*) FROM saknat"),
            "hamtningar": q("SELECT COUNT(*) FROM hamtlogg"),
            "bolag_med_valuta": q(
                "SELECT COUNT(DISTINCT ticker) FROM bolagsmeta "
                "WHERE rapportvaluta IS NOT NULL"
            ),
            "forsta_datum": q("SELECT MIN(datum) FROM fundamenta"),
            "senaste_datum": q("SELECT MAX(datum) FROM fundamenta"),
            "storlek_kb": round(self.vag.stat().st_size / 1024, 1)
            if self.vag.exists()
            else 0,
        }

    def hamtningar_med_problem(self, datum: dt.date) -> list[sqlite3.Row]:
        """Hämtningar som inte gick bra ett visst datum. Till rapporten."""
        return self.db.execute(
            "SELECT ticker, kalla, status, meddelande FROM hamtlogg "
            "WHERE datum=? AND status<>'ok' ORDER BY ticker",
            (datum.isoformat(),),
        ).fetchall()
