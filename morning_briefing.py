#!/usr/bin/env python3
"""
Kicktipp Morning Briefing
Läuft täglich um 8:00 Uhr via GitHub Actions.
Generiert eine HTML-Mail mit WM-News + Tipprunden-Highlights per Claude API.
"""

import json, os, smtplib, re
from datetime import datetime, timezone, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from urllib.request import urlopen, Request
from urllib.parse import urlencode
from urllib.error import URLError

# ── Konfiguration aus GitHub Secrets ────────────────────────────
ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]
GMAIL_USER        = os.environ["GMAIL_USER"]        # deine@gmail.com
GMAIL_APP_PW      = os.environ["GMAIL_APP_PW"]      # 16-stelliges App-Passwort
# Komma-getrennte Empfänger
EMPFAENGER        = os.environ["BRIEFING_EMPFAENGER"].split(",")
COMMUNITY         = os.environ.get("KICKTIPP_COMMUNITY", "stb-tipprunde")
DATEN_FILE        = "kicktipp_daten.json"


def lade_kicktipp_daten():
    if not os.path.exists(DATEN_FILE):
        return None
    with open(DATEN_FILE, encoding="utf-8") as f:
        return json.load(f)


def kumuliert(daten, name, bis_st_idx):
    """Punkte eines Spielers bis einschließlich Spieltag-Index."""
    total = 0
    for i, st in enumerate(daten["spieltage"]):
        if i > bis_st_idx:
            break
        for sp in st["spiele"]:
            if not sp["abgeschlossen"]:
                continue
            p = next((x for x in st["spieler"] if x["name"] == name), None)
            if p:
                total += p["punkte_pro_spiel"].get(str(sp["col_idx"]), 0)
    return total


def erstelle_kontext(daten):
    """Bereitet die Kicktipp-Daten als Text-Kontext für Claude auf."""
    if not daten or not daten.get("spieltage"):
        return "Noch keine Daten verfügbar."

    spieltage = daten["spieltage"]
    namen = list({p["name"] for st in spieltage for p in st["spieler"]})
    letzter_idx = len(spieltage) - 1

    # Aktuelle Rangliste
    rangliste = sorted(
        [{"name": n, "pts": kumuliert(daten, n, letzter_idx)} for n in namen],
        key=lambda x: -x["pts"]
    )

    # Spiele der letzten 24h
    heute = datetime.now(timezone.utc)
    gestern_spiele = []
    for st in spieltage:
        for sp in st["spiele"]:
            if not sp["abgeschlossen"]:
                continue
            # Alle abgeschlossenen Spiele des letzten Spieltags nehmen
            # (Timestamp nicht verfügbar, nehmen letzten Spieltag)
            gestern_spiele.append({
                "label": sp["label"],
                "ergebnis": sp["ergebnis"],
                "spieltag": st["name"],
            })

    # Nur Spiele des aktuellsten Spieltags
    if spieltage:
        letzter_st = spieltage[letzter_idx]
        neueste_spiele = [
            {"label": sp["label"], "ergebnis": sp["ergebnis"]}
            for sp in letzter_st["spiele"] if sp["abgeschlossen"]
        ]
        spieltag_name = letzter_st["name"]

        # Spieltagssieger
        spieltag_punkte = []
        for sp_data in letzter_st["spieler"]:
            pts = sum(
                sp_data["punkte_pro_spiel"].get(str(sp["col_idx"]), 0)
                for sp in letzter_st["spiele"] if sp["abgeschlossen"]
            )
            spieltag_punkte.append({"name": sp_data["name"], "pts": pts})
        spieltag_punkte.sort(key=lambda x: -x["pts"])
        tagessieger = spieltag_punkte[0] if spieltag_punkte else None
    else:
        neueste_spiele = []
        spieltag_name = ""
        tagessieger = None

    kontext = f"""
AKTUELLE RANGLISTE ({COMMUNITY}):
""" + "\n".join(f"{i+1}. {r['name']} – {r['pts']} Punkte" for i, r in enumerate(rangliste))

    kontext += f"\n\nLETZTER SPIELTAG: {spieltag_name}"
    kontext += "\nSPIELE:\n" + "\n".join(
        f"  {s['label']} → {s['ergebnis']}" for s in neueste_spiele
    )

    if tagessieger:
        kontext += f"\n\nSPIELTAGSSIEGER: {tagessieger['name']} mit {tagessieger['pts']} Punkten"

    n = len(namen)
    topf = n * 20
    kontext += f"\n\nPREISGELD: {topf}€ Topf ({n} Spieler × 20€)"
    kontext += f"\n  1. Platz: {int(topf*0.5)}€ → {rangliste[0]['name']}"
    kontext += f"\n  2. Platz: {int(topf*0.3)}€ → {rangliste[1]['name']}"
    kontext += f"\n  3. Platz: {int(topf*0.2)}€ → {rangliste[2]['name']}"
    kontext += f"\n  Grill-Pflicht: {rangliste[-3]['name']}, {rangliste[-2]['name']}, {rangliste[-1]['name']}"

    return kontext


