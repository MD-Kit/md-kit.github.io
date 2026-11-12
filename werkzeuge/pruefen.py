#!/usr/bin/env python3
"""Prüfungen vor jedem Push. Die letzte Zeile ist das Urteil: GESAMT: GRÜN oder GESAMT: ROT -- <Gründe>.

  Lokal, alles:   python werkzeuge/pruefen.py --werte <datei> --aus <ordner> --fotos <ordner> [--browser <exe>]
  GitHub Actions: python werkzeuge/pruefen.py --nur-seiten <gebauter ordner>

Geprüft wird:
  1. Bau: frisch aus quellen/, ohne Probleme (Pflichtangaben, Platzhalter, Markdown).
  2. Seiten: kein [ oder ] im sichtbaren Text; jede Seite mit lang, Titel, viewport und CSP; jeder
     interne Link und jede Gegenseite vorhanden; nach außen nur Links (<a>), nie geladene Dateien;
     app-ads.txt in der AdMob-Form; nirgends ein Pfad eines PCs.
  3. Repo: nur erlaubte Pfade, keine PC-Pfade, kein persönlicher Wert aus der Wertedatei; Workflow nur
     mit offiziellen GitHub-Actions auf fester Commit-SHA und genau den Secrets aus webseite.toml.
  4. Farben: jedes Text/Grund-Paar aus site.css mindestens 4,5 : 1.
  5. Browser: jede Seite (und eine unbekannte Adresse für die 404-Seite) lokal von 127.0.0.1 laden,
     jede Anfrage zählen -- eine einzige nach außen ist ROT; kein waagrechtes Scrollen am Handy;
     Handy-Fotos (390 x 844, Faktor 3, ganze Seite) in --fotos.
Gibt nie einen persönlichen Wert aus.
"""
import argparse
import html
import json
import re
import shutil
import subprocess
import sys
import tempfile
import threading
from functools import partial
from html.parser import HTMLParser
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.dont_write_bytecode = True  # keine __pycache__-Ordner im Repo-Ordner
sys.path.insert(0, str(Path(__file__).resolve().parent))
import bauen  # noqa: E402
import regeln  # noqa: E402

WURZEL = bauen.WURZEL
FARBPAARE = [  # (Text, Grund, wo)
	("tinte", "grund", "Fließtext"), ("tinte", "karte", "Text in den Karten"),
	("petrol", "grund", "Überschriften und Links"), ("petrol", "karte", "Links in den Karten"),
	("weiss", "petrol", "Kopfzeile"), ("tinte-leise", "grund", "Fußzeile"),
]


class Ergebnis:
	def __init__(self):
		self.rot = []

	def abschnitt(self, name, probleme, gut):
		print(f"\n== {name}")
		for p in probleme:
			print("  ROT:", p)
		if probleme:
			self.rot.append(f"{name} ({len(probleme)})")
		else:
			print("  ok:", gut)


# ---------------------------------------------------------------- 2. gebaute Seiten

class SeitenLeser(HTMLParser):
	def __init__(self):
		super().__init__(convert_charrefs=True)
		self.lang = None
		self.titel = None
		self.meta = {}
		self.verweise = []  # (tag, attr, wert, rel)
		self.text = []
		self.tags = set()
		self._im_titel = False

	def handle_starttag(self, tag, attrs):
		a = dict(attrs)
		self.tags.add(tag)
		if tag == "html":
			self.lang = a.get("lang")
		if tag == "title":
			self._im_titel = True
		if tag == "meta":
			k = a.get("name") or a.get("http-equiv") or ("charset" if "charset" in a else None)
			if k:
				self.meta[k.lower()] = a.get("content", a.get("charset"))
		for k in ("href", "src", "srcset", "action", "poster", "data"):
			if k in a:
				self.verweise.append((tag, k, a[k] or "", a.get("rel", "")))
		if "style" in a:
			self.verweise.append((tag, "style", a["style"], ""))

	def handle_endtag(self, tag):
		if tag == "title":
			self._im_titel = False

	def handle_data(self, d):
		if self._im_titel:
			self.titel = (self.titel or "") + d
		self.text.append(d)


def ziel_datei(aus, pfad):
	p = pfad.split("#")[0].split("?")[0]
	if p.endswith("/"):
		p += "index.html"
	return aus / p.lstrip("/")


