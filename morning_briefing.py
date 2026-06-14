#!/usr/bin/env python3
"""
Kicktipp Morning Briefing – token-effizient
Täglich 8:00 Uhr via GitHub Actions.
"""

import json, os, smtplib, http.client, ssl
from datetime import datetime, timezone, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]
GMAIL_USER        = os.environ["GMAIL_USER"]
GMAIL_APP_PW      = os.environ["GMAIL_APP_PW"]
EMPFAENGER        = os.environ["BRIEFING_EMPFAENGER"].split(",")
COMMUNITY         = os.environ.get("KICKTIPP_COMMUNITY", "stb-tipprunde")
DATEN_FILE        = "kicktipp_daten.json"

MESZ = timezone(timedelta(hours=2))


# ── Hilfsfunktionen ─────────────────────────────────────────────

def api_call(payload):
    body = json.dumps(payload).encode()
    ctx  = ssl.create_default_context()
    conn = http.client.HTTPSConnection("api.anthropic.com", context=ctx)
    conn.request("POST", "/v1/messages", body=body, headers={
        "Content-Type": "application/json",
        "x-api-key": ANTHROPIC_API_KEY,
        "anthropic-version": "2023-06-01",
    })
    resp = conn.getresponse()
    raw  = resp.read().decode()
    conn.close()
    if resp.status != 200:
        raise Exception(f"API {resp.status}: {raw[:300]}")
    return json.loads(raw)


def text_aus_response(result):
    return "\n".join(b["text"] for b in result.get("content", []) if b.get("type") == "text").strip()


def kumuliert(spieltage, name, bis_idx):
    total = 0
    for st in spieltage[:bis_idx + 1]:
        p = next((x for x in st["spieler"] if x["name"] == name), None)
        if not p:
            continue
        for sp in st["spiele"]:
            if sp["abgeschlossen"]:
                total += p["punkte_pro_spiel"].get(str(sp["col_idx"]), 0)
    return total


# ── Kontext aufbereiten ─────────────────────────────────────────

