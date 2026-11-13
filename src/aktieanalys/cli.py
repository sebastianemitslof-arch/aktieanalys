"""Kommandoraden -- allt du kör startar här.

    python -m aktieanalys kontrollera-config
    python -m aktieanalys visa-matt
    python -m aktieanalys visa-universum

Fler kommandon tillkommer i senare steg (hamta-data, veckorapport,
backtest).
"""

from __future__ import annotations

import argparse
import sys

from aktieanalys import logg
from aktieanalys.konfig import KonfigFel, las_installningar
from aktieanalys.poang.matt import MATT, Pelare


def _kontrollera_config(args: argparse.Namespace) -> int:
    inst = las_installningar(args.config)
    c = inst.config

    print("Konfigurationen är giltig.\n")
    print("Universum")
    print(f"  {len(inst.alla_tickers())} bolag i {len(inst.kategorinamn)} kategorier")
    print(f"  gränser: minst {c.universum.min_borsvarde_mdr} mdr kr i börsvärde, "
          f"minst {c.universum.min_daglig_omsattning_mkr} mkr omsättning/dag")
    print(f"  utesluts vid fler än {c.universum.max_saknade_matt_per_bolag} "
          f"saknade nyckeltal\n")

    print("Kategorier")
    for namn, kat in inst.universum.kategorier.items():
        innehav = inst.innehav_i(namn)
        text = innehav.ticker if innehav.finns else "(inget innehav)"
        print(f"  {namn:18} {len(kat.bolag):3} bolag   "
              f"mått: {kat.mattuppsattning:16} innehav: {text}")

    print("\nPoängvikter")
    for pelare, vikt in c.poang.vikter.som_dict().items():
        print(f"  {pelare:18} {vikt:5.0%}")

    print("\nBytesregler")
    print(f"  utmanaren måste vara {c.byten.min_poangforbattring_procent:.0f} % bättre "
          f"i {c.byten.antal_veckor_i_rad} veckor i rad")
    print(f"  minsta innehavstid: {c.byten.min_innehavstid_manader} månader")
    print(f"  courtage: {c.kostnader.courtage_procent} %, minst "
          f"{c.kostnader.courtage_min_kr:.0f} kr per affär")
    fonster = ", ".join(f"{f.fran}--{f.till}" for f in c.byten.beslutsfonster)
    print(f"  beslutsfönster: {fonster}")
    status = "JA -- byten får föreslås" if inst.i_beslutsfonster() else "nej -- bara bevakning"
    print(f"  i beslutsfönster idag: {status}")

    print("\nKvalitativt lager")
    if c.kvalitativt.aktiverat:
        print(f"  på, modell {c.kvalitativt.modell}, "
              f"justering högst ±{c.kvalitativt.max_justering:.0%}")
    else:
        print("  avstängt")

    print("\nRapport")
    if c.rapport.spara_fil:
        print(f"  sparas i {inst.rapportkatalog}")
    if c.rapport.skicka_mejl:
        mottagare = c.rapport.mejl.till or "(mejladress inte ifylld än)"
        print(f"  mejlas till {mottagare}")
    return 0


def _visa_matt(args: argparse.Namespace) -> int:
    print("Alla mått som får användas i config.yaml.\n")
    for pelare in Pelare:
        print(f"{pelare.value.upper()}")
        for namn, d in sorted(MATT.items()):
            if d.pelare is not pelare:
                continue
            riktning = "högre är bättre" if d.riktning.value == "hogre_battre" else "lägre är bättre"
            print(f"  {namn}")
            print(f"      {riktning}")
            print(f"      {d.beskrivning}")
        print()
    return 0


def _visa_universum(args: argparse.Namespace) -> int:
    inst = las_installningar(args.config)
    for namn, kat in inst.universum.kategorier.items():
        upps = inst.mattuppsattning_for(namn)
        print(f"\n{namn}  ({len(kat.bolag)} bolag, mått: {kat.mattuppsattning})")
        print("  mått: " + ", ".join(upps.alla_matt()))
        print("  " + "  ".join(kat.bolag))
    return 0