def pruefe_seiten(aus, e):
	p = []
	aus = Path(aus)
	url = e["studio"]["url"]
	erwartet = {"index.html", "404.html", "site.css", "app-ads.txt"}
	paare = []
	for _, de, en in bauen.STUDIO_SEITEN:
		paare.append((f"/{de}/", f"/{en}/"))
	for s in e["spiel"]:
		for _, de, en in bauen.SPIEL_SEITEN:
			paare.append((f"/{s['ordner']}/{de}/", f"/{s['ordner']}/{en}/"))
	for a, b in paare:
		erwartet |= {a.lstrip("/") + "index.html", b.lstrip("/") + "index.html"}
	vorhanden = {f.relative_to(aus).as_posix() for f in aus.rglob("*") if f.is_file() and f.name != bauen.KENNUNG}
	for f in sorted(erwartet - vorhanden):
		p.append(f"fehlt: {f}")
	for f in sorted(vorhanden - erwartet):
		p.append(f"unerwartete Datei: {f}")

	gegen = {a: b for a, b in paare} | {b: a for a, b in paare}
	for f in sorted(x for x in vorhanden if x.endswith(".html")):
		t = (aus / f).read_text(encoding="utf-8")
		s = SeitenLeser()
		s.feed(t)
		sichtbar = "".join(s.text)
		if "[" in sichtbar or "]" in sichtbar:
			p.append(f"{f}: eckige Klammer im sichtbaren Text")
		if s.lang not in ("de", "en"):
			p.append(f"{f}: <html lang> fehlt")
		if not (s.titel or "").strip():
			p.append(f"{f}: kein Titel")
		if "viewport" not in s.meta:
			p.append(f"{f}: keine viewport-Angabe")
		if s.meta.get("content-security-policy") != bauen.CSP:
			p.append(f"{f}: Content-Security-Policy fehlt oder weicht ab")
		for tag in ("script", "iframe", "img", "object", "embed", "form", "video", "audio", "picture", "source", "base"):
			if tag in s.tags:
				p.append(f"{f}: <{tag}> nicht erlaubt")
		for tag, attr, wert, rel in s.verweise:
			if attr == "style":
				p.append(f"{f}: style-Attribut nicht erlaubt")
			elif tag == "a" and attr == "href":
				if wert.startswith("/"):
					if not ziel_datei(aus, wert).exists():
						p.append(f"{f}: Link ins Leere: {wert}")
				elif wert.startswith("http://"):
					p.append(f"{f}: Link mit http://")
				elif not wert.startswith(("https://", "mailto:")):
					p.append(f"{f}: Link-Art nicht erlaubt: {wert[:30]}")
			elif tag == "link" and rel == "stylesheet":
				if wert != "/site.css":
					p.append(f"{f}: fremdes Stylesheet {wert[:40]}")
			elif tag == "link" and rel == "icon":
				if wert != "data:,":
					p.append(f"{f}: Icon lädt eine Datei: {wert[:40]}")
			elif tag == "link" and rel in ("canonical", "alternate"):
				if not wert.startswith(url + "/") or not ziel_datei(aus, wert[len(url):]).exists():
					p.append(f"{f}: {rel} zeigt nicht auf eine eigene Seite: {wert[:60]}")
			else:
				p.append(f"{f}: <{tag} {attr}> lädt etwas: {wert[:40]}")
		seite = "/" + f.removesuffix("index.html") if f.endswith("index.html") else None
		if seite in gegen:
			ziel = gegen[seite]
			if f'class="sprache" href="{ziel}"' not in t:
				p.append(f"{f}: Sprachlink zur Gegenseite {ziel} fehlt")
	css = (aus / "site.css").read_text(encoding="utf-8") if (aus / "site.css").exists() else ""
	if re.search(r"url\s*\(|@import|https?:", css, re.IGNORECASE):
		p.append("site.css: lädt etwas (url(), @import oder Adresse)")
	ads = (aus / "app-ads.txt").read_text(encoding="utf-8") if (aus / "app-ads.txt").exists() else ""
	if not re.fullmatch(r"google\.com, pub-\d{16}, DIRECT, f08c47fec0942fa0\n", ads):
		p.append("app-ads.txt: nicht in der AdMob-Form (Publisher-ID noch nicht eingetragen?)")
	for f in sorted(vorhanden):
		if regeln.LOKALER_PFAD.search((aus / f).read_text(encoding="utf-8", errors="replace")):
			p.append(f"{f}: enthält einen Pfad eines PCs")
	return p, len(vorhanden)


# ---------------------------------------------------------------- 3. Repo

def git(*args):
	return subprocess.run(["git", "-C", str(WURZEL), *args], capture_output=True, text=True, encoding="utf-8", check=True).stdout


