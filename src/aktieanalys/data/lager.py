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
  veckopoang  varje veckas poäng per bolag -- grunden för bytesreglerna
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

CREATE TABLE IF NOT EXISTS veckopoang (
    datum              TEXT NOT NULL,
    ticker             TEXT NOT NULL,
    kategori           TEXT NOT NULL,
    poang_kvantitativ  REAL,
    poang_slutlig      REAL,
    placering          INTEGER,
    ar_innehav         INTEGER NOT NULL DEFAULT 0,
    justering          REAL NOT NULL DEFAULT 0,
    PRIMARY KEY (datum, ticker)
);
CREATE INDEX IF NOT EXISTS ix_veckopoang ON veckopoang (kategori, datum);

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


def aterskapa(dumpvag: Path | str, databasvag: Path | str) -> int:
    """Bygger upp databasen ur en textdump.

    Den befintliga databasfilen ersätts. Används i veckokörningen, där
    repot bara innehåller textdumpen.
    """
    dumpvag, databasvag = Path(dumpvag), Path(databasvag)
    if not dumpvag.exists():
        raise FileNotFoundError(f"ingen dump att återskapa från: {dumpvag}")

    databasvag.parent.mkdir(parents=True, exist_ok=True)
    if databasvag.exists():
        databasvag.unlink()

    db = sqlite3.connect(databasvag)
    try:
        db.executescript(dumpvag.read_text(encoding="utf-8"))
        db.commit()
        antal = db.execute("SELECT COUNT(*) FROM fundamenta").fetchone()[0]
    finally:
        db.close()
    logg.info("återskapade databasen ur %s (%d fundamentavärden)", dumpvag, antal)
    return antal


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
                    if falt == "kurshistorik":
                        self.spara_kurser(
                            bd.ticker, varde, commit=False,
                            volymer=bd.varde("volymhistorik"),
                        )
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
        self,
        ticker: str,
        serie: pd.Series,
        commit: bool = True,
        volymer: pd.Series | None = None,
    ) -> int:
        """Sparar kurshistorik, och volym när den finns."""
        if serie is None or len(serie) == 0:
            return 0
        per_dag = {} if volymer is None else {
            pd.Timestamp(d).date().isoformat(): float(v)
            for d, v in volymer.items() if pd.notna(v)
        }
        rader = [
            (ticker, pd.Timestamp(d).date().isoformat(), float(v),
             per_dag.get(pd.Timestamp(d).date().isoformat()))
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
            volymer = self.las_volymer(ticker, per_datum)
            if not volymer.empty:
                bd.satt("volymhistorik", volymer, "databas")

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
        return self._kurskolumn(ticker, "stangning", till_datum)

    def las_volymer(
        self, ticker: str, till_datum: dt.date | None = None
    ) -> pd.Series:
        """Handelsvolym per dag. Tom serie för äldre data som saknar volym."""
        return self._kurskolumn(ticker, "volym", till_datum)

    def _kurskolumn(
        self, ticker: str, kolumn: str, till_datum: dt.date | None
    ) -> pd.Series:
        villkor = "" if till_datum is None else " AND datum<=?"
        param = [ticker] + ([] if till_datum is None else [till_datum.isoformat()])
        rader = self.db.execute(
            f"SELECT datum, {kolumn} FROM kurser WHERE ticker=?{villkor} "
            f"AND {kolumn} IS NOT NULL ORDER BY datum",
            param,
        ).fetchall()
        if not rader:
            return pd.Series(dtype=float)
        return pd.Series(
            [r[kolumn] for r in rader],
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
            "veckorapporter": q("SELECT COUNT(DISTINCT datum) FROM veckopoang"),
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

    # =================================================================
    #  Veckopoäng
    # =================================================================
    def spara_veckopoang(self, datum: dt.date, rader: list[dict]) -> int:
        """Sparar en veckas poäng. Kör man om samma dag skrivs den över.

        Det är den här historiken bytesreglerna läser för att avgöra om
        en utmanare varit bättre flera veckor i rad. Utan den skulle ett
        byte kunna utlösas av en enda veckas brus.
        """
        with self.db:
            self.db.executemany(
                "INSERT OR REPLACE INTO veckopoang (datum, ticker, kategori, "
                "poang_kvantitativ, poang_slutlig, placering, ar_innehav, "
                "justering) VALUES (?,?,?,?,?,?,?,?)",
                [
                    (
                        datum.isoformat(), r["ticker"], r["kategori"],
                        r.get("poang_kvantitativ"), r.get("poang_slutlig"),
                        r.get("placering"), int(r.get("ar_innehav", False)),
                        r.get("justering", 0.0),
                    )
                    for r in rader
                ],
            )
        logg.info("sparade veckopoäng för %d bolag per %s", len(rader), datum)
        return len(rader)

    def rapportdatum(
        self, kategori: str | None = None, till: dt.date | None = None
    ) -> list[dt.date]:
        """Alla datum som har loggad veckopoäng, nyast först."""
        villkor, param = "", []
        if kategori:
            villkor += " AND kategori=?"
            param.append(kategori)
        if till:
            villkor += " AND datum<=?"
            param.append(till.isoformat())
        rader = self.db.execute(
            f"SELECT DISTINCT datum FROM veckopoang WHERE 1=1{villkor} "
            f"ORDER BY datum DESC",
            param,
        ).fetchall()
        return [dt.date.fromisoformat(r["datum"]) for r in rader]

    def veckopoang_vid(self, datum: dt.date, kategori: str) -> dict[str, dict]:
        """Poängen för en kategori ett visst rapportdatum, per ticker."""
        return {
            r["ticker"]: {
                "poang_slutlig": r["poang_slutlig"],
                "poang_kvantitativ": r["poang_kvantitativ"],
                "placering": r["placering"],
                "ar_innehav": bool(r["ar_innehav"]),
                "justering": r["justering"],
            }
            for r in self.db.execute(
                "SELECT * FROM veckopoang WHERE datum=? AND kategori=?",
                (datum.isoformat(), kategori),
            )
        }

    # =================================================================
    #  Textdump: databasen i en form git kan hantera
    # =================================================================
    #: Tabellerna och vad de sorteras på. Sorteringen är det viktiga:
    #: den gör dumpen DETERMINISTISK, så att en ny vecka blir ett litet
    #: tillägg i filen i stället för att kasta om allt. Git lagrar då en
    #: delta på några tiotals kilobyte i stället för en ny megabytefil.
    DUMPORDNING = {
        "fundamenta": "ticker, datum, falt",
        "bolagsmeta": "ticker, datum",
        "kurser": "ticker, datum",
        "saknat": "ticker, datum, falt",
        "veckopoang": "datum, ticker",
        "hamtlogg": "id",
    }

    def dumpa(self, vag: Path | str) -> int:
        """Skriver hela databasen som sorterad SQL-text.

        En SQLite-fil skrivs om på tvären varje gång, så git lagrar en ny
        binär kopia varje vecka -- ungefär en halv gigabyte per år för
        den här databasen. En sorterad textdump växer i stället med det
        som faktiskt tillkommit.
        """
        vag = Path(vag)
        vag.parent.mkdir(parents=True, exist_ok=True)
        rader = 0

        with vag.open("w", encoding="utf-8", newline="\n") as f:
            f.write(
                "-- Databasdump för aktieanalys.\n"
                "-- Sorterad med flit: det gör filen deterministisk, så att\n"
                "-- git kan lagra varje vecka som en liten delta.\n"
                "-- Återskapa med: python -m aktieanalys aterskapa-databas\n"
                "PRAGMA foreign_keys=OFF;\n"
                "BEGIN TRANSACTION;\n"
            )
            for sats in self.db.execute(
                # sqlite_% är SQLites interna tabeller (t.ex.
                # sqlite_sequence från AUTOINCREMENT). De skapas av
                # motorn själv och går inte att skapa om ur en dump.
                "SELECT sql FROM sqlite_master WHERE sql IS NOT NULL "
                "AND name NOT LIKE 'sqlite_%' ORDER BY type DESC, name"
            ):
                f.write(f"{sats[0]};\n")

            for tabell, ordning in self.DUMPORDNING.items():
                kolumner = [
                    r["name"]
                    for r in self.db.execute(f"PRAGMA table_info({tabell})")
                ]
                if not kolumner:
                    continue
                # quote() är SQLites egen escapning -- säkrare än att
                # formatera värdena själv.
                uttryck = " || ',' || ".join(f"quote({k})" for k in kolumner)
                for rad in self.db.execute(
                    f"SELECT 'INSERT INTO {tabell} VALUES(' || {uttryck} || ');' "
                    f"FROM {tabell} ORDER BY {ordning}"
                ):
                    f.write(rad[0] + "\n")
                    rader += 1
            f.write("COMMIT;\n")

        logg.info("dumpade %d rader till %s", rader, vag)
        return rader

    def harledda_falt(self) -> dict[str, int]:
        """Fält som har härledda (proxy-)värden i databasen, med antal rader."""
        return {
            r["falt"]: r["n"]
            for r in self.db.execute(
                "SELECT falt, COUNT(*) AS n FROM fundamenta "
                "WHERE kalla LIKE 'härlett%' GROUP BY falt ORDER BY falt"
            )
        }

    def ta_bort_harledda(self, falt: str) -> int:
        """Tar bort HÄRLEDDA värden för ett fält. Returnerar antalet rader.

        Rör aldrig värden från yfinance eller från CSV-filerna -- bara
        sådana som räknats fram av en härledning.

        Behövs eftersom arkivet medvetet aldrig skriver över ett värde
        med ingenting. Drar man tillbaka en härledning, som när den visat
        sig opålitlig, blir de gamla proxyvärdena annars kvar och
        fortsätter användas.
        """
        with self.db:
            cur = self.db.execute(
                "DELETE FROM fundamenta WHERE falt=? AND kalla LIKE 'härlett%'",
                (falt,),
            )
        logg.info("tog bort %d härledda värden för %s", cur.rowcount, falt)
        return cur.rowcount

    def hamtningar_med_problem(self, datum: dt.date) -> list[sqlite3.Row]:
        """Hämtningar som inte gick bra ett visst datum. Till rapporten."""
        return self.db.execute(
            "SELECT ticker, kalla, status, meddelande FROM hamtlogg "
            "WHERE datum=? AND status<>'ok' ORDER BY ticker",
            (datum.isoformat(),),
        ).fetchall()
