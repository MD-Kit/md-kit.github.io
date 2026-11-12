"""Gemeinsame Regeln für bauen.py, pruefen.py und die Git-Haken (nur Python-Standardbibliothek).

Das Repo ist öffentlich. Hinein kommen nur Texte mit Platzhaltern, die Vorlage, die Werkzeuge,
der Workflow und .gitignore -- nie persönliche Angaben, Notizen, Bildschirmfotos oder Pfade eines PCs.
"""
import re

# Nur diese Pfade dürfen im Repo stehen (Ordner enden mit "/").
ERLAUBT = ("quellen/", "vorlage/", "werkzeuge/", ".github/workflows/", ".gitignore")

# Pfade eines PCs: Laufwerksbuchstabe mit Pfad, Benutzerordner unter Windows, macOS und Linux,
# Git-Bash-Schreibweise. Gesucht wird in jeder Datei des Repos und in jeder gebauten Seite.
LOKALER_PFAD = re.compile(
	r"(?<![A-Za-z0-9])[A-Za-z]:[\\/]"
	r"|[\\/]Users[\\/]"
	r"|[\\/]home[\\/][a-z]"
	r"|(?<![A-Za-z0-9])/[a-z]/Users(?=[\\/])"
	r"|[\\/]AppData[\\/]",
	re.IGNORECASE,
)

# Zeilen, die nie in einer Commit-Nachricht stehen dürfen.
VERBOTEN_IN_NACHRICHT = re.compile(r"co-authored-by|claude-session|generated with", re.IGNORECASE)

# Adresse für Autor und Committer.
NOREPLY = re.compile(r"^\d+\+[A-Za-z0-9-]+@users\.noreply\.github\.com$")

# Persönliche Werte unter dieser Länge werden beim Suchen im Repo nicht beachtet (zu viele Zufallstreffer).
MIN_WERTLAENGE = 4


def erlaubt(pfad):
	return any(pfad == e or (e.endswith("/") and pfad.startswith(e)) for e in ERLAUBT)


def lies_werte_datei(text):
	"""KEY=Wert je Zeile, # Kommentar, \\n im Wert = Zeilenumbruch. Gibt (dict, fehler) zurück; nie Werte in Meldungen."""
	werte, fehler = {}, []
	for nr, zeile in enumerate(text.splitlines(), 1):
		z = zeile.strip()
		if not z or z.startswith("#"):
			continue
		if "=" not in z:
			fehler.append(f"Wertedatei Zeile {nr}: KEY=Wert erwartet")
			continue
		k, v = z.split("=", 1)
		k = k.strip()
		if not re.fullmatch(r"[A-Z][A-Z0-9_]*", k):
			fehler.append(f"Wertedatei Zeile {nr}: ungültiger Schlüssel")
			continue
		if k in werte:
			fehler.append(f"Wertedatei Zeile {nr}: {k} doppelt")
		werte[k] = v.strip()
	return werte, fehler
