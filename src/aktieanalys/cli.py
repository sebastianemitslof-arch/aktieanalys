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


def _rensa_harlett(args: argparse.Namespace) -> int:
    """Tar bort proxyvärden som ingen härledning längre producerar.

    Arkivet skriver aldrig över ett värde med ingenting, vilket är rätt
    när datakällan missar något -- men det betyder också att en
    HÄRLEDNING som dragits tillbaka lämnar sina gamla värden kvar.
    """
    from aktieanalys.data.harledda import HARLEDNINGAR
    from aktieanalys.data.lager import Lager

    inst = las_installningar(args.config)
    logg.satt_upp(inst.config.loggning.niva, inst.loggfil)
    if not inst.databasvag.exists():
        print("Databasen finns inte än.")
        return 1

    aktuella = {h.falt for h in HARLEDNINGAR}
    with Lager(inst.databasvag) as lager:
        i_databasen = lager.harledda_falt()
        if not i_databasen:
            print("Databasen innehåller inga härledda värden.")
            return 0

        if args.falt:
            if args.falt not in i_databasen:
                print(f"{args.falt} har inga härledda värden i databasen.\n"
                      f"Fält med härledda värden: {', '.join(i_databasen)}")
                return 1
            mal = [args.falt]
        else:
            mal = [f for f in i_databasen if f not in aktuella]

        print("Härledda värden i databasen:")
        for falt, antal in i_databasen.items():
            status = "tas bort" if falt in mal else "behålls"
            print(f"  {falt:42} {antal:4} rader   {status}")

        if not mal:
            print("\nInget att rensa -- alla härledda värden har en "
                  "aktuell härledning.")
            return 0

        print()
        borttagna = 0
        for falt in mal:
            n = lager.ta_bort_harledda(falt)
            borttagna += n
            print(f"Tog bort {n} härledda värden för {falt}.")
        print(f"\nTotalt {borttagna} rader. Värden från yfinance och från "
              f"CSV-filerna är orörda.")
    return 0


def _enhet(mattnamn: str) -> str:
    """Hur ett måttvärde bäst visas."""
    if mattnamn in ("roic", "roe", "belaningsgrad", "kapitalstyrka",
                    "momentum_12m_exkl_1m", "substansvarde_tillvaxt",
                    "forvaltningsresultat_per_aktie_tillvaxt",
                    "substansrabatt_mot_historik"):
        return "procent"
    return "tal"


def _visa_varde(mattnamn: str, varde: float | None) -> str:
    if varde is None:
        return "     --"
    if _enhet(mattnamn) == "procent":
        return f"{varde * 100:6.1f}%"
    return f"{varde:7.2f}"


def _visa_poang(args: argparse.Namespace) -> int:
    import datetime as dt

    from aktieanalys.poang.modell import poangsatt
    from aktieanalys.universum import las_och_valj

    inst = las_installningar(args.config)
    logg.satt_upp(inst.config.loggning.niva, inst.loggfil)
    if not inst.databasvag.exists():
        print("Databasen finns inte än. Kör först: python -m aktieanalys hamta-data")
        return 1

    datum = dt.date.fromisoformat(args.datum) if args.datum else None
    urval, data = las_och_valj(inst, datum)
    tavla = poangsatt(inst, urval, data, datum)

    if args.bolag:
        return _visa_ett_bolags_poang(args.bolag, tavla, inst)

    print(f"Poäng per {tavla.datum}   ({tavla.sammanfattning()})")
    print("Rangordningen sker inom varje kategori. 100 = bäst i kategorin, "
          "0 = sämst.\n")

    for namn, kp in tavla.kategorier.items():
        rubrik = f"{namn}  ({len(kp.bolag)} bedömda)"
        print(rubrik)
        print("-" * len(rubrik))
        if not kp.rangordningsbar:
            print("  för få bedömbara bolag för en rangordning\n")
            continue

        print(f"  {'':3} {'bolag':14} {'poäng':>6}  "
              f"{'kval':>5} {'värd':>5} {'styr':>5} {'mom':>5}   vikt satt")
        for n, b in enumerate(kp.bolag, start=1):
            p = b.pelare
            def v(namn_: str) -> str:
                x = p[namn_].poang
                return f"{x:5.0f}" if x is not None else "   --"
            markering = " *" if b.ar_innehav else "  "
            print(f"  {n:2}.{markering}{b.ticker:14} {b.totalpoang:6.1f}  "
                  f"{v('kvalitet')} {v('vardering')} {v('finansiell_styrka')} "
                  f"{v('momentum')}   {b.andel_vikt:6.0%}")

        for b in kp.obedombara:
            print(f"   --  {b.ticker:14} obedömbart: {b.orsak}")
        if kp.innehav and not any(b.ar_innehav for b in kp.bolag + kp.obedombara):
            print(f"       innehavet {kp.innehav} finns inte i urvalet")
        for v_ in kp.innehav_varningar:
            print(f"       VARNING  {v_}")
        print()

    print("* = nuvarande innehav. "
          "Kolumnen 'vikt satt' visar hur stor del av poängvikten som "
          "vilade på mått med data.")
    print("Kör med --bolag TICKER för hela uträkningen bakom ett bolags poäng.")
    return 0


