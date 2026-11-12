#!/usr/bin/env python3
"""Baut die Webseite aus quellen/ und vorlage/ (nur Python-Standardbibliothek, ab 3.11).

  python werkzeuge/bauen.py --aus <ordner> --werte <datei>        lokal: Werte aus einer Datei außerhalb des Repos
  python werkzeuge/bauen.py --aus <ordner> --werte-aus-umgebung   GitHub Actions: Werte aus den Secrets PRIVAT_<KEY>

Eine Stelle für Studio-Name, Adresse und Spiele: quellen/webseite.toml. Ein neues Spiel braucht nur
einen Ordner quellen/<spiel>/ mit den vier Texten (datenschutz, privacy, nutzungsbedingungen, terms)
und einen [[spiel]]-Eintrag dort.

Persönliche Angaben stehen nie im Repo. Die Texte enthalten nur Platzhalter wie [NAME]; erst der Bau
setzt die Werte ein. Pflichtangaben dürfen überall stehen. Optionale Angaben (Stufe 2 des Impressums)
nur in einem Block für sich selbst:
    ::: wenn UST_ID
    Umsatzsteuer-ID: [UST_ID]
    ::: sonst            (optional)
    ...
    :::
Ein Block erscheint nur, wenn der Wert gesetzt ist; "sonst" nur, wenn nicht.

Unterstütztes Markdown: # bis ###, Absätze, - und 1.-Listen (auch eingerückt), --- als Linie,
**fett**, *kursiv*, [Text](https://… | mailto:… | /intern/), bloße https-Adressen, Zeilenumbruch mit
\\ oder zwei Leerzeichen am Zeilenende. Alles andere (Tabellen, Bilder, HTML, Code, Zitate) bricht den
Bau mit Datei und Zeile ab -- nie still etwas Falsches.

Bei jedem Problem (fehlende Pflichtangabe, übrig gebliebener Platzhalter, unbekanntes Markdown) wird
nichts geschrieben (Exit 1). Meldungen nennen nur Schlüssel und Zeilen, nie einen Wert.
"""
import argparse
import html
import os
import re
import shutil
import sys
import tomllib
from html.parser import HTMLParser
from pathlib import Path

sys.dont_write_bytecode = True  # keine __pycache__-Ordner im Repo-Ordner
sys.path.insert(0, str(Path(__file__).resolve().parent))
import regeln  # noqa: E402

WURZEL = Path(__file__).resolve().parent.parent
QUELLEN = WURZEL / "quellen"
VORLAGE = WURZEL / "vorlage"
KENNUNG = ".bau_kennung"  # markiert einen Bau-Ordner; wird nicht veröffentlicht (versteckte Datei)
UMGEBUNG = "PRIVAT_"

TEXTE = {
	"de": {"name": "Deutsch", "start": "Startseite", "impressum": "Impressum",
		"datenschutz": "Datenschutzerklärung", "nutzungsbedingungen": "Nutzungsbedingungen",
		"fuss": "Rechtliches"},
	"en": {"name": "English", "start": "Home", "impressum": "Legal notice",
		"datenschutz": "Privacy policy", "nutzungsbedingungen": "Terms of use",
		"fuss": "Legal"},
}
ANDERE = {"de": "en", "en": "de"}
# (Thema, deutsche Datei = Adresse, englische Datei = Adresse). Die Adressen ändern sich nie mehr.
SPIEL_SEITEN = [("datenschutz", "datenschutz", "privacy"), ("nutzungsbedingungen", "nutzungsbedingungen", "terms")]
STUDIO_SEITEN = [("impressum", "impressum", "imprint")]
STUDIO_ORDNER = "studio"
PLATZHALTER = re.compile(r"\[([A-Z][A-Z0-9_]*)\](?!\()")
CSP = "default-src 'none'; style-src 'self'; img-src 'self' data:; base-uri 'none'; form-action 'none'"  # frame-ancestors wirkt nur als HTTP-Header


class Probleme(Exception):
	pass


# ---------------------------------------------------------------- Einstellungen und Werte

