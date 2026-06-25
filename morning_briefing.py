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
    """Gibt den offiziellen Kicktipp-Gesamtstand inkl. Bonuspunkte zurück."""
    if bis_idx < 0 or bis_idx >= len(spieltage):
        return 0
    st = spieltage[bis_idx]
    p = next((x for x in st["spieler"] if x["name"] == name), None)
    return p["gesamt"] if p else 0


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

    # Turnierfortschritt: 104 Spiele total (fix), nur tatsächlich gespielte zählen
    TOTAL_WM_SPIELE = 104
    total_gesp = sum(1 for st in alle_st for sp in st["spiele"] if sp["abgeschlossen"])
    prozent    = round(total_gesp / TOTAL_WM_SPIELE * 100)
    n          = len(namen)

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

        f"TURNIERSTAND: {total_gesp}/104 Spiele ({prozent}%)",
        f"TON-HINWEIS: {'Noch frueh im Turnier, vorsichtige Formulierungen verwenden.' if prozent < 30 else 'Turnier fortgeschritten, kann dramatischer werden.' if prozent < 80 else 'Endphase, volle Dramatik erlaubt.'}",
    ]

    return "\n".join(lines), 104, total_gesp


# ── Ranglisten-Tabelle als HTML ─────────────────────────────────

def erstelle_tabelle_html(rang):
    rows = ""
    medals = {1: "🥇", 2: "🥈", 3: "🥉"}
    for i, r in enumerate(rang):
        pl = i + 1
        medal = medals.get(pl, f"{pl}.")
        grill = " 🔥" if pl > len(rang) - 3 else ""
        bg = "#2a2a2a" if i % 2 == 0 else "#222"
        rows += (
            f'<tr style="background:{bg}">'
            f'<td style="padding:6px 10px;text-align:center">{medal}</td>'
            f'<td style="padding:6px 10px;font-weight:600">{r["name"]}{grill}</td>'
            f'<td style="padding:6px 10px;text-align:right;font-family:monospace;'
            f'color:#c01c00;font-weight:700">{r["pts"]}</td>'
            f'</tr>'
        )
    return (
        '<table style="width:100%;border-collapse:collapse;margin:10px 0;font-size:.85rem">'
        '<thead><tr style="background:#333">'
        '<th style="padding:7px 10px;text-align:center;color:#888;font-weight:500">Platz</th>'
        '<th style="padding:7px 10px;text-align:left;color:#888;font-weight:500">Name</th>'
        '<th style="padding:7px 10px;text-align:right;color:#888;font-weight:500">Punkte</th>'
        '</tr></thead>'
        f'<tbody>{rows}</tbody></table>'
    )


# ── WM-News ─────────────────────────────────────────────────────

def hole_wm_news():
    heute = datetime.now(MESZ)
    datum_str = heute.strftime("%d.%m.%Y")
    gestern_str = (heute - timedelta(days=1)).strftime("%d.%m.%Y")
    result = api_call({
        "model": "claude-haiku-4-5-20251001",
        "max_tokens": 300,
        "tools": [{"type": "web_search_20250305", "name": "web_search"}],
        "messages": [{"role": "user", "content":
            f"WM 2026 Ergebnisse und Highlights vom {gestern_str} und {datum_str}. "
            f"Nur Ereignisse dieser zwei Tage, keine älteren Spiele. "
            f"Max 3 Stichpunkte auf Deutsch, je 1 Satz. Keine Formatierung."
        }]
    })
    return text_aus_response(result) or "Keine aktuellen WM-News."


# ── Mail generieren ─────────────────────────────────────────────