def _visa_ett_bolags_poang(ticker: str, tavla, inst) -> int:
    for kp in tavla.kategorier.values():
        for b in kp.bolag + kp.obedombara:
            if b.ticker != ticker:
                continue
            placering = kp.placering(ticker)
            print(f"{ticker}  --  {b.namn or ''}")
            print(f"kategori: {kp.namn}"
                  + (f"   placering {placering} av {len(kp.bolag)}" if placering else ""))
            if b.totalpoang is not None:
                print(f"TOTALPOÄNG: {b.totalpoang:.1f}    "
                      f"({b.andel_vikt:.0%} av vikten gick att sätta)")
            else:
                print(f"OBEDÖMBART: {b.orsak}")
            if b.ar_innehav:
                print("detta är ditt nuvarande innehav")

            for pelarnamn, p in b.pelare.items():
                poangtext = f"{p.poang:5.1f}" if p.finns else "   --"
                print(f"\n  {pelarnamn.upper()}  vikt {p.vikt:.0%}   "
                      f"poäng {poangtext}   ({p.andel_vikt:.0%} av måttvikten satt)")
                print(f"    {'mått':40} {'värde':>8} {'rank':>6} {'vikt':>6}")
                for m in p.matt:
                    if m.orsak == "måttet används inte i kategorin":
                        continue
                    ranktext = f"{m.rank:6.1f}" if m.finns else "    --"
                    vikttext = f"{m.anvand_vikt:5.0%}" if m.finns else "    0%"
                    print(f"    {m.namn:40} {_visa_varde(m.namn, m.varde)} "
                          f"{ranktext} {vikttext}")
                    if m.orsak:
                        print(f"      {m.orsak}")
            print(f"\nRanken är relativ: 100 = bäst bland de {len(kp.bolag)} "
                  f"bedömda bolagen i {kp.namn}, 0 = sämst.")
            return 0

    print(f"{ticker} finns inte i någon kategoris urval.\n"
          f"Kör 'visa-urval' för att se om bolaget uteslutits, och varför.")
    return 1


def _visa_kvalitativ(args: argparse.Namespace) -> int:
    import datetime as dt

    from aktieanalys.kvalitativ.klient import Kvalitativtlager
    from aktieanalys.kvalitativ.schema import SVENSKA
    from aktieanalys.poang.modell import poangsatt
    from aktieanalys.universum import las_och_valj

    inst = las_installningar(args.config)
    logg.satt_upp(inst.config.loggning.niva, inst.loggfil)
    if not inst.databasvag.exists():
        print("Databasen finns inte än. Kör först: python -m aktieanalys hamta-data")
        return 1

    datum = dt.date.fromisoformat(args.datum) if args.datum else None
    urval, data = las_och_valj(inst, datum)
    tavla = poangsatt(inst, urval, data, datum)
    lager = Kvalitativtlager(inst)

    # Torrkörning: visa exakt vad som skulle skickas, utan att anropa API:et
    if args.torrkor:
        valda = lager._valj_bolag(tavla)
        if args.bolag:
            valda = [b for b in valda if b.ticker == args.bolag]
            if not valda:
                print(f"{args.bolag} ingår inte i urvalet som skickas till Claude.\n"
                      f"Urvalet är topp {inst.config.kvalitativt.antal_utmanare} "
                      f"per kategori plus dina innehav.")
                return 1
        print(f"TORRKÖRNING -- inget skickas, inget kostar.\n")
        print(f"Urvalet omfattar {len(lager._valj_bolag(tavla))} bolag: "
              f"topp {inst.config.kvalitativt.antal_utmanare} per kategori "
              f"plus innehav.\n")
        for bp in valda[: 1 if args.bolag else 1]:
            bd = data.get(bp.ticker)
            nyheter = lager._nyheter(bp.ticker)
            print("=" * 70)
            print("SYSTEMPROMPT (samma för alla bolag)")
            print("=" * 70)
            from aktieanalys.kvalitativ.klient import SYSTEMPROMPT
            print(SYSTEMPROMPT.format(
                grans=inst.config.kvalitativt.max_justering * 100))
            print()
            print("=" * 70)
            print(f"UNDERLAG FÖR {bp.ticker}")
            print("=" * 70)
            print(lager._bygg_underlag(bp, bd, tavla, nyheter))
        if not args.bolag and len(valda) > 1:
            print(f"\n(visar det första av {len(valda)} bolag -- "
                  f"använd --bolag TICKER för ett annat)")
        return 0

    resultat = lager.bedom_urval(tavla, data, utan_llm=args.utan_llm)
    print(f"Kvalitativt lager: {resultat.sammanfattning()}\n")
    if not resultat.aktiverat:
        print(f"Avstängt: {resultat.orsak_avstangt}")
        return 0

    print(f"modell: {resultat.modell}")
    kostnad = resultat.tokens_in / 1e6 * 4 + resultat.tokens_ut / 1e6 * 20
    print(f"tokens: {resultat.tokens_in:,} in, {resultat.tokens_ut:,} ut "
          f"(ca {kostnad * 11:.2f} kr)\n")

    for ticker, b in sorted(resultat.bedomningar.items()):
        if not b.anvand:
            print(f"{ticker:14} ingen justering -- {b.orsak}")
            continue
        tecken = "+" if b.justering >= 0 else ""
        rad = f"{ticker:14} {tecken}{b.justering:5.1f} %"
        if b.fundamental_forsamring:
            rad += "   FUNDAMENTAL FÖRSÄMRING"
        if b.klamrad_fran is not None:
            rad += f"   (modellen föreslog {b.klamrad_fran:.1f} %, kapat)"
        print(rad)
        print(f"               {b.motivering}")
        for r in b.risker:
            print(f"               - {r.som_text()}")
        print()
    return 0