def claude_generiere_mail(kontext):
    """Ruft Claude API auf und lässt das Briefing generieren."""
    heute = datetime.now(timezone(timedelta(hours=2)))  # MESZ
    datum = heute.strftime("%A, %d. %B %Y").replace(
        "Monday","Montag").replace("Tuesday","Dienstag").replace(
        "Wednesday","Mittwoch").replace("Thursday","Donnerstag").replace(
        "Friday","Freitag").replace("Saturday","Samstag").replace(
        "Sunday","Sonntag").replace("January","Januar").replace(
        "February","Februar").replace("March","März").replace(
        "April","April").replace("May","Mai").replace("June","Juni").replace(
        "July","Juli").replace("August","August").replace(
        "September","September").replace("October","Oktober").replace(
        "November","November").replace("December","Dezember")

    prompt = f"""Du bist der Moderator der Kicktipp-Tipprunde "STB-Tipprunde" bei der Fußball-WM 2026.
Schreibe ein tägliches Morning Briefing für die Gruppe. Heute ist {datum}.

Hier sind die aktuellen Daten der Tipprunde:
{kontext}

Schreibe eine lockere, witzige, motivierende Nachricht auf Deutsch.
Nutze Emojis. Hebe Highlights hervor, mache Witze über schlechte Tipper,
lobe gute Tipper, kommentiere die Tabellensituation dramatisch.
Erwähne das Preisgeld und die Grill-Pflicht.

Gib NUR valides HTML zurück – einen kompletten E-Mail-Body (kein <html>/<head>, nur den <body>-Inhalt).
Nutze Inline-CSS. Dunkles Design: Hintergrund #111, Text #f0f0f0, Akzent #c01c00 (Kicktipp-Rot).
Max. 400 Wörter. Struktur: Begrüßung → Tabelle → Highlights → Ausblick → Spruch."""

    import json as _json
    body = _json.dumps({
        "model": "claude-sonnet-4-6",
        "max_tokens": 1500,
        "messages": [{"role": "user", "content": prompt}]
    }).encode()

    req = Request(
        "https://api.anthropic.com/v1/messages",
        data=body,
        headers={
            "Content-Type": "application/json",
            "x-api-key": ANTHROPIC_API_KEY,
            "anthropic-version": "2023-06-01",
        },
        method="POST"
    )
    with urlopen(req) as resp:
        result = _json.loads(resp.read())
    return result["content"][0]["text"]


def sende_mail(html_body):
    heute = datetime.now(timezone(timedelta(hours=2)))
    datum = heute.strftime("%d.%m.%Y")

    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"☀️ Kicktipp Morning Briefing – {datum}"
    msg["From"]    = f"Kicktipp Bot <{GMAIL_USER}>"
    msg["To"]      = ", ".join(EMPFAENGER)

    # Komplette HTML-Mail
    html_full = f"""<!DOCTYPE html>
<html lang="de">
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body style="margin:0;padding:0;background:#111;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;">
  <div style="max-width:600px;margin:0 auto;padding:20px;">
    <div style="background:#c01c00;border-radius:10px 10px 0 0;padding:16px 20px;display:flex;align-items:center;gap:10px;">
      <span style="font-size:1.2rem;font-weight:800;color:#fff;letter-spacing:-.02em;">kicktipp</span>
      <span style="color:rgba(255,255,255,.6);font-size:.85rem;">· STB-Tipprunde · WM 2026</span>
    </div>
    <div style="background:#1a1a1a;border:1px solid #333;border-top:none;border-radius:0 0 10px 10px;padding:24px;">
      {html_body}
    </div>
    <div style="text-align:center;padding:16px;color:#555;font-size:.72rem;">
      Automatisch generiert · kicktipp-wm · {datum}
    </div>
  </div>
</body>
</html>"""

    msg.attach(MIMEText(html_full, "html", "utf-8"))

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:
        smtp.login(GMAIL_USER, GMAIL_APP_PW)
        smtp.sendmail(GMAIL_USER, EMPFAENGER, msg.as_bytes())

    print(f"✓ Mail gesendet an {len(EMPFAENGER)} Empfänger")


def main():
    print("☀️  Kicktipp Morning Briefing")
    print("=" * 40)

    daten   = lade_kicktipp_daten()
    kontext = erstelle_kontext(daten)
    print("Kontext erstellt:")
    print(kontext[:500] + "...")

    print("\nGeneriere Briefing via Claude...")
    html = claude_generiere_mail(kontext)
    print("✓ Briefing generiert")

    sende_mail(html)
    print("✓ Fertig!")


if __name__ == "__main__":
    main()