def generiere_html(kontext, wm_news, tabelle_html=""):
    datum = datetime.now(MESZ).strftime("%A %d. %B %Y").replace(
        "Monday","Montag").replace("Tuesday","Dienstag").replace("Wednesday","Mittwoch").replace(
        "Thursday","Donnerstag").replace("Friday","Freitag").replace("Saturday","Samstag").replace(
        "Sunday","Sonntag").replace("January","Januar").replace("February","Februar").replace(
        "March","Maerz").replace("April","April").replace("May","Mai").replace("June","Juni").replace(
        "July","Juli").replace("August","August").replace("September","September").replace(
        "October","Oktober").replace("November","November").replace("December","Dezember")

    prompt = f"""Morning Briefing STB-Tipprunde WM 2026, {datum}.

CHARAKTER Bot-Valentin: Fußballbegeistert, pointiert, mit Augenzwinkern. Stil: 11-Freunde-Kolumne. Nie boshaft, aber gnadenlos bei schlechten Tipps. Korrekte deutsche Rechtschreibung, alle Umlaute ausschreiben.

WM-NEWS (nur diese Fakten verwenden, keine älteren Spiele erfinden):
{wm_news}

TIPPRUNDE:
{kontext}

VERBOTEN (absolute Regeln, keine Ausnahmen):
- Gedankenstriche (— oder – oder -- oder -) in jeglicher Form, auch nicht als Pause oder Einschub
- Bindestrich-Listen oder Aufzählungen mit Strich
- Preisgeld erwähnen
- Zitate oder Abschlusssprüche
- Links
- Spiele die nicht in WM-NEWS stehen

AUFBAU:
1. Kurze Begrüßung (1 Sätze)
2. WM-Highlights aus den NEWS oben (2-3 Sätze, nur was dort steht)
3. Schreibe exakt ##TABELLE## auf einer eigenen Zeile, dann darunter 2-3 Sätze Kommentar zu Tippern
4. Ausblick der kommenden Spiele heute (1 Satz)
5. "Greets, Bot-Valentin" als Abschluss

OUTPUT: Direkt mit HTML-Tag beginnen, kein ```html. Inline-CSS. bg #1a1a1a, text #f0f0f0, akzent #c01c00. Max 280 Wörter."""

    result = api_call({
        "model": "claude-sonnet-4-6",
        "max_tokens": 1500,
        "messages": [{"role": "user", "content": prompt}]
    })
    html = text_aus_response(result)
    # Markdown-Fences entfernen falls Claude sie trotzdem schreibt
    html = html.strip()
    if html.startswith("```"):
        lines = html.split("\n", 1)
        html = lines[-1] if len(lines) > 1 else html
    if html.endswith("```"):
        html = html[:html.rfind("```")]
    html = html.strip()
    # Platzhalter durch echte Python-generierte Tabelle ersetzen
    html = html.replace("##TABELLE##", tabelle_html)
    # Gedankenstriche entfernen die Claude trotzdem schreibt
    html = html.replace(" — ", " ").replace(" – ", " ").replace("—", " ").replace("–", " ")
    return html


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
  <div style="background:#1a1a1a;border:1px solid #333;border-top:none;border-bottom:none;padding:20px 20px 10px 20px;">
    {html_body}
  </div>
  <div style="background:#1a1a1a;border:1px solid #333;border-top:none;border-radius:0 0 10px 10px;padding:16px 20px;">
    <table width="100%" cellpadding="0" cellspacing="0" border="0">
      <tr>
        <td width="49%" style="padding-right:4px">
          <a href="https://valentingongoll-ops.github.io/kicktipp-wm/" style="display:block;text-align:center;background:#c01c00;color:#fff;text-decoration:none;padding:11px;border-radius:7px;font-weight:700;font-size:.82rem;">📊 Leaderboard</a>
        </td>
        <td width="2%"></td>
        <td width="49%" style="padding-left:4px">
          <a href="https://www.kicktipp.de" style="display:block;text-align:center;background:#2a2a2a;color:#f0f0f0;text-decoration:none;border:1px solid #444;padding:11px;border-radius:7px;font-weight:700;font-size:.82rem;">⚽ Tipps abgeben</a>
        </td>
      </tr>
    </table>
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

    kontext, _, total_gesp = erstelle_kontext()
    if not kontext:
        print("Keine Daten verfuegbar")
        return
    print(f"Kontext: {len(kontext)} Zeichen, {total_gesp}/104 Spiele")

    print("Hole WM-News...")
    wm_news = hole_wm_news()
    print(f"News: {wm_news[:80]}...")

    # Tabelle direkt in Python generieren (nicht von Claude, damit alle Eintraege sicher drin sind)
    kontext_obj, _, _ = erstelle_kontext()  # reload for rang
    with open(DATEN_FILE, encoding="utf-8") as f:
        daten = json.load(f)
    alle_st   = daten.get("spieltage", [])
    aktive_st = [st for st in alle_st if any(sp["abgeschlossen"] for sp in st["spiele"])]
    namen     = sorted({p["name"] for st in aktive_st for p in st["spieler"]})
    letzter_idx = len(aktive_st) - 1
    rang = sorted(
        [{"name": n, "pts": kumuliert(aktive_st, n, letzter_idx)} for n in namen],
        key=lambda x: -x["pts"]
    )
    tabelle_html = erstelle_tabelle_html(rang)

    print("Generiere Mail...")
    html = generiere_html(kontext, wm_news, tabelle_html)
    print(f"HTML: {len(html)} Zeichen")

    sende_mail(html)
    print("Fertig!")


if __name__ == "__main__":
    main()