def _visa_byten(args: argparse.Namespace) -> int:
    import datetime as dt

    from aktieanalys.byten import bedom_byten, spara_veckans_poang
    from aktieanalys.data.lager import Lager
    from aktieanalys.kvalitativ.klient import Kvalitativtlager
    from aktieanalys.poang.modell import poangsatt
    from aktieanalys.universum import las_och_valj

    inst = las_installningar(args.config)
    logg.satt_upp(inst.config.loggning.niva, inst.loggfil)
    if not inst.databasvag.exists():
        print("Databasen finns inte än. Kör först: python -m aktieanalys hamta-data")
        return 1

    datum = dt.date.fromisoformat(args.datum) if args.datum else None
    urval, data = las_och_valj(inst, datum)
    tavla = poangsatt(inst, urval, data, datum)

    kvalitativt = Kvalitativtlager(inst).bedom_urval(
        tavla, data, utan_llm=args.utan_llm
    )

    with Lager(inst.databasvag) as lager:
        if args.spara:
            antal = spara_veckans_poang(lager, tavla, kvalitativt)
            print(f"Sparade veckans poäng för {antal} bolag per {tavla.datum}.\n")
        beslut = bedom_byten(inst, tavla, data, lager, kvalitativt, datum)

    c = inst.config.byten
    print(f"Bytesbedömning per {beslut.datum}")
    print(f"  krav: utmanaren {c.min_poangforbattring_procent:.0f} % bättre i "
          f"{c.antal_veckor_i_rad} rapporter i rad, innehav minst "
          f"{c.min_innehavstid_manader} mån")
    print(f"  {beslut.sammanfattning()}\n")

    for namn, kb in beslut.kategorier.items():
        rubrik = f"{namn}"
        if kb.foreslas:
            rubrik += "   >>> FÖRSLAG <<<"
        print(rubrik)
        print("-" * max(len(rubrik), 20))

        if kb.typ == "obedombar":
            print(f"  {kb.motivering}\n")
            continue

        if kb.typ == "forstagangskop":
            print(f"  inget innehav. Högst rankad: {kb.utmanare} "
                  f"({kb.utmanarpoang:.1f} poäng)")
        else:
            print(f"  innehav:  {kb.innehav:14} "
                  + (f"{kb.innehavspoang:5.1f} poäng" if kb.innehavspoang
                     else "kan inte poängsättas"))
            if kb.utmanare:
                print(f"  utmanare: {kb.utmanare:14} {kb.utmanarpoang:5.1f} poäng "
                      f"({kb.forbattring_procent:+.1f} %)")

        if kb.undantag:
            print(f"  UNDANTAG: {kb.undantag}")

        for v in kb.villkor:
            if v.undantaget and not v.uppfyllt:
                markering = "~"
            elif v.ok:
                markering = "+"
            else:
                markering = "-"
            print(f"   {markering} {v.namn:16} {v.text}")

        if kb.datum_i_rad and len(kb.datum_i_rad) > 1:
            print(f"     rapporter: "
                  + ", ".join(d.isoformat() for d in kb.datum_i_rad))

        if kb.kostnad:
            k = kb.kostnad
            kalla = "verklig position" if k.kand_positionsstorlek else "antagen position"
            print(f"   kostnad: {k.total_kr:,.0f} kr av {k.belopp_kr:,.0f} kr "
                  f"({k.total_procent:.2f} %, {kalla})")
            delar = []
            if k.courtage_salj:
                delar.append(f"sälj {k.courtage_salj:.0f} kr")
            delar.append(f"köp {k.courtage_kop:.0f} kr")
            delar.append(f"spread {k.spread_kr:.0f} kr ({k.spread_kalla})")
            print(f"            " + " + ".join(delar))
            if k.hog(inst.config.kostnader.varna_over_procent):
                print(f"   VARNING  kostnaden överstiger "
                      f"{inst.config.kostnader.varna_over_procent} % av positionen")
            if inst.config.kostnader.isk:
                print("            ISK: ingen vinstskatt vid försäljning")

        print(f"   {kb.motivering}\n")

    print("+ uppfyllt   - ej uppfyllt   ~ åsidosatt av flaggad försämring")
    if not args.spara:
        print("\nVeckans poäng sparades INTE. Kör med --spara i veckokörningen, "
              "annars kan uthållighetsregeln inte räknas nästa vecka.")
    return 0