def erstelle_kontext():
    if not os.path.exists(DATEN_FILE):
        return None, 0, 0

    with open(DATEN_FILE, encoding="utf-8") as f:
        daten = json.load(f)

    alle_st   = daten.get("spieltage", [])
    aktive_st = [st for st in alle_st if any(sp["abgeschlossen"] for sp in st["spiele"])]
    if not aktive_st:
        return None, 0, 0

    namen = sorted({p["name"] for st in aktive_st for p in st["spieler"]})
    letzter_idx = len(aktive_st) - 1
    letzter_st  = aktive_st[letzter_idx]

    # Rangliste
    rang = sorted(
        [{"name": n, "pts": kumuliert(aktive_st, n, letzter_idx)} for n in namen],
        key=lambda x: -x["pts"]
    )

    # Spieltag-Punkte (letzter Spieltag)
    st_pts = {}
    for p in letzter_st["spieler"]:
        pts = sum(
            p["punkte_pro_spiel"].get(str(sp["col_idx"]), 0)
            for sp in letzter_st["spiele"] if sp["abgeschlossen"]
        )
        st_pts[p["name"]] = pts

    st_sorted = sorted(st_pts.items(), key=lambda x: -x[1])

    # Besondere Tipps im letzten Spieltag
    spezial = []
    for sp in letzter_st["spiele"]:
        if not sp["abgeschlossen"]:
            continue
        col = str(sp["col_idx"])
        treffer = [(p["name"], p["punkte_pro_spiel"].get(col, 0))
                   for p in letzter_st["spieler"]
                   if p["punkte_pro_spiel"].get(col, 0) > 0]
        gesamt  = len(letzter_st["spieler"])
        if len(treffer) == 1:
            spezial.append(f"{sp['label']} {sp['ergebnis']}: nur {treffer[0][0]} hat gepunktet ({treffer[0][1]}P)")
        elif len(treffer) == 0:
            spezial.append(f"{sp['label']} {sp['ergebnis']}: niemand hat gepunktet")

    # Tabellenveränderungen (aktuell vs. vorletzter Spieltag)
    bewegung = []
    if letzter_idx > 0:
        prev_rang = sorted(
            [{"name": n, "pts": kumuliert(aktive_st, n, letzter_idx - 1)} for n in namen],
            key=lambda x: -x["pts"]
        )
        prev_pos = {r["name"]: i+1 for i, r in enumerate(prev_rang)}
        curr_pos = {r["name"]: i+1 for i, r in enumerate(rang)}
        for n in namen:
            delta = prev_pos.get(n, 0) - curr_pos.get(n, 0)
            if abs(delta) >= 2:
                bewegung.append(f"{n}: {'hoch' if delta > 0 else 'runter'} {abs(delta)} Plaetze")

    # Turnierfortschritt
    total_ges  = sum(len(st["spiele"]) for st in alle_st)
    total_gesp = sum(sp["abgeschlossen"] for st in alle_st for sp in st["spiele"])
    prozent    = round(total_gesp / total_ges * 100) if total_ges else 0
    n          = len(namen)
    topf       = n * 20

    lines = [
        f"SPIELTAG: {letzter_st['name']}",
        "SPIELE: " + ", ".join(f"{s['label']} {s['ergebnis']}" for s in letzter_st["spiele"] if s["abgeschlossen"]),
        "",
        "RANGLISTE:",
        *[f"{i+1}. {r['name']} {r['pts']}P" for i, r in enumerate(rang)],
        "",
        f"SPIELTAG-PUNKTE: {', '.join(f'{n} {p}P' for n,p in st_sorted)}",
    ]
    if spezial:
        lines += ["", "BESONDERE TIPPS:", *spezial]
    if bewegung:
        lines += ["", "TABELLENBEWEGUNG:", *bewegung]
    lines += [
        "",
        f"PREISGELD: {topf}E Topf",
        f"Aktuell vorne: {rang[0]['name']} ({int(topf*0.5)}E), {rang[1]['name']} ({int(topf*0.3)}E), {rang[2]['name']} ({int(topf*0.2)}E)",
        f"Aktuell hinten (Grillpflicht): {rang[-3]['name']}, {rang[-2]['name']}, {rang[-1]['name']}",
        "",
        f"TURNIERSTAND: {total_gesp}/{total_ges} Spiele ({prozent}%)",
        f"TON-HINWEIS: {'Noch frueh im Turnier, vorsichtige Formulierungen verwenden.' if prozent < 30 else 'Turnier fortgeschritten, kann dramatischer werden.' if prozent < 80 else 'Endphase, volle Dramatik erlaubt.'}",
    ]

    return "\n".join(lines), total_ges, total_gesp


# ── WM-News ─────────────────────────────────────────────────────

def hole_wm_news():
    datum = datetime.now(MESZ).strftime("%d.%m.%Y")
    result = api_call({
        "model": "claude-sonnet-4-6",
        "max_tokens": 400,
        "tools": [{"type": "web_search_20250305", "name": "web_search"}],
        "messages": [{"role": "user", "content":
            f"WM 2026 Neuigkeiten letzte 24h ({datum}): Gib mir maximal 4 kurze Stichpunkte auf Deutsch. Tore, Ueberraschungen, Aufreger, besondere Spieler. Nur Text, keine Formatierung, max 100 Woerter."
        }]
    })
    return text_aus_response(result) or "Keine aktuellen WM-News."


# ── Mail generieren ─────────────────────────────────────────────

