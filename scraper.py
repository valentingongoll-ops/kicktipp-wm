#!/usr/bin/env python3
"""
Kicktipp Scraper – für GitHub Actions
Credentials kommen aus Umgebungsvariablen (GitHub Secrets).
"""

import requests
from bs4 import BeautifulSoup
import json, re, os
from datetime import datetime

EMAIL      = os.environ.get("KICKTIPP_EMAIL",     "")
PASSWORD   = os.environ.get("KICKTIPP_PASSWORD",  "")
COMMUNITY  = os.environ.get("KICKTIPP_COMMUNITY", "stb-tipprunde")
OUTPUT_FILE = "kicktipp_daten.json"

BASE_URL  = "https://www.kicktipp.de"
LOGIN_URL = f"{BASE_URL}/info/profil/loginaction"
HEADERS   = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}


def login(session):
    r = session.get(f"{BASE_URL}/info/profil/login", headers=HEADERS)
    soup = BeautifulSoup(r.text, "html.parser")
    form = soup.find("form")
    payload = {"kennung": EMAIL, "passwort": PASSWORD}
    if form:
        for inp in form.find_all("input", {"type": "hidden"}):
            if inp.get("name"):
                payload[inp["name"]] = inp.get("value", "")
    r = session.post(LOGIN_URL, data=payload, headers=HEADERS, allow_redirects=True)
    ok = "loginForm" not in r.text and "passwort" not in r.url
    print("✓ Login erfolgreich" if ok else "✗ Login fehlgeschlagen")
    return ok


def hole_saison_id(session):
    r = session.get(f"{BASE_URL}/{COMMUNITY}/tippuebersicht", headers=HEADERS)
    m = re.search(r"tippsaisonId=(\d+)", r.text)
    if m:
        return m.group(1), r.text
    return None, None


def hole_spieltage(html):
    soup = BeautifulSoup(html, "html.parser")
    seen, result = set(), []
    for a in soup.find_all("a", href=re.compile(r"spieltagIndex=\d+")):
        href = a.get("href", "")
        if "bonus" in href:
            continue
        m = re.search(r"spieltagIndex=(\d+)", href)
        if m:
            idx = m.group(1)
            if idx not in seen:
                seen.add(idx)
                result.append((idx, a.get_text(strip=True)))
    return result


def parse_spiele_header(soup):
    table = soup.find("table", {"id": "ranking"})
    if not table:
        return []
    spiele = []
    for th in table.find("thead").find_all("th", class_=re.compile(r"ereignis\d+")):
        idx_match = re.search(r"ereignis(\d+)", " ".join(th.get("class", [])))
        if not idx_match:
            continue
        col_idx = int(idx_match.group(1))
        headerboxes = [d.get_text(strip=True) for d in th.find_all("div", class_="headerbox")]
        heim = headerboxes[0] if len(headerboxes) > 0 else "?"
        gast = headerboxes[1] if len(headerboxes) > 1 else "?"
        ergebnis_span = th.find("span", class_=re.compile(r"kicktipp-abpfiff"))
        abgeschlossen = False
        ergebnis = None
        if ergebnis_span:
            heim_el = ergebnis_span.find("span", class_="kicktipp-heim")
            gast_el = ergebnis_span.find("span", class_="kicktipp-gast")
            if heim_el and gast_el:
                h, g = heim_el.get_text(strip=True), gast_el.get_text(strip=True)
                if h != "-" and g != "-" and h != "" and g != "":
                    abgeschlossen = True
                    ergebnis = f"{h}:{g}"
        spiele.append({"col_idx": col_idx, "label": f"{heim}–{gast}",
                        "abgeschlossen": abgeschlossen, "ergebnis": ergebnis})
    spiele.sort(key=lambda s: s["col_idx"])
    return spiele