def _veckorapport(args: argparse.Namespace) -> int:
    """Hela kedjan: hämta, poängsätt, bedöm, rapportera."""
    import datetime as dt

    from aktieanalys.byten import bedom_byten, spara_veckans_poang
    from aktieanalys.data.csv_komplettering import CsvFel
    from aktieanalys.data.hamtare import Hamtare
    from aktieanalys.data.lager import Lager
    from aktieanalys.kvalitativ.klient import Kvalitativtlager
    from aktieanalys.poang.modell import poangsatt
    from aktieanalys.rapport.bygg import bygg_rapport
    from aktieanalys.rapport.mejl import skicka
    from aktieanalys.rapport.rendera import amnesrad, rendera, spara
    from aktieanalys.universum import las_och_valj, valj_urval

    inst = las_installningar(args.config)
    logg.satt_upp(inst.config.loggning.niva, inst.loggfil)
    idag = dt.date.today()

    print(f"Veckorapport {idag}\n")

    # --- 1. hämta data -------------------------------------------------
    if args.utan_hamtning:
        print("1/5  hoppar över hämtningen, använder databasen")
        if not inst.databasvag.exists():
            print("     databasen finns inte -- kör utan --utan-hamtning")
            return 1
    else:
        print(f"1/5  hämtar data för {len(inst.alla_tickers())} bolag")
        try:
            hamtare = Hamtare(inst)
        except CsvFel as e:
            print(f"\nFel i en CSV-fil:\n  {e}\n", file=sys.stderr)
            return 1
        try:
            hamtrapport = hamtare.hamta(idag=idag)
            print(f"     {hamtrapport.sammanfattning()}")
        finally:
            hamtare.stang()

    # --- 2. urval och poäng --------------------------------------------
    print("2/5  väljer urval och poängsätter")
    urval, data = las_och_valj(inst, idag)
    tavla = poangsatt(inst, urval, data, idag)
    print(f"     {urval.sammanfattning()}")
    print(f"     {tavla.sammanfattning()}")

    # --- 3. kvalitativt lager ------------------------------------------
    print("3/5  kvalitativt lager")
    kvalitativt = Kvalitativtlager(inst).bedom_urval(
        tavla, data, utan_llm=args.utan_llm
    )
    print(f"     {kvalitativt.sammanfattning()}")

    # --- 4. bytesregler ------------------------------------------------
    print("4/5  bytesregler")
    with Lager(inst.databasvag) as lager:
        if not args.torrkor:
            antal = spara_veckans_poang(lager, tavla, kvalitativt)
            print(f"     sparade veckans poäng för {antal} bolag")
        else:
            print("     TORRKÖRNING: veckans poäng sparas inte")
        beslut = bedom_byten(inst, tavla, data, lager, kvalitativt, idag)
        print(f"     {beslut.sammanfattning()}")
        rapport = bygg_rapport(
            inst, urval, tavla, data, beslut, lager, kvalitativt,
            antal_utmanare=inst.config.kvalitativt.antal_utmanare,
        )

    # --- 5. rapporten ---------------------------------------------------
    print("5/5  bygger rapporten")
    html = rendera(rapport)
    amne = amnesrad(rapport)

    if inst.config.rapport.spara_fil and not args.torrkor:
        vag = spara(rapport, html)
        print(f"     sparad: {vag}")
    elif args.torrkor:
        from pathlib import Path
        vag = Path(args.config).resolve().parent / "rapport-torrkorning.html"
        vag.write_text(html, encoding="utf-8")
        print(f"     TORRKÖRNING, sparad som: {vag}")

    if args.utan_mejl:
        print("     mejl: avstängt med --utan-mejl")
    else:
        utskick = skicka(inst, amne, html, torrkor=args.torrkor)
        print(f"     {utskick}")

    print(f"\nKlart. {rapport.sammanfattning()}")
    print(f"Ämnesrad: {amne}")
    for f in rapport.forslag:
        if f.typ == "forstagangskop":
            print(f"  FÖRSLAG  {f.kategori}: köp {f.utmanare}")
        else:
            print(f"  FÖRSLAG  {f.kategori}: {f.innehav} -> {f.utmanare}")
    return 0