def generiere_html(kontext, wm_news):
    datum = datetime.now(MESZ).strftime("%A %d. %B %Y").replace(
        "Monday","Montag").replace("Tuesday","Dienstag").replace("Wednesday","Mittwoch").replace(
        "Thursday","Donnerstag").replace("Friday","Freitag").replace("Saturday","Samstag").replace(
        "Sunday","Sonntag").replace("January","Januar").replace("February","Februar").replace(
        "March","Maerz").replace("April","April").replace("May","Mai").replace("June","Juni").replace(
        "July","Juli").replace("August","August").replace("September","September").replace(
        "October","Oktober").replace("November","November").replace("December","Dezember")

    prompt = f"""Morning Briefing STB-Tipprunde WM 2026, {datum}.

WM-NEWS:
{wm_news}

TIPPRUNDE:
{kontext}

Schreibe lockeres, witziges Briefing auf Deutsch mit Emojis. Struktur:
1. Kurze Begruessung (1-2 Saetze)
2. WM-Highlights gestern (3-4 Saetze, die interessantesten Fakten)
3. Tipprunden-Stand (Tabelle + wer hat gestern gut/schlecht getippt + besondere Tipps + Tabellenbewegungen)
4. Ausblick heutige Spiele (1-2 Saetze)
5. Gruesse von Bot-Valentin (Pflicht, immer am Ende)

Regeln: Keine Gedankenstriche. Keine Aufzaehlungen mit Strich. Ton laut TONHINWEIS im Kontext anpassen. Kein abschliessender Spruch oder Zitat. Keine Links.
Ausgabe: NUR HTML-Body-Inhalt mit Inline-CSS. Dunkel: bg #1a1a1a, text #f0f0f0, akzent #c01c00. Max 350 Woerter."""

    result = api_call({
        "model": "claude-sonnet-4-6",
        "max_tokens": 1200,
        "messages": [{"role": "user", "content": prompt}]
    })
    return text_aus_response(result)


# ── Mail senden ─────────────────────────────────────────────────

def sende_mail(html_body):
    datum = datetime.now(MESZ).strftime("%d.%m.%Y")
    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"Kicktipp Morning Briefing {datum}"
    msg["From"]    = f"Kicktipp Bot <{GMAIL_USER}>"
    msg["To"]      = ", ".join(EMPFAENGER)

    html = f"""<!DOCTYPE html><html lang="de">
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body style="margin:0;padding:0;background:#111;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;">
<div style="max-width:600px;margin:0 auto;padding:16px;">
  <div style="background:#c01c00;border-radius:10px 10px 0 0;padding:14px 20px;">
    <span style="font-size:1.1rem;font-weight:800;color:#fff;">kicktipp</span>
    <span style="color:rgba(255,255,255,.55);font-size:.8rem;"> STB-Tipprunde WM 2026</span>
  </div>
  <div style="background:#1a1a1a;border:1px solid #333;border-top:none;padding:20px;">
    {html_body}
  </div>
  <div style="background:#1a1a1a;border:1px solid #333;border-top:1px solid #2a2a2a;border-radius:0 0 10px 10px;padding:16px 20px;display:flex;gap:10px;">
    <a href="https://valentingongoll-ops.github.io/kicktipp-wm/" style="flex:1;display:block;text-align:center;background:#c01c00;color:#fff;text-decoration:none;padding:11px;border-radius:7px;font-weight:700;font-size:.82rem;">📊 Leaderboard</a>
    <a href="https://www.kicktipp.de" style="flex:1;display:block;text-align:center;background:#2a2a2a;color:#f0f0f0;text-decoration:none;border:1px solid #444;padding:11px;border-radius:7px;font-weight:700;font-size:.82rem;">⚽ Tipps abgeben</a>
  </div>
  <div style="text-align:center;padding:12px;color:#444;font-size:.68rem;">Automatisch generiert {datum}</div>
</div>
</body></html>"""

    msg.attach(MIMEText(html, "html", "utf-8"))
    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:
        smtp.login(GMAIL_USER, GMAIL_APP_PW)
        smtp.sendmail(GMAIL_USER, EMPFAENGER, msg.as_bytes())
    print(f"Mail gesendet an {len(EMPFAENGER)} Empfaenger")


# ── Main ─────────────────────────────────────────────────────────

def main():
    print("Morning Briefing Start")

    kontext, total_ges, total_gesp = erstelle_kontext()
    if not kontext:
        print("Keine Daten verfuegbar")
        return
    print(f"Kontext: {len(kontext)} Zeichen, {total_gesp}/{total_ges} Spiele")

    print("Hole WM-News...")
    wm_news = hole_wm_news()
    print(f"News: {wm_news[:80]}...")

    print("Generiere Mail...")
    html = generiere_html(kontext, wm_news)
    print(f"HTML: {len(html)} Zeichen")

    sende_mail(html)
    print("Fertig!")


if __name__ == "__main__":
    main()