def lade_einstellungen():
	e = tomllib.loads((QUELLEN / "webseite.toml").read_text(encoding="utf-8"))
	p = []
	studio = e.get("studio", {})
	for k in ("name", "url", "text_de", "text_en"):
		if not isinstance(studio.get(k), str) or not studio.get(k).strip():
			p.append(f"webseite.toml: [studio] {k} fehlt")
	if isinstance(studio.get("url"), str) and not re.fullmatch(r"https://[a-z0-9.-]+", studio["url"]):
		p.append("webseite.toml: [studio] url muss https://<name> ohne / am Ende sein")
	pers = e.get("persoenlich", {})
	pflicht, optional = pers.get("pflicht", []), pers.get("optional", [])
	for k in pflicht + optional:
		if not re.fullmatch(r"[A-Z][A-Z0-9_]*", k):
			p.append(f"webseite.toml: ungültiger Schlüssel {k!r}")
	if set(pflicht) & set(optional) or set(pflicht + optional) & {"STUDIO", "WEBSEITE", "SPIEL", "PUBLISHER_ID"}:
		p.append("webseite.toml: Schlüssel doppelt oder reserviert")
	spiele = e.get("spiel", [])
	if not spiele:
		p.append("webseite.toml: kein [[spiel]]")
	ordner = set()
	for s in spiele:
		o = s.get("ordner", "")
		if not re.fullmatch(r"[a-z0-9]+(-[a-z0-9]+)*", o) or o in ordner or o == STUDIO_ORDNER:
			p.append(f"webseite.toml: [[spiel]] ordner {o!r} ungültig oder doppelt")
		ordner.add(o)
		for k in ("name", "text_de", "text_en"):
			if not str(s.get(k, "")).strip():
				p.append(f"webseite.toml: [[spiel]] {o}: {k} fehlt")
	if p:
		raise Probleme(p)
	return e


def lade_werte(e, werte_datei=None, aus_umgebung=False):
	pers = e["persoenlich"]
	schluessel = pers["pflicht"] + pers["optional"]
	p = []
	if aus_umgebung:
		roh = {k: os.environ.get(UMGEBUNG + k, "") for k in schluessel}
	else:
		roh, fehler = regeln.lies_werte_datei(Path(werte_datei).read_text(encoding="utf-8-sig"))
		p += fehler
		for k in roh:
			if k not in schluessel:
				p.append(f"Wertedatei: unbekannter Schlüssel {k}")
	werte = {}
	for k in schluessel:
		v = "\n".join(z.strip() for z in roh.get(k, "").replace("\\n", "\n").strip().splitlines())
		werte[k] = v
		if k in pers["pflicht"] and not v:
			p.append(f"Pflichtangabe fehlt: {k} (Secret {UMGEBUNG}{k})")
		if v and ("[" in v or "]" in v or "<" in v):
			p.append(f"Wert von {k} enthält [ ] oder <")
		if v and k.endswith("EMAIL") and not re.fullmatch(r"[^@\s]+@[^@\s]+\.[a-z]{2,}", v, re.IGNORECASE):
			p.append(f"Wert von {k} ist keine E-Mail-Adresse")
	return werte, p


# ---------------------------------------------------------------- Markdown