def _dumpa_databas(args: argparse.Namespace) -> int:
    from aktieanalys.data.lager import Lager

    inst = las_installningar(args.config)
    logg.satt_upp(inst.config.loggning.niva, inst.loggfil)
    if not inst.databasvag.exists():
        print(f"Databasen finns inte: {inst.databasvag}")
        return 1
    vag = inst.rotkatalog / args.fil
    with Lager(inst.databasvag) as lager:
        rader = lager.dumpa(vag)
    storlek = vag.stat().st_size / 1_048_576
    print(f"Dumpade {rader:,} rader till {vag} ({storlek:.1f} MB).")
    print("Dumpen är sorterad, så varje ny vecka blir ett litet tillägg "
          "i filen i stället för en helt ny version.")
    return 0


def _aterskapa_databas(args: argparse.Namespace) -> int:
    from aktieanalys.data.lager import aterskapa

    inst = las_installningar(args.config)
    logg.satt_upp(inst.config.loggning.niva, inst.loggfil)
    vag = inst.rotkatalog / args.fil
    if not vag.exists():
        print(f"Ingen dump att återskapa från: {vag}")
        return 1
    if inst.databasvag.exists() and not args.tvinga:
        print(f"{inst.databasvag} finns redan. Kör med --tvinga för att "
              f"ersätta den med innehållet i dumpen.")
        return 1
    antal = aterskapa(vag, inst.databasvag)
    print(f"Återskapade {inst.databasvag} ur {vag.name} "
          f"({antal:,} fundamentavärden).")
    return 0


def _radbryt(text: str, bredd: int) -> list[str]:
    import textwrap

    return textwrap.wrap(text, bredd)


def _backtest(args: argparse.Namespace) -> int:
    import datetime as dt

    from aktieanalys.backtest import kor_backtest
    from aktieanalys.data.lager import Lager

    inst = las_installningar(args.config)
    logg.satt_upp(inst.config.loggning.niva, inst.loggfil)
    if not inst.databasvag.exists():
        print("Databasen finns inte än. Kör först: python -m aktieanalys hamta-data")
        return 1

    b = inst.config.backtest
    start = dt.date.fromisoformat(args.fran) if args.fran else b.start
    slut = dt.date.fromisoformat(args.till) if args.till else dt.date.today()

    print("=" * 68)
    print("LÄS DET HÄR FÖRST")
    print("=" * 68)
    print("Backtestet är en rimlighetskontroll av reglerna, inte ett bevis")
    print("för att de fungerar. Antalet beslut är för litet för att säga")
    print("något statistiskt, och flera antaganden ligger under siffrorna.")
    print("Varningarna listas i sin helhet efter resultatet.\n")

    with Lager(inst.databasvag) as lager:
        data = {}
        for ticker in inst.alla_tickers():
            bd = lager.las(ticker, dt.date.today())
            if bd is not None:
                data[ticker] = bd
        print(f"Kör {start} till {slut} med {len(data)} bolag. "
              f"Det tar en stund.\n")
        resultat = kor_backtest(
            inst, data, lager, start, slut,
            startkapital=b.startkapital_kr,
            fordrojning_manader=b.rapportfordrojning_manader,
            jamforelseindex=b.jamforelseindex,
        )

    print("\n" + "=" * 68)
    print("RESULTAT")
    print("=" * 68)
    print(f"Period            {resultat.start} till {resultat.slut} "
          f"({resultat.ar:.1f} år)")
    print(f"Startkapital      {resultat.startkapital:>12,.0f} kr"
          .replace(",", " "))
    print(f"Slutvärde         {resultat.slutvarde:>12,.0f} kr"
          .replace(",", " "))
    print(f"Avkastning        {resultat.avkastning:>12.1f} %   "
          f"({resultat.arsavkastning:.1f} % per år)")
    print(f"Handelskostnader  {resultat.total_kostnad():>12,.0f} kr"
          .replace(",", " "))

    print("\nJÄMFÖRELSE")
    for namn in resultat.index:
        avk = resultat.indexavkastning(namn)
        if avk is None:
            continue
        skillnad = resultat.avkastning - avk
        print(f"  {namn:36} {avk:7.1f} %   "
              f"portföljen {skillnad:+.1f} procentenheter")
    if not resultat.index:
        print("  inga index kunde hämtas")

    if resultat.slump:
        s = resultat.slump
        print("\n" + "-" * 68)
        print("ÄR DET HÄR SKILLT FRÅN TUR?")
        print("-" * 68)
        antal_text = f"{s.antal:,}".replace(",", "\u00a0")
        print(f"  {antal_text} portföljer valda på måfå, en aktie per")
        print(f"  kategori, hållna hela perioden:")
        print(f"    median                 {s.median:7.1f} %")
        print(f"    5:e till 95:e percentilen   {s.percentil_5:.1f} % "
              f"till {s.percentil_95:.1f} %")
        if s.likaviktad_korg is not None:
            print(f"    likaviktad korg av alla bolag  {s.likaviktad_korg:.1f} %")
        print()
        for rad in _radbryt(s.omdome(), 66):
            print(f"  {rad}")
        print("-" * 68)

    print("\nOMSÄTTNING PER ÅR")
    print(f"  {'år':6} {'byten':>6} {'köp':>5} {'omsättning':>12} {'kostnad':>10}")
    for ar, rad in resultat.per_ar().items():
        print(f"  {ar:6} {rad['byten']:6.0f} {rad['kop']:5.0f} "
              f"{rad['omsattning_procent']:11.1f} % {rad['kostnad']:9.0f} kr")

    if resultat.affarer:
        print(f"\nAFFÄRER ({len(resultat.affarer)})")
        for a in resultat.affarer:
            print(f"  {a.datum}  {a.typ:4} {a.ticker:14} {a.kategori:16} "
                  f"{a.belopp:9,.0f} kr  kostnad {a.kostnad:6.0f} kr"
                  .replace(",", " "))
    else:
        print("\nInga affärer genomfördes under perioden.")

    print("\n" + "=" * 68)
    print("VARNINGAR -- läs dem innan du drar slutsatser")
    print("=" * 68)
    for v in resultat.varningar:
        print(f"  * {v}")
    print("\n  Kursdata är äkta historisk. Det arkiv du bygger upp från och")
    print("  med nu blir med tiden den tillförlitliga källan -- om några år")
    print("  går det här att göra på riktigt.")
    return 0


