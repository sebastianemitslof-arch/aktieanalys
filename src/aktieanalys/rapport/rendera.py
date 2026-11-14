"""Gör om rapportinnehållet till HTML, och sparar det som fil."""

from __future__ import annotations

import datetime as dt
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from aktieanalys.konfig import Installningar
from aktieanalys.logg import hamta
from aktieanalys.rapport.bygg import Veckorapport
from aktieanalys.text import KORTA, MANADER, datum_text, manad_dag

logg = hamta(__name__)




def nasta_beslutsfonster(inst: Installningar, datum: dt.date) -> str:
    """När nästa beslutsfönster öppnar, i klartext."""
    fonster = inst.config.byten.beslutsfonster
    if not fonster:
        return "inga fönster konfigurerade"

    idag = datum.strftime("%m-%d")
    kommande = sorted(f.fran for f in fonster if f.fran > idag)
    if kommande:
        return manad_dag(kommande[0])
    return f"{manad_dag(sorted(f.fran for f in fonster)[0])} {datum.year + 1}"


def rendera(rapport: Veckorapport) -> str:
    """Bygger HTML-sidan."""
    inst = rapport.installningar
    miljo = Environment(
        loader=FileSystemLoader(Path(__file__).parent),
        autoescape=select_autoescape(["html", "j2"]),
        trim_blocks=True,
        lstrip_blocks=True,
    )
    # strftime ger engelska månadsnamn beroende på systemets språkinställning
    miljo.filters["svenskt_datum"] = datum_text
    mall = miljo.get_template("mall.html.j2")

    html = mall.render(
        r=rapport,
        nasta_fonster=nasta_beslutsfonster(inst, rapport.datum) if inst else "",
        isk=inst.config.kostnader.isk if inst else True,
        varna_over=inst.config.kostnader.varna_over_procent if inst else 1.0,
    )
    logg.debug("renderade %d tecken HTML", len(html))
    return html


def spara(rapport: Veckorapport, html: str) -> Path:
    """Sparar rapporten som rapporter/ÅÅÅÅ-MM-DD.html."""
    katalog = rapport.installningar.rapportkatalog
    katalog.mkdir(parents=True, exist_ok=True)
    vag = katalog / f"{rapport.datum.isoformat()}.html"
    vag.write_text(html, encoding="utf-8")
    logg.info("sparade rapporten i %s", vag)
    return vag


def amnesrad(rapport: Veckorapport) -> str:
    """Mejlets ämnesrad. Det viktigaste först, så det syns i notisen."""
    datum = f"{rapport.datum.day} {KORTA[rapport.datum.month - 1]}"
    if rapport.forslag:
        kategorier = ", ".join(f.kategori for f in rapport.forslag)
        return f"Aktieanalys {datum}: {len(rapport.forslag)} förslag ({kategorier})"
    forsamringar = (
        rapport.kvalitativt.forsamringar() if rapport.kvalitativt else []
    )
    if forsamringar:
        return (
            f"Aktieanalys {datum}: fundamental försämring flaggad "
            f"({', '.join(forsamringar)})"
        )
    if rapport.riskflaggor:
        return (
            f"Aktieanalys {datum}: bevakning, "
            f"{len(rapport.riskflaggor)} riskflagg(or)"
        )
    return f"Aktieanalys {datum}: bevakning, inget att agera på"