class Text:
	"""Wandelt die eingeschränkte Markdown-Schreibweise in HTML; sammelt Probleme mit Datei und Zeile."""

	def __init__(self, datei, werte, optional, zusatz, probleme):
		self.datei, self.werte, self.optional, self.zusatz, self.p = datei, werte, set(optional), zusatz, probleme

	def fehler(self, nr, text):
		self.p.append(f"{self.datei}:{nr}: {text}")

	# --- ::: wenn / sonst
	def bedingungen(self, zeilen):
		aus, stapel = [], []  # stapel: [schluessel, im_wenn_zweig, wert_gesetzt]
		for nr, z in enumerate(zeilen, 1):
			s = z.strip()
			m = re.fullmatch(r":::\s*wenn\s+([A-Z][A-Z0-9_]*)", s)
			if m:
				if m.group(1) not in self.optional:
					self.fehler(nr, f"'::: wenn' nur für optionale Angaben ({m.group(1)})")
				stapel.append([m.group(1), True, bool(self.werte.get(m.group(1)))])
				continue
			if s == "::: sonst":
				if not stapel or not stapel[-1][1]:
					self.fehler(nr, "'::: sonst' ohne '::: wenn'")
				else:
					stapel[-1][1] = False
				continue
			if s == ":::":
				if not stapel:
					self.fehler(nr, "':::' ohne '::: wenn'")
				else:
					stapel.pop()
				continue
			if s.startswith(":::"):
				self.fehler(nr, "unbekannte ':::'-Zeile")
				continue
			for k in PLATZHALTER.findall(z):
				if k in self.optional and not any(sk == k and wenn for sk, wenn, _ in stapel):
					self.fehler(nr, f"optionale Angabe [{k}] nur in '::: wenn {k}'")
			if all(gesetzt if wenn else not gesetzt for _, wenn, gesetzt in stapel):
				aus.append((nr, z))
		if stapel:
			self.fehler(len(zeilen), "'::: wenn' nicht mit ':::' geschlossen")
		return aus

	# --- Zeilen im Text
	def inline(self, nr, text):
		marken = []

		def platzhalter(m):
			k = m.group(1)
			if k not in self.werte and k not in self.zusatz:
				self.fehler(nr, f"unbekannter Platzhalter [{k}]")
				return m.group(0)
			marken.append(k)
			return f"\x00{len(marken) - 1}\x00"

		text = PLATZHALTER.sub(platzhalter, text)
		teile, pos = [], 0
		for m in re.finditer(r"\[([^\[\]]+)\]\(([^()\s]+)\)", text):
			teile.append(self.flach(nr, text[pos:m.start()]))
			if "\x00" in m.group(0):
				self.fehler(nr, "Platzhalter in einem Link")
			teile.append(f'<a href="{html.escape(self.url(nr, m.group(2)))}">{self.betonung(html.escape(m.group(1)))}</a>')
			pos = m.end()
		teile.append(self.flach(nr, text[pos:]))
		return re.sub("\x00(\\d+)\x00", lambda m: self.wert_html(marken[int(m.group(1))]), "".join(teile))

	def flach(self, nr, s):
		if "[" in s or "]" in s:
			self.fehler(nr, "eckige Klammer außerhalb eines Links oder Platzhalters")
		if "`" in s:
			self.fehler(nr, "Code (`) wird nicht unterstützt")
		if re.search(r"<[A-Za-z/!]", s):
			self.fehler(nr, "HTML wird nicht unterstützt")
		aus, pos = [], 0
		for m in re.finditer(r"https://[^\s<>\x00]+", s):
			url = m.group(0).rstrip(".,;:!?)")
			aus.append(self.betonung(html.escape(s[pos:m.start()])))
			aus.append(f'<a href="{html.escape(url)}">{html.escape(url)}</a>')
			pos = m.start() + len(url)
		aus.append(self.betonung(html.escape(s[pos:])))
		return "".join(aus)

	@staticmethod
	def betonung(s):
		s = re.sub(r"\*\*(?=\S)(.+?)(?<=\S)\*\*", r"<strong>\1</strong>", s)
		return re.sub(r"(?<![*\w])\*(?=\S)(.+?)(?<=\S)\*(?![*\w])", r"<em>\1</em>", s)

	def url(self, nr, u):
		if u.startswith("http://"):
			self.fehler(nr, "Link mit http:// (nur https://)")
		elif not re.match(r"(https://|mailto:|/)", u):
			self.fehler(nr, f"Link-Art nicht erlaubt: {u[:20]}")
		return u

	def wert_html(self, k):
		if k in self.zusatz:
			return self.zusatz[k]
		v = self.werte.get(k, "")
		if not v:
			return html.escape(f"[{k}]")  # fällt in der Prüfung der sichtbaren Texte auf
		if k.endswith("EMAIL"):
			return f'<a href="mailto:{html.escape(v)}">{html.escape(v)}</a>'
		return "<br>\n".join(html.escape(z) for z in v.splitlines())

	# --- Blöcke
	def html(self, quelltext):
		zeilen = self.bedingungen(quelltext.splitlines())
		aus, absatz, liste = [], [], []
		titel = None

		def zeilen_text(teile):
			s = ""
			for i, (nr, z) in enumerate(teile):
				umbruch = z.endswith("\\") or z.endswith("  ")
				z = z.rstrip().rstrip("\\").strip()
				s += self.inline(nr, z)
				if i < len(teile) - 1:
					s += "<br>\n" if umbruch else " "
			return s

		def absatz_zu():
			if absatz:
				aus.append(f"<p>{zeilen_text(absatz)}</p>")
				absatz.clear()

		def liste_zu():
			if liste:
				aus.append(self.liste_html(liste, zeilen_text))
				liste.clear()

		for nr, z in zeilen:
			s = z.strip()
			if not s:
				absatz_zu()
				liste_zu()
				continue
			if re.match(r"\s*(\||```|~~~|>|!\[|={3,}\s*$)", z) or re.match(r" {4,}\S", z) and not liste:
				self.fehler(nr, "Tabelle, Bild, Code, Zitat oder Einrückung werden nicht unterstützt")
				continue
			m = re.fullmatch(r"(#{1,6})\s+(.+?)\s*#*", s)
			if m:
				absatz_zu()
				liste_zu()
				stufe = len(m.group(1))
				if stufe > 3:
					self.fehler(nr, "nur Überschriften # bis ###")
				if stufe == 1:
					if titel is not None or aus:
						self.fehler(nr, "genau eine # Überschrift, ganz am Anfang")
					titel = self.inline(nr, m.group(2))
					aus.append(f"<h1>{titel}</h1>")
				else:
					aus.append(f"<h{stufe}>{self.inline(nr, m.group(2))}</h{stufe}>")
				continue
			if re.fullmatch(r"(-\s*){3,}|(\*\s*){3,}|(_\s*){3,}", s):
				absatz_zu()
				liste_zu()
				aus.append("<hr>")
				continue
			m = re.fullmatch(r"( *)([-*+]|\d{1,3}[.)])\s+(.*)", z.rstrip("\n"))
			if m:
				absatz_zu()
				liste.append([len(m.group(1)), "ol" if m.group(2)[0].isdigit() else "ul", [(nr, m.group(3))]])
				continue
			if liste:  # Fortsetzung des letzten Listenpunkts
				liste[-1][2].append((nr, z))
				continue
			absatz.append((nr, z))
		absatz_zu()
		liste_zu()
		if titel is None:
			self.fehler(1, "keine # Überschrift")
		return titel or "", "\n".join(aus)

	def liste_html(self, punkte, zeilen_text):
		aus, stapel = [], []  # stapel: (einrueckung, tag)
		for einr, tag, teile in punkte:
			if stapel and einr > stapel[-1][0]:
				if len(stapel) >= 3:
					self.fehler(teile[0][0], "höchstens drei Listen-Ebenen")
				aus.append(f"\n<{tag}>")
				stapel.append((einr, tag))
			else:
				while stapel and einr < stapel[-1][0]:
					aus.append(f"</li></{stapel.pop()[1]}>")
				if stapel and stapel[-1][1] != tag:
					aus.append(f"</li></{stapel.pop()[1]}>")
				if stapel:
					aus.append("</li>\n")
				else:
					aus.append(f"<{tag}>\n")
					stapel.append((einr, tag))
			aus.append(f"<li>{zeilen_text(teile)}")
		while stapel:
			aus.append(f"</li></{stapel.pop()[1]}>")
		return "".join(aus)