def pruefe_repo(e, werte):
	p = []
	dateien = [z for z in git("ls-files", "-co", "--exclude-standard").splitlines() if z]
	for f in dateien:
		if not regeln.erlaubt(f):
			p.append(f"nicht erlaubter Pfad im Repo: {f}")
	geheim = {k: v for k, v in werte.items() if len(v) >= regeln.MIN_WERTLAENGE}
	for f in dateien:
		pf = WURZEL / f
		if not pf.is_file():
			continue
		t = pf.read_text(encoding="utf-8", errors="replace")
		if regeln.LOKALER_PFAD.search(t):
			p.append(f"{f}: enthält einen Pfad eines PCs")
		for k, v in geheim.items():
			for teil in v.splitlines():
				if len(teil) >= regeln.MIN_WERTLAENGE and teil.lower() in t.lower():
					p.append(f"{f}: enthält den Wert von {k}")
	wf = WURZEL / ".github/workflows/seite.yml"
	if not wf.exists():
		p.append(".github/workflows/seite.yml fehlt")
	else:
		t = wf.read_text(encoding="utf-8")
		for u in re.findall(r"uses:\s*(\S+)", t):
			if not re.fullmatch(r"actions/[a-z0-9-]+@[0-9a-f]{40}", u):
				p.append(f"Workflow: Action nicht offiziell oder nicht auf eine Commit-SHA festgelegt: {u}")
		pers = e["persoenlich"]
		soll = {bauen.UMGEBUNG + k for k in pers["pflicht"] + pers["optional"]}
		ist = set(re.findall(r"secrets\.([A-Z0-9_]+)", t))
		for k in sorted(soll - ist):
			p.append(f"Workflow: Secret {k} wird nicht übergeben")
		for k in sorted(ist - soll):
			p.append(f"Workflow: unbekanntes Secret {k}")
		for k in re.findall(r"(PRIVAT_[A-Z0-9_]+):\s*\$\{\{\s*secrets\.([A-Z0-9_]+)", t):
			if k[0] != k[1]:
				p.append(f"Workflow: {k[0]} bekommt secrets.{k[1]}")
	return p, len(dateien)


# ---------------------------------------------------------------- 4. Farben

def kontrast(a, b):
	def hell(h):
		h = h.lstrip("#")
		c = [int(h[i:i + 2], 16) / 255 for i in (0, 2, 4)]
		c = [x / 12.92 if x <= 0.04045 else ((x + 0.055) / 1.055) ** 2.4 for x in c]
		return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]
	la, lb = sorted((hell(a), hell(b)), reverse=True)
	return (la + 0.05) / (lb + 0.05)


def pruefe_farben():
	css = (bauen.VORLAGE / "site.css").read_text(encoding="utf-8")
	farben = dict(re.findall(r"--([a-z-]+):\s*(#[0-9a-fA-F]{6})\s*;", css))
	p, zeilen = [], []
	for t, g, wo in FARBPAARE:
		if t not in farben or g not in farben:
			p.append(f"Farbe --{t} oder --{g} fehlt in site.css")
			continue
		k = kontrast(farben[t], farben[g])
		zeilen.append(f"{wo} {k:.1f}:1")
		if k < 4.5:
			p.append(f"{wo}: --{t} auf --{g} nur {k:.2f} : 1")
	return p, ", ".join(zeilen)


# ---------------------------------------------------------------- 5. Browser

class Handler(SimpleHTTPRequestHandler):
	def log_message(self, *a):
		pass

	def list_directory(self, path):
		self.send_error(404)

	def send_error(self, code, message=None, explain=None):
		seite = Path(self.directory) / "404.html"
		if code == 404 and seite.exists():  # wie GitHub Pages: eigene 404-Seite mit Status 404
			body = seite.read_bytes()
			self.send_response(404)
			self.send_header("Content-Type", "text/html; charset=utf-8")
			self.send_header("Content-Length", str(len(body)))
			self.end_headers()
			if self.command != "HEAD":
				self.wfile.write(body)
		else:
			super().send_error(code, message, explain)


class Server(ThreadingHTTPServer):
	def handle_error(self, request, client_address):
		pass  # der Browser schließt Verbindungen beim Beenden; kein Prüfergebnis


def finde_browser(angabe):
	if angabe:
		return angabe
	import os
	for basis in (os.environ.get("PROGRAMFILES"), os.environ.get("PROGRAMFILES(X86)"), os.environ.get("LOCALAPPDATA")):
		if not basis:
			continue
		for teile in (("Google", "Chrome", "Application", "chrome.exe"), ("Microsoft", "Edge", "Application", "msedge.exe")):
			kandidat = Path(basis, *teile)
			if kandidat.exists():
				return str(kandidat)
	return shutil.which("chromium") or shutil.which("google-chrome")