def _hamta_data(args: argparse.Namespace) -> int:
    from aktieanalys.data.bolagsdata import FALT, MANUELLA_FALT
    from aktieanalys.data.csv_komplettering import CsvFel
    from aktieanalys.data.hamtare import Hamtare

    inst = las_installningar(args.config)
    logg.satt_upp(inst.config.loggning.niva, inst.loggfil)

    tickers = args.bolag or inst.alla_tickers()
    if args.antal:
        tickers = tickers[: args.antal]

    print(f"Hämtar {len(tickers)} bolag. Data nyare än "
          f"{inst.config.data.cache_dagar} dag(ar) tas ur databasen.\n")

    try:
        hamtare = Hamtare(inst)
    except CsvFel as e:
        print(f"\nFel i en CSV-fil:\n  {e}\n", file=sys.stderr)
        return 1

    try:
        rapport = hamtare.hamta(tickers, tvinga=args.tvinga)
    finally:
        hamtare.stang()

    print(f"\nKlart: {rapport.sammanfattning()}")
    print(f"Manuell data: {rapport.csv_sammanfattning}")

    if rapport.okanda_csv_tickers:
        print(f"\nCSV-filerna nämner bolag som inte finns i universum.yaml "
              f"(troligen stavfel):")
        for t in rapport.okanda_csv_tickers:
            print(f"  {t}")

    if rapport.utan_svar:
        print(f"\n{len(rapport.utan_svar)} bolag gav inget svar alls "
              f"-- kontrollera tickern:")
        for ticker, orsak in sorted(rapport.utan_svar.items()):
            print(f"  {ticker:14} {orsak[:90]}")

    fel = rapport.bolag_med_fel()
    if fel:
        print(f"\n{len(fel)} bolag med anmärkning:")
        for ticker, meddelanden in sorted(fel.items()):
            for m in meddelanden:
                print(f"  {ticker:14} {m[:110]}")

    if not rapport.data:
        return 1

    # Täckningstabell: hur många bolag som har varje fält
    print(f"\nDatatäckning över {rapport.antal} bolag")
    print(f"  {'fält':42} {'har':>5} {'saknar':>7}")
    for falt in FALT:
        har = sum(1 for bd in rapport.data.values() if bd.har(falt))
        saknar = rapport.antal - har
        markering = "  (manuell)" if falt in MANUELLA_FALT else ""
        print(f"  {falt:42} {har:5} {saknar:7}{markering}")
    return 0


def _visa_bolag(args: argparse.Namespace) -> int:
    import datetime as dt

    from aktieanalys.data.lager import Lager

    inst = las_installningar(args.config)
    per_datum = dt.date.fromisoformat(args.datum) if args.datum else None

    with Lager(inst.databasvag) as lager:
        bd = lager.las(args.ticker, per_datum)
        if bd is None:
            print(f"{args.ticker} finns inte i databasen. Kör först:\n"
                  f"  python -m aktieanalys hamta-data --bolag {args.ticker}")
            return 1

        kategori = inst.kategori_for(args.ticker) or "(utanför universumet)"
        print(f"{args.ticker}  --  {kategori}")
        print(f"data från {bd.hamtad}"
              + (f" (läst per {per_datum})" if per_datum else ""))

        print(f"\nVärden ({bd.antal_varden})")
        for falt in sorted(bd.varden):
            varde = bd.varden[falt]
            if isinstance(varde, list):
                visning = ", ".join(f"{a}: {v:,.2f}" for a, v in varde[:5])
            elif hasattr(varde, "index"):
                visning = (f"{len(varde)} dagar, "
                           f"{varde.index[0].date()} -- {varde.index[-1].date()}")
            elif abs(varde) >= 10000:
                visning = f"{varde:,.0f}"
            else:
                visning = f"{varde:,.3f}"
            print(f"  {falt:42} {visning}")
            print(f"  {'':42} källa: {bd.kallor.get(falt, 'okänd')}")

        if bd.saknade:
            print(f"\nSaknade fält ({len(bd.saknade)})")
            for falt, orsak in sorted(bd.saknade.items()):
                print(f"  {falt:42} {orsak}")

        if kategori in inst.universum.kategorier:
            upps = inst.mattuppsattning_for(kategori)
            print(f"\nMått som {kategori} bedöms på")
            for mattnamn in upps.alla_matt():
                saknade = bd.saknade_av(MATT[mattnamn].kraver)
                if saknade:
                    print(f"  {mattnamn:42} GÅR INTE -- saknar {', '.join(saknade)}")
                else:
                    print(f"  {mattnamn:42} ok")
    return 0