def _hitta_universum(args: argparse.Namespace) -> int:
    from aktieanalys.hitta import sok_bolag, yaml_forslag

    inst = las_installningar(args.config)
    logg.satt_upp(inst.config.loggning.niva, inst.loggfil)
    c = inst.config.universum

    print(f"Söker svenska bolag över {c.min_borsvarde_mdr:.0f} mdr kr i "
          f"börsvärde och {c.min_daglig_omsattning_mkr:.0f} mkr/dag.\n")
    resultat = sok_bolag(inst, hamta_sektor=not args.snabb)

    if resultat.fel:
        print(f"Sökningen misslyckades: {resultat.fel}")
        return 1

    krockar = [k for k in resultat.nya if k.aktieslagskrock]
    print(f"Yahoo gav {resultat.traffar} bolag över börsvärdesgränsen.")
    print(f"{len(resultat.kandidater)} klarar även likviditetsgränsen.")
    print(f"  {len(resultat.befintliga)} finns redan i universum.yaml")
    print(f"  {len(resultat.nya)} är NYA, varav {len(krockar)} är ett annat")
    print(f"      aktieslag av ett bolag som redan finns\n")

    saknas = sorted(set(inst.alla_tickers())
                    - {k.ticker for k in resultat.kandidater})
    if saknas:
        print(f"Bolag i din lista som screenern INTE hittade ({len(saknas)}):")
        print(f"  {', '.join(saknas)}")
        print("  (för små, för tunt handlade, eller inte i Yahoos urval)\n")

    if not resultat.nya:
        print("Inget nytt att lägga till.")
        return 0

    for kategori, bolag in sorted(resultat.per_kategori().items()):
        print(f"{kategori}  ({len(bolag)} nya)")
        for k in bolag:
            rad = (f"  {k.ticker:14} {k.namn[:32]:33} "
                   f"{k.borsvarde_mdr:6.0f} mdr {k.omsattning_mkr:6.0f} mkr/dag")
            if k.aktieslagskrock:
                rad += "  [ANNAT AKTIESLAG]"
            print(rad)
            if k.aktieslagskrock:
                print(f"  {'':14} samma bolag som {k.aktieslagskrock} "
                      f"-- ta bara med ETT aktieslag")
            if k.anmarkning:
                for bit in _radbryt(f"KONTROLLERA: {k.anmarkning}", 60):
                    print(f"  {'':14} {bit}")
        print()

    if args.yaml:
        print("=" * 68)
        print("ATT KLISTRA IN I universum.yaml")
        print("=" * 68)
        print(yaml_forslag(resultat))
    else:
        print("Kör med --yaml för ett färdigt block att klistra in i "
              "universum.yaml.")

    print("\nLäs igenom innan du lägger till något. Kategoriindelningen "
          "bygger på\nYahoos sektorer, som är för grova för bank och "
          "finans: där ryms både\nstorbanker, försäkringsbolag och "
          "investmentbolag, som ska bedömas på\nhelt olika mått.")
    return 0