def pruefe_browser(aus, e, fotos, browser):
	p = []
	exe = finde_browser(browser)
	if not exe:
		return ["kein Chrome oder Edge gefunden (--browser angeben)"], ""
	node = shutil.which("node")
	if not node:
		return ["node nicht gefunden"], ""
	fotos = Path(fotos)
	fotos.mkdir(parents=True, exist_ok=True)
	seiten = ["/"] + sorted("/" + f.relative_to(aus).as_posix().removesuffix("index.html")
		for f in Path(aus).rglob("index.html") if f.parent != Path(aus))
	seiten += ["/gibt-es-nicht/"]
	server = Server(("127.0.0.1", 0), partial(Handler, directory=str(aus)))
	threading.Thread(target=server.serve_forever, daemon=True).start()
	basis = f"http://127.0.0.1:{server.server_address[1]}"
	try:
		r = subprocess.run([node, str(Path(__file__).with_name("pruefen_browser.mjs")), exe, basis, str(fotos), *seiten],
			capture_output=True, text=True, encoding="utf-8", timeout=300)
	finally:
		server.shutdown()
	if r.returncode != 0:
		return [f"Browser-Messung gescheitert: {r.stderr.strip()[:200]}"], ""
	daten = json.loads(r.stdout.strip().splitlines()[-1])
	gesamt, aussen = 0, 0
	for d in daten:
		erwartet = 404 if d["pfad"] == "/gibt-es-nicht/" else 200
		if not d["geladen"]:
			p.append(f"{d['pfad']}: nicht fertig geladen")
		if d["status"] != erwartet:
			p.append(f"{d['pfad']}: Status {d['status']} statt {erwartet}")
		for u in d["anfragen"]:
			gesamt += 1
			if not (u.startswith(basis + "/") or u.startswith("data:")):
				aussen += 1
				p.append(f"{d['pfad']}: Anfrage nach außen: {u[:80]}")
		if d["breite"][0] > d["breite"][1]:
			p.append(f"{d['pfad']}: waagrechtes Scrollen am Handy ({d['breite'][0]} > {d['breite'][1]} px)")
	zus = (f"{len(daten)} Seiten, {gesamt} Anfragen, davon {aussen} nach außen; "
		f"Handy-Fotos: {len(daten)} in {fotos.name}")
	return p, zus


# ---------------------------------------------------------------- Ablauf

def main():
	sys.stdout.reconfigure(encoding="utf-8")
	a = argparse.ArgumentParser(description=__doc__.split("\n")[0])
	a.add_argument("--nur-seiten", help="nur die gebauten Seiten in diesem Ordner prüfen (GitHub Actions)")
	a.add_argument("--werte")
	a.add_argument("--aus")
	a.add_argument("--fotos")
	a.add_argument("--browser")
	args = a.parse_args()
	erg = Ergebnis()
	e = bauen.lade_einstellungen()
	if args.nur_seiten:
		p, n = pruefe_seiten(args.nur_seiten, e)
		erg.abschnitt("Seiten", p, f"{n} Dateien")
	else:
		if not (args.werte and args.aus and args.fotos):
			a.error("--werte, --aus und --fotos angeben (oder --nur-seiten)")
		werte, p = bauen.lade_werte(e, args.werte)
		dateien, p2 = bauen.baue(e, werte)
		p += p2
		try:
			bauen.schreibe(args.aus, dateien)
		except bauen.Probleme as x:
			p += x.args[0]
		erg.abschnitt("Bau", p, f"{len(dateien)} Dateien aus quellen/")
		aus = Path(args.aus).resolve()
		p, n = pruefe_seiten(aus, e)
		erg.abschnitt("Seiten", p, f"{n} Dateien, alle internen Links und Gegenseiten vorhanden")
		p, n = pruefe_repo(e, werte)
		erg.abschnitt("Repo", p, f"{n} Dateien, nur erlaubte Pfade, keine PC-Pfade, keine persönlichen Werte; Workflow fest auf SHAs")
		p, z = pruefe_farben()
		erg.abschnitt("Farben", p, z)
		p, z = pruefe_browser(aus, e, args.fotos, args.browser)
		erg.abschnitt("Browser", p, z)
	print()
	print("GESAMT: GRÜN" if not erg.rot else "GESAMT: ROT -- " + "; ".join(erg.rot))
	return 1 if erg.rot else 0


if __name__ == "__main__":
	sys.exit(main())
