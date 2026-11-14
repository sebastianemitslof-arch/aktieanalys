"""Skickar veckorapporten som HTML-mejl.

Lösenordet läses ur miljövariabeln SMTP_LOSENORD, aldrig ur
config.yaml. För Gmail behövs ett app-specifikt lösenord: vanliga
kontolösenord fungerar inte mot SMTP.

Går utskicket fel är rapporten ändå sparad som fil. Ett mejlfel får
inte göra veckans analys oläsbar.
"""

from __future__ import annotations

import os
import smtplib
import ssl
from dataclasses import dataclass
from email.message import EmailMessage

from aktieanalys.konfig import Installningar
from aktieanalys.logg import hamta

logg = hamta(__name__)

LOSENORD_MILJOVARIABEL = "SMTP_LOSENORD"


@dataclass
class Utskick:
    skickat: bool
    mottagare: str = ""
    orsak: str | None = None

    def __str__(self) -> str:
        if self.skickat:
            return f"mejl skickat till {self.mottagare}"
        return f"inget mejl skickat: {self.orsak}"


def _saknas(inst: Installningar) -> str | None:
    """Vad som fattas för att kunna skicka. None betyder att allt finns."""
    m = inst.config.rapport.mejl
    if not inst.config.rapport.skicka_mejl:
        return "avstängt i config.yaml (rapport.skicka_mejl)"
    if not m.till:
        return "ingen mottagare i config.yaml (rapport.mejl.till)"
    if not m.fran:
        return "ingen avsändare i config.yaml (rapport.mejl.fran)"
    if not m.smtp_server:
        return "ingen SMTP-server i config.yaml"
    if not os.environ.get(LOSENORD_MILJOVARIABEL):
        return (
            f"{LOSENORD_MILJOVARIABEL} är inte satt. Lägg lösenordet i "
            f".env-filen. För Gmail krävs ett app-specifikt lösenord."
        )
    return None


def skicka(
    inst: Installningar, amne: str, html: str, torrkor: bool = False
) -> Utskick:
    """Skickar rapporten. Höjer aldrig undantag."""
    from aktieanalys.kvalitativ.klient import las_env_fil

    las_env_fil(inst.rotkatalog / ".env")

    orsak = _saknas(inst)
    if orsak:
        logg.info("inget mejl: %s", orsak)
        return Utskick(False, orsak=orsak)

    m = inst.config.rapport.mejl
    if torrkor:
        return Utskick(False, m.till, "torrkörning -- inget skickades")

    brev = EmailMessage()
    brev["Subject"] = amne
    brev["From"] = m.fran
    brev["To"] = m.till
    brev.set_content(
        "Den här rapporten är formaterad som HTML. "
        "Öppna den i en mejlklient som visar HTML, eller läs filen i "
        "mappen rapporter/."
    )
    brev.add_alternative(html, subtype="html")

    try:
        kontext = ssl.create_default_context()
        if m.smtp_port == 465:
            with smtplib.SMTP_SSL(m.smtp_server, m.smtp_port, context=kontext,
                                  timeout=60) as server:
                server.login(m.fran, os.environ[LOSENORD_MILJOVARIABEL])
                server.send_message(brev)
        else:
            with smtplib.SMTP(m.smtp_server, m.smtp_port, timeout=60) as server:
                server.starttls(context=kontext)
                server.login(m.fran, os.environ[LOSENORD_MILJOVARIABEL])
                server.send_message(brev)
    except smtplib.SMTPAuthenticationError:
        return Utskick(
            False, m.till,
            "inloggningen avvisades. För Gmail krävs ett app-specifikt "
            "lösenord, inte kontots vanliga lösenord.",
        )
    except smtplib.SMTPException as e:
        logg.warning("mejlfel: %s", e)
        return Utskick(False, m.till, f"{type(e).__name__}: {e}")
    except OSError as e:
        logg.warning("nätverksfel vid mejlutskick: %s", e)
        return Utskick(False, m.till, f"nätverksfel: {e}")

    logg.info("mejl skickat till %s", m.till)
    return Utskick(True, m.till)