def _visa_databas(args: argparse.Namespace) -> int:
    from aktieanalys.data.lager import Lager

    inst = las_installningar(args.config)
    if not inst.databasvag.exists():
        print(f"Databasen finns inte än ({inst.databasvag}).\n"
              f"Kör: python -m aktieanalys hamta-data")
        return 1
    with Lager(inst.databasvag) as lager:
        s = lager.statistik()
    print(f"Databas: {inst.databasvag}\n")
    print(f"  bolag                {s['bolag']}")
    print(f"  sparade värden       {s['varden']:,}")
    print(f"  kursrader            {s['kursrader']:,}")
    print(f"  noterade saknade     {s['saknade_varden']:,}")
    print(f"  hämtningar           {s['hamtningar']:,}")
    print(f"  historik             {s['forsta_datum']} -- {s['senaste_datum']}")
    print(f"  filstorlek           {s['storlek_kb']} kB")
    return 0


def _visa_urval(args: argparse.Namespace) -> int:
    import datetime as dt

    from aktieanalys.universum import MDR, MKR, las_och_valj

    inst = las_installningar(args.config)
    logg.satt_upp(inst.config.loggning.niva, inst.loggfil)
    datum = dt.date.fromisoformat(args.datum) if args.datum else None

    if not inst.databasvag.exists():
        print("Databasen finns inte än. Kör först: python -m aktieanalys hamta-data")
        return 1

    urval, data = las_och_valj(inst, datum)
    c = inst.config.universum

    print(f"Urval per {urval.datum}")
    print(f"  gränser: börsvärde minst {c.min_borsvarde_mdr:.0f} mdr kr, "
          f"omsättning minst {c.min_daglig_omsattning_mkr:.0f} mkr/dag")
    print(f"  utesluts vid fler än {c.max_saknade_matt_per_bolag} saknade mått")
    print(f"  {urval.sammanfattning()}\n")

    for namn, ku in urval.kategorier.items():
        rubrik = f"{namn}  --  {ku.antal} bolag av {len(inst.universum.kategorier[namn].bolag)}"
        print(rubrik)
        print("-" * len(rubrik))

        if not ku.rangordningsbar:
            print("  GÅR INTE ATT RANGORDNA: "
                  f"{ku.antal} bolag, {len(ku.anvandbara_matt)} användbara mått")

        tomma = ku.saknade_pelare(inst)
        if tomma:
            vikt = sum(inst.config.poang.vikter.som_dict()[p] for p in tomma)
            print(f"  TOMMA PELARE: {', '.join(tomma)} "
                  f"-- {vikt:.0%} av poängvikten går inte att sätta")

        print(f"  mått som används ({len(ku.anvandbara_matt)}): "
              f"{', '.join(ku.anvandbara_matt) or 'inga'}")
        for m, orsak in ku.bortvalda_matt.items():
            print(f"    BORTVALT  {m}: {orsak}")

        if ku.innehav:
            print(f"  innehav: {ku.innehav}")
            for v in ku.innehav_varningar:
                print(f"    VARNING  {v}")
        else:
            print("  innehav: (inget -- rapporten föreslår förstagångsköp)")

        if args.utforlig and ku.med:
            print("  med i urvalet:")
            rader = []
            for ticker in ku.med:
                bd = data[ticker]
                bv = bd.varde("borsvarde")
                om = bd.varde("daglig_omsattning")
                saknade = [m for m in ku.anvandbara_matt
                           if not bd.har_alla(MATT[m].kraver)]
                rader.append((bv or 0, ticker, bv, om, saknade))
            for _, ticker, bv, om, saknade in sorted(rader, reverse=True):
                bvt = f"{bv / MDR:7.1f}" if bv else "      ?"
                omt = f"{om / MKR:6.0f}" if om else "     ?"
                extra = f"  saknar: {', '.join(saknade)}" if saknade else ""
                print(f"    {ticker:14} {bvt} mdr  {omt} mkr/dag{extra}")

        if ku.uteslutna:
            print(f"  uteslutna ({len(ku.uteslutna)}):")
            for u in ku.uteslutna:
                print(f"    {u.ticker:14} {u.orsak}")
        print()

    if urval.utan_data:
        print(f"Bolag utan data ({len(urval.utan_data)}):")
        for ticker, orsak in sorted(urval.utan_data.items()):
            print(f"  {ticker:14} {orsak}")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="python -m aktieanalys",
        description="Beslutsstöd för en liten portfölj med svenska bolag. "
                    "Analyserar och rapporterar -- handlar aldrig.",
    )
    p.add_argument("--config", default="config",
                   help="katalog med konfigurationsfilerna (standard: config)")
    under = p.add_subparsers(dest="kommando", required=True)

    under.add_parser(
        "kontrollera-config",
        help="läser konfigurationen, visar den i sammanfattning och talar om vad som är fel",
    ).set_defaults(func=_kontrollera_config)

    under.add_parser(
        "visa-matt", help="listar alla nyckeltal som får användas i config.yaml"
    ).set_defaults(func=_visa_matt)

    under.add_parser(
        "visa-universum", help="visar kategorierna med bolag och mått"
    ).set_defaults(func=_visa_universum)

    p_hamta = under.add_parser(
        "hamta-data",
        help="hämtar data för bolagen och visar vad som finns och vad som saknas",
    )
    p_hamta.add_argument("--bolag", nargs="*", metavar="TICKER",
                         help="bara dessa tickers (standard: alla i universum.yaml)")
    p_hamta.add_argument("--antal", type=int, metavar="N",
                         help="bara de N första bolagen -- bra för en snabb provkörning")
    p_hamta.add_argument("--tvinga", action="store_true",
                         help="hämta om även data som är färsk i databasen")
    p_hamta.set_defaults(func=_hamta_data)

    p_bolag = under.add_parser(
        "visa-bolag", help="visar allt databasen vet om ett bolag, fält för fält"
    )
    p_bolag.add_argument("ticker")
    p_bolag.add_argument("--datum", metavar="ÅÅÅÅ-MM-DD",
                         help="visa vad vi visste ett visst datum (punkt i tiden)")
    p_bolag.set_defaults(func=_visa_bolag)

    under.add_parser(
        "visa-databas", help="statistik över databasen"
    ).set_defaults(func=_visa_databas)

    p_urval = under.add_parser(
        "visa-urval",
        help="vilka bolag som får vara med, vilka som utesluts och varför",
    )
    p_urval.add_argument("--utforlig", action="store_true",
                         help="visa börsvärde och omsättning för varje bolag")
    p_urval.add_argument("--datum", metavar="ÅÅÅÅ-MM-DD",
                         help="gör urvalet med den data som fanns ett visst datum")
    p_urval.set_defaults(func=_visa_urval)

    args = p.parse_args(argv)
    logg.satt_upp()

    try:
        return args.func(args)
    except KonfigFel as e:
        print(f"\n{e}\n", file=sys.stderr)
        print("Rätta felet i filen och kör kommandot igen.", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nAvbrutet. Det som hämtats hittills är sparat i databasen.",
              file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
