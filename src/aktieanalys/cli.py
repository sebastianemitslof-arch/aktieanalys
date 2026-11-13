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

    args = p.parse_args(argv)
    logg.satt_upp()

    try:
        return args.func(args)
    except KonfigFel as e:
        print(f"\n{e}\n", file=sys.stderr)
        print("Rätta felet i filen och kör kommandot igen.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