# ---------------------------------------------------------------- Seiten

class _NurText(HTMLParser):
	def __init__(self):
		super().__init__()
		self.t = []

	def handle_data(self, d):
		self.t.append(d)


def nur_text(h):
	p = _NurText()
	p.feed(h)
	return "".join(p.t)


def vorlage(werte):
	t = (VORLAGE / "seite.html").read_text(encoding="utf-8")
	for k, v in werte.items():
		t = t.replace("{{" + k + "}}", v)
	rest = re.findall(r"\{\{[A-Z_]+\}\}", t)
	if rest:
		raise Probleme([f"vorlage/seite.html: {rest[0]} nicht gefüllt"])
	return t


def seite(e, lang, titel, inhalt, pfad, gegen=None, fuss=""):
	url = e["studio"]["url"]
	kopf, sprache = "", ""
	if gegen:
		o = ANDERE[lang]
		kopf = (f'<link rel="canonical" href="{url}{pfad}">\n'
			f'<link rel="alternate" hreflang="{lang}" href="{url}{pfad}">\n'
			f'<link rel="alternate" hreflang="{o}" href="{url}{gegen}">')
		sprache = f'<a class="sprache" href="{gegen}" hreflang="{o}" lang="{o}">{TEXTE[o]["name"]}</a>'
	else:
		kopf = f'<link rel="canonical" href="{url}{pfad}">' if pfad != "/404.html" else ""
	return vorlage({
		"LANG": lang, "TITEL": html.escape(titel), "CSP": CSP, "KOPF": kopf, "STUDIO": html.escape(e["studio"]["name"]),
		"SPRACHE": sprache, "INHALT": inhalt, "FUSS": fuss,
	})