def parse_spieler_zeilen(soup, spiele):
    table = soup.find("table", {"id": "ranking"})
    if not table:
        return []
    col_indices  = {s["col_idx"] for s in spiele}
    abgeschl_cols = {s["col_idx"] for s in spiele if s["abgeschlossen"]}
    spieler = []
    for row in table.find("tbody").find_all("tr"):
        pos_td   = row.find("td", class_="position")
        name_div = row.find("div", class_="mg_name")
        ges_td   = row.find("td", class_=re.compile(r"gesamtpunkte"))
        if not (pos_td and name_div and ges_td):
            continue
        try:
            platz_text = pos_td.get_text(strip=True).replace(".", "")
            platz  = int(platz_text) if platz_text.isdigit() else None
            name   = name_div.get_text(strip=True)
            gesamt = int(ges_td.get_text(strip=True))
        except ValueError:
            continue
        punkte_pro_spiel, exakt_pro_spiel = {}, {}
        for td in row.find_all("td", class_=re.compile(r"ereignis\d+")):
            classes = " ".join(td.get("class", []))
            m = re.search(r"ereignis(\d+)", classes)
            if not m:
                continue
            cidx = int(m.group(1))
            if cidx not in col_indices:
                continue
            sub = td.find("sub", class_="p")
            if sub:
                try:
                    pts = int(sub.get_text(strip=True))
                    punkte_pro_spiel[cidx] = pts
                    exakt_pro_spiel[cidx]  = pts >= 4
                except ValueError:
                    punkte_pro_spiel[cidx] = 0
                    exakt_pro_spiel[cidx]  = False
            elif cidx in abgeschl_cols:
                punkte_pro_spiel[cidx] = 0
                exakt_pro_spiel[cidx]  = False
        spieler.append({"platz": platz, "name": name, "gesamt": gesamt,
                         "punkte_pro_spiel": punkte_pro_spiel,
                         "exakt_pro_spiel":  exakt_pro_spiel})
    return spieler


def scrape(session, saison_id, html_first):
    spieltage_list = hole_spieltage(html_first)
    print(f"  {len(spieltage_list)} Spieltage gefunden")
    result = {"community": COMMUNITY, "zuletzt_aktualisiert": datetime.now().isoformat(), "spieltage": []}
    base_url = f"{BASE_URL}/{COMMUNITY}/tippuebersicht"

    for st_idx, st_name in spieltage_list:
        url = f"{base_url}?tippsaisonId={saison_id}&spieltagIndex={st_idx}"
        r   = session.get(url, headers=HEADERS)
        if r.status_code != 200:
            continue
        soup    = BeautifulSoup(r.text, "html.parser")
        spiele  = parse_spiele_header(soup)
        spieler = parse_spieler_zeilen(soup, spiele)
        if not spieler:
            continue
        abgeschl = [s for s in spiele if s["abgeschlossen"]]
        print(f"  ✓ {st_name}: {len(spieler)} Spieler · {len(abgeschl)}/{len(spiele)} Spiele")
        allein = {p["name"]: 0 for p in spieler}
        for spiel in abgeschl:
            cidx = spiel["col_idx"]
            mit  = [p["name"] for p in spieler if p["punkte_pro_spiel"].get(cidx, 0) > 0]
            if len(mit) == 1:
                allein[mit[0]] += 1
        # Nur Spieltage mit mind. einem abgeschlossenen Spiel speichern
        if not abgeschl:
            print(f"  – {st_name}: übersprungen (keine abgeschlossenen Spiele)")
            continue

        result["spieltage"].append({
            "name": st_name, "index": int(st_idx), "spiele": spiele,
            "spieler": [{"platz": p["platz"], "name": p["name"], "gesamt": p["gesamt"],
                          "punkte_pro_spiel": {str(k): v for k, v in p["punkte_pro_spiel"].items()},
                          "exakt_pro_spiel":  {str(k): v for k, v in p["exakt_pro_spiel"].items()},
                          "allein_punkte":    allein.get(p["name"], 0)}
                         for p in spieler]
        })
    return result


def main():
    if not EMAIL:
        print("✗ Keine Credentials – KICKTIPP_EMAIL Umgebungsvariable fehlt")
        raise SystemExit(1)
    session = requests.Session()
    if not login(session):
        raise SystemExit(1)
    saison_id, html = hole_saison_id(session)
    if not saison_id:
        print("✗ Saison-ID nicht gefunden")
        raise SystemExit(1)
    daten = scrape(session, saison_id, html)

    # Vorherige JSON laden um zu prüfen ob sich was geändert hat
    changed = True
    if os.path.exists(OUTPUT_FILE):
        with open(OUTPUT_FILE) as f:
            old = json.load(f)
        # Zeitstempel ignorieren beim Vergleich
        old.pop("zuletzt_aktualisiert", None)
        daten_copy = {k: v for k, v in daten.items() if k != "zuletzt_aktualisiert"}
        changed = json.dumps(old, sort_keys=True) != json.dumps(daten_copy, sort_keys=True)

    if changed:
        with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
            json.dump(daten, f, ensure_ascii=False, indent=2)
        total = sum(len([s for s in st["spiele"] if s["abgeschlossen"]]) for st in daten["spieltage"])
        print(f"✓ {OUTPUT_FILE} gespeichert · {total} Spiele abgeschlossen")
    else:
        print("→ Keine Änderungen, kein Commit nötig")

if __name__ == "__main__":
    main()