def _utvardera(args: argparse.Namespace) -> int:
    import datetime as dt

    from aktieanalys.data.lager import Lager
    from aktieanalys.utvardering import (
        mat_information_coefficient,
        mat_rangstabilitet,
    )

    inst = las_installningar(args.config)
    logg.satt_upp(inst.config.loggning.niva, inst.loggfil)
    if not inst.databasvag.exists():
        print("Databasen finns inte än. Kör först: python -m aktieanalys hamta-data")
        return 1

    b = inst.config.backtest
    start = dt.date.fromisoformat(args.fran) if args.fran else b.start
    slut = dt.date.fromisoformat(args.till) if args.till else dt.date.today()

    print("=" * 68)
    print("HAR POÄNGEN NÅGOT SAMBAND MED EFTERFÖLJANDE AVKASTNING?")
    print("=" * 68)
    print("Backtestet mäter portföljen och domineras av slump. Det här")
    print("mäter rangordningen i stället, med alla bolag vid varje")
    print("tidpunkt -- hundra gånger fler datapunkter.\n")

    with Lager(inst.databasvag) as lager:
        data = {}
        for ticker in inst.alla_tickers():
            bd = lager.las(ticker, dt.date.today())
            if bd is not None:
                data[ticker] = bd

    print(f"Mäter {start} till {slut}, {args.horisont} dagars horisont, "
          f"{len(data)} bolag.\nDet tar en stund.\n")

    resultat = mat_information_coefficient(
        inst, data, start, slut,
        horisont_dagar=args.horisont, steg_dagar=args.steg,
        fordrojning_manader=b.rapportfordrojning_manader,
    )

    # Hur mycket rör sig rangordningen? Avgör hur många av perioderna
    # som verkligen är oberoende vad.
    import datetime as _dt

    mitten = start + (slut - start) / 2
    if (mitten - start).days > 300:
        resultat.stabilitet = mat_rangstabilitet(
            inst, data, start, start + _dt.timedelta(days=365),
            fordrojning_manader=b.rapportfordrojning_manader,
        )

    if not resultat.perioder:
        print("Inga perioder kunde mätas. Historiken räcker inte.")
        for v in resultat.varningar:
            print(f"  * {v}")
        return 1

    alla = resultat.sammanfatta()
    oberoende = resultat.sammanfatta(resultat.oberoende)

    print("=" * 68)
    print("RESULTAT")
    print("=" * 68)
    print(f"  {'':28} {'överlappande':>14} {'oberoende':>12}")
    print(f"  {'antal perioder':28} {alla['antal']:>14} {oberoende['antal']:>12}")
    print(f"  {'genomsnittlig IC':28} {alla['medel']:>14.4f} "
          f"{oberoende.get('medel', 0):>12.4f}")
    print(f"  {'andel perioder över noll':28} {alla['andel_positiva']:>13.0f} % "
          f"{oberoende.get('andel_positiva', 0):>11.0f} %")
    if alla["antal"] > 1:
        print(f"  {'t-värde (uppblåst)':28} {alla['t']:>14.2f} "
              f"{oberoende.get('t', 0):>12.2f}")

    band = resultat.nollband()
    if band:
        print("\nVAD GER REN SLUMP I SAMMA UPPSTÄLLNING?")
        print(f"  {'slumpad poäng, medel':28} {band[0]:>14.4f}")
        print(f"  {'5:e till 95:e percentilen':28} "
              f"{band[1]:>7.4f} till {band[2]:.4f}")
        if resultat.inom_bruset():
            print("  Det uppmätta värdet ligger INOM detta band.")

    if resultat.stabilitet:
        print("\nRÖR SIG RANGORDNINGEN MELLAN ÅREN?")
        for kategori, varde in sorted(resultat.stabilitet.items(),
                                      key=lambda x: -x[1]):
            etikett = "nästan oförändrad" if varde > 0.8 else (
                "trögrörlig" if varde > 0.5 else "rörlig")
            print(f"  {kategori:22} {varde:+7.2f}   {etikett}")
        snitt = resultat.genomsnittlig_stabilitet()
        print(f"  {'genomsnitt':22} {snitt:+7.2f}")

    per_pelare = resultat.per_pelare()
    if per_pelare:
        print("\nVILKEN FAKTOR BAR INFORMATIONEN?")
        print(f"  {'pelare':22} {'IC':>8} {'över noll':>12}")
        for pelare, rad in sorted(per_pelare.items(),
                                  key=lambda x: -x[1]["medel"]):
            print(f"  {pelare:22} {rad['medel']:>8.4f} "
                  f"{rad['andel_positiva']:>10.0f} %")

    print("\n" + "-" * 68)
    for rad in _radbryt(resultat.omdome(), 66):
        print(f"  {rad}")
    print("-" * 68)

    print("\nTOLKNING AV SKALAN")
    print("  IC omkring 0      ingen information alls")
    print("  IC 0,02 - 0,05    svagt men användbart, vanligt för riktiga modeller")
    print("  IC över 0,10      ovanligt starkt, misstänk ett fel i uppställningen")

    print("\nVARNINGAR")
    for v in resultat.varningar:
        for rad in _radbryt(v, 64):
            print(f"  {rad}" if v.startswith(rad) else f"    {rad}")
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

    p_rensa = under.add_parser(
        "rensa-harlett",
        help="tar bort proxyvärden som ingen härledning längre producerar",
    )
    p_rensa.add_argument("--falt", metavar="FÄLT",
                         help="rensa ett bestämt fält, t.ex. när en "
                              "härlednings formel ändrats")
    p_rensa.set_defaults(func=_rensa_harlett)

    p_poang = under.add_parser(
        "visa-poang",
        help="rangordningen per kategori, och hela uträkningen bakom ett bolags poäng",
    )
    p_poang.add_argument("--bolag", metavar="TICKER",
                         help="visa varje mått, värde, rank och vikt för ett bolag")
    p_poang.add_argument("--datum", metavar="ÅÅÅÅ-MM-DD",
                         help="poängsätt med den data som fanns ett visst datum")
    p_poang.set_defaults(func=_visa_poang)

    p_kval = under.add_parser(
        "visa-kvalitativ",
        help="Claudes riskflaggor och poängjusteringar för topp 3 plus innehav",
    )
    p_kval.add_argument("--torrkor", action="store_true",
                        help="visa exakt vad som skulle skickas, utan att "
                             "anropa API:et (kostar ingenting)")
    p_kval.add_argument("--bolag", metavar="TICKER",
                        help="med --torrkor: visa underlaget för ett bestämt bolag")
    p_kval.add_argument("--utan-llm", action="store_true", dest="utan_llm",
                        help="hoppa över det kvalitativa lagret helt")
    p_kval.add_argument("--datum", metavar="ÅÅÅÅ-MM-DD")
    p_kval.set_defaults(func=_visa_kvalitativ)

    p_byten = under.add_parser(
        "visa-byten",
        help="bytesreglerna: vilka villkor som håller och vilka som inte gör det",
    )
    p_byten.add_argument("--spara", action="store_true",
                         help="skriv veckans poäng till databasen "
                              "(krävs för uthållighetsregeln)")
    p_byten.add_argument("--utan-llm", action="store_true", dest="utan_llm",
                         help="hoppa över det kvalitativa lagret")
    p_byten.add_argument("--datum", metavar="ÅÅÅÅ-MM-DD")
    p_byten.set_defaults(func=_visa_byten)

    p_vecka = under.add_parser(
        "veckorapport",
        help="kör hela kedjan och skapar veckorapporten (detta är "
             "kommandot som schemaläggs)",
    )
    p_vecka.add_argument("--utan-hamtning", action="store_true",
                         dest="utan_hamtning",
                         help="använd databasen som den är, hämta inget nytt")
    p_vecka.add_argument("--utan-llm", action="store_true", dest="utan_llm",
                         help="hoppa över det kvalitativa lagret")
    p_vecka.add_argument("--utan-mejl", action="store_true", dest="utan_mejl",
                         help="skapa rapporten men skicka inget mejl")
    p_vecka.add_argument("--torrkor", action="store_true",
                         help="ändra ingenting: spara varken veckopoäng, "
                              "rapportfil eller mejl")
    p_vecka.set_defaults(func=_veckorapport)

    p_dump = under.add_parser(
        "dumpa-databas",
        help="skriver databasen som sorterad SQL-text (det som versionshanteras)",
    )
    p_dump.add_argument("--fil", default="data/cache.sql")
    p_dump.set_defaults(func=_dumpa_databas)

    p_ater = under.add_parser(
        "aterskapa-databas", help="bygger upp databasen ur textdumpen",
    )
    p_ater.add_argument("--fil", default="data/cache.sql")
    p_ater.add_argument("--tvinga", action="store_true",
                        help="ersätt en befintlig databasfil")
    p_ater.set_defaults(func=_aterskapa_databas)

    p_back = under.add_parser(
        "backtest",
        help="kör reglerna historiskt (läs varningarna -- det är en "
             "rimlighetskontroll, inte ett bevis)",
    )
    p_back.add_argument("--fran", metavar="ÅÅÅÅ-MM-DD",
                        help="startdatum (standard: backtest.start i config)")
    p_back.add_argument("--till", metavar="ÅÅÅÅ-MM-DD", help="slutdatum")
    p_back.set_defaults(func=_backtest)

    p_hitta = under.add_parser(
        "hitta-universum",
        help="söker upp svenska bolag som klarar dina gränser, i stället "
             "för att de ska skrivas in för hand",
    )
    p_hitta.add_argument("--yaml", action="store_true",
                         help="skriv ut ett färdigt block för universum.yaml")
    p_hitta.add_argument("--snabb", action="store_true",
                         help="hoppa över sektorhämtningen (ingen "
                              "kategoriindelning, men går på sekunder)")
    p_hitta.set_defaults(func=_hitta_universum)

    p_utv = under.add_parser(
        "utvardera",
        help="mäter om poängen har något samband med efterföljande "
             "avkastning (fler datapunkter än backtestet)",
    )
    p_utv.add_argument("--fran", metavar="ÅÅÅÅ-MM-DD")
    p_utv.add_argument("--till", metavar="ÅÅÅÅ-MM-DD")
    p_utv.add_argument("--horisont", type=int, default=365, metavar="DAGAR",
                       help="hur långt fram avkastningen mäts (standard 365)")
    p_utv.add_argument("--steg", type=int, default=30, metavar="DAGAR",
                       help="hur ofta mätningen görs (standard var 30:e dag)")
    p_utv.set_defaults(func=_utvardera)

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