def fuss_html(lang, spiel=None):
	t = TEXTE[lang]
	links = [f'<a href="/">{t["start"]}</a>']
	if spiel:
		for thema, de, en in SPIEL_SEITEN:
			links.append(f'<a href="/{spiel["ordner"]}/{de if lang == "de" else en}/">{html.escape(spiel["name"])}: {t[thema]}</a>')
	de, en = STUDIO_SEITEN[0][1:]
	links.append(f'<a href="/{de if lang == "de" else en}/">{t["impressum"]}</a>')
	return f'<nav aria-label="{t["fuss"]}">' + " <span aria-hidden=\"true\">·</span> ".join(links) + "</nav>"


def baue(e, werte):
	"""Gibt ({veröffentlichter Pfad: bytes}, [Probleme]) zurück."""
	p = []
	pers = e["persoenlich"]
	studio = e["studio"]
	grund = {"STUDIO": html.escape(studio["name"]), "WEBSEITE": f'<a href="{studio["url"]}/">{html.escape(studio["url"])}/</a>'}
	dateien = {}

	def text_seite(quelle, lang, pfad, gegen, spiel=None):
		zusatz = dict(grund)
		if spiel:
			zusatz["SPIEL"] = html.escape(spiel["name"])
		datei = f"quellen/{quelle.parent.name}/{quelle.name}"
		if not quelle.exists():
			p.append(f"{datei} fehlt")
			return
		t = Text(datei, werte, pers["optional"], zusatz, p)
		titel, inhalt = t.html(quelle.read_text(encoding="utf-8-sig"))
		titel = nur_text(titel)
		voll = f"{titel} – {spiel['name']} – {studio['name']}" if spiel else f"{titel} – {studio['name']}"
		dateien[pfad.lstrip("/") + "index.html"] = seite(e, lang, voll, inhalt, pfad, gegen, fuss_html(lang, spiel))

	for _, de, en in STUDIO_SEITEN:
		text_seite(QUELLEN / STUDIO_ORDNER / f"{de}.md", "de", f"/{de}/", f"/{en}/")
		text_seite(QUELLEN / STUDIO_ORDNER / f"{en}.md", "en", f"/{en}/", f"/{de}/")
	for s in e["spiel"]:
		for _, de, en in SPIEL_SEITEN:
			text_seite(QUELLEN / s["ordner"] / f"{de}.md", "de", f"/{s['ordner']}/{de}/", f"/{s['ordner']}/{en}/", s)
			text_seite(QUELLEN / s["ordner"] / f"{en}.md", "en", f"/{s['ordner']}/{en}/", f"/{s['ordner']}/{de}/", s)

	# Startseite: oben Studio und Spiele (beide Sprachen), darunter je ein Block Deutsch und English.
	kurz = Text("quellen/webseite.toml", {}, [], grund, p)
	teile = [f"<h1>{html.escape(studio['name'])}</h1>",
		f'<p lang="de">{kurz.inline(0, studio["text_de"])}</p>',
		f'<p lang="en">{kurz.inline(0, studio["text_en"])}</p>',
		'<ul class="spiele">']
	for s in e["spiel"]:
		teile.append(f'<li><strong>{html.escape(s["name"])}</strong><br>\n'
			f'<span lang="de">{kurz.inline(0, s["text_de"])}</span><br>\n'
			f'<span lang="en">{kurz.inline(0, s["text_en"])}</span></li>')
	teile.append("</ul>\n<div class=\"bloecke\">")
	for lang in ("de", "en"):
		t = TEXTE[lang]
		block = [f'<section class="karte" lang="{lang}">', f"<h2>{t['name']}</h2>"]
		for s in e["spiel"]:
			block.append(f"<h3>{html.escape(s['name'])}</h3>\n<ul>")
			for thema, de, en in SPIEL_SEITEN:
				block.append(f'<li><a href="/{s["ordner"]}/{de if lang == "de" else en}/">{t[thema]}</a></li>')
			block.append("</ul>")
		de, en = STUDIO_SEITEN[0][1:]
		block.append(f"<h3>{html.escape(studio['name'])}</h3>\n<ul>"
			f'<li><a href="/{de if lang == "de" else en}/">{t["impressum"]}</a></li></ul>')
		block.append("</section>")
		teile += block
	teile.append("</div>")
	start_fuss = ('<nav aria-label="Rechtliches"><a href="/impressum/">Impressum</a> <span aria-hidden="true">·</span> '
		'<a href="/imprint/" lang="en">Legal notice</a></nav>')
	dateien["index.html"] = seite(e, "de", studio["name"], "\n".join(teile), "/", fuss=start_fuss)

	dateien["404.html"] = seite(e, "de", f"Seite nicht gefunden · Page not found – {studio['name']}", "\n".join([
		"<h1>Seite nicht gefunden</h1>",
		'<p>Diese Adresse gibt es nicht. <a href="/">Zur Startseite</a></p>',
		'<h2 lang="en">Page not found</h2>',
		'<p lang="en">This address does not exist. <a href="/">Go to the home page</a></p>']), "/404.html", fuss=start_fuss)

	dateien["site.css"] = (VORLAGE / "site.css").read_text(encoding="utf-8")
	pub = str(e.get("admob", {}).get("publisher_id", ""))
	if not re.fullmatch(r"pub-\d{16}", pub):
		p.append("webseite.toml: [admob] publisher_id fehlt noch (Form pub-0000000000000000)")
	dateien["app-ads.txt"] = f"google.com, {pub}, DIRECT, f08c47fec0942fa0\n"

	# Sicherheitsnetz: nichts Sichtbares mit eckigen Klammern, keine PC-Pfade.
	for pfad, inhalt in dateien.items():
		sichtbar = nur_text(inhalt) if pfad.endswith(".html") else inhalt
		if "[" in sichtbar or "]" in sichtbar:
			p.append(f"{pfad}: eckige Klammer im sichtbaren Text (Platzhalter übrig?)")
		if regeln.LOKALER_PFAD.search(inhalt):
			p.append(f"{pfad}: enthält einen Pfad eines PCs")
	return {k: v.encode("utf-8") for k, v in dateien.items()}, p


def schreibe(aus, dateien):
	aus = Path(aus).resolve()
	if aus == WURZEL or WURZEL in aus.parents:
		raise Probleme(["--aus darf nicht im Repo liegen (gebaute Seiten enthalten persönliche Angaben)"])
	if aus.exists():
		if any(aus.iterdir()) and not (aus / KENNUNG).exists():
			raise Probleme([f"--aus ist nicht leer und kein Bau-Ordner ({KENNUNG} fehlt) -- nichts geschrieben"])
		shutil.rmtree(aus)
	for pfad, inhalt in dateien.items():
		ziel = aus / pfad
		ziel.parent.mkdir(parents=True, exist_ok=True)
		ziel.write_bytes(inhalt)
	(aus / KENNUNG).write_text("Bau-Ordner von werkzeuge/bauen.py; wird bei jedem Bau ersetzt.\n", encoding="utf-8")


def main():
	sys.stdout.reconfigure(encoding="utf-8")
	a = argparse.ArgumentParser(description=__doc__.split("\n")[0])
	a.add_argument("--aus", required=True)
	quelle = a.add_mutually_exclusive_group(required=True)
	quelle.add_argument("--werte")
	quelle.add_argument("--werte-aus-umgebung", action="store_true")
	a.add_argument("--trotzdem", action="store_true", help="auch bei Problemen schreiben (nur lokale Vorschau)")
	args = a.parse_args()
	try:
		e = lade_einstellungen()
		werte, p = lade_werte(e, args.werte, args.werte_aus_umgebung)
		dateien, p2 = baue(e, werte)
		p += p2
		if p and not args.trotzdem:
			raise Probleme(p)
		schreibe(args.aus, dateien)
	except Probleme as x:
		for z in x.args[0]:
			print("PROBLEM:", z)
		print(f"BAU: ROT ({len(x.args[0])} Probleme) -- nichts geschrieben")
		return 1
	for z in p:
		print("PROBLEM:", z)
	print(f"BAU: {'ROT' if p else 'GRÜN'} -- {len(dateien)} Dateien geschrieben" + (" (trotzdem)" if p else ""))
	return 1 if p else 0


if __name__ == "__main__":
	sys.exit(main())
