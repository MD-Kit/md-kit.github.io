#!/usr/bin/env python3
"""Git-Haken für dieses öffentliche Repo. Einrichten: python werkzeuge/haken/installieren.py

  vorher               (pre-commit) nur erlaubte Pfade; Autor und Committer mit noreply-Adresse; kein Pfad
                       eines PCs und kein persönlicher Wert in den vorgemerkten Dateien
  nachricht <datei>    (commit-msg) keine Zeilen mit Co-Authored-By, Claude-Session oder Generated with

Die persönlichen Werte liest der Haken aus der Wertedatei, deren Pfad nur in .git/config steht
(git config --local webseite.werte <datei>); er gibt nie einen Wert aus.
"""
import re
import subprocess
import sys
from pathlib import Path

sys.dont_write_bytecode = True  # keine __pycache__-Ordner im Repo-Ordner
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import regeln  # noqa: E402


def git(*args, check=True):
	return subprocess.run(["git", *args], capture_output=True, text=True, encoding="utf-8", errors="replace", check=check).stdout


def vorher():
	p = []
	for wer in ("GIT_AUTHOR_IDENT", "GIT_COMMITTER_IDENT"):
		m = re.search(r"<([^>]*)>", git("var", wer))
		if not m or not regeln.NOREPLY.fullmatch(m.group(1)):
			p.append(f"{wer}: keine noreply-Adresse (git config --local user.email setzen)")
	pfade = [z for z in git("diff", "--cached", "--name-only", "--diff-filter=ACMRT", "-z").split("\0") if z]
	for f in pfade:
		if not regeln.erlaubt(f):
			p.append(f"nicht erlaubter Pfad: {f}")
	werte = {}
	datei = git("config", "--local", "webseite.werte", check=False).strip()
	if datei:
		if Path(datei).exists():
			werte, _ = regeln.lies_werte_datei(Path(datei).read_text(encoding="utf-8-sig"))
		else:
			p.append("webseite.werte zeigt auf eine fehlende Datei")
	for f in pfade:
		t = git("show", f":{f}")
		if regeln.LOKALER_PFAD.search(t):
			p.append(f"{f}: enthält einen Pfad eines PCs")
		for k, v in werte.items():
			for teil in v.replace("\\n", "\n").splitlines():
				teil = teil.strip()
				if len(teil) >= regeln.MIN_WERTLAENGE and teil.lower() in t.lower():
					p.append(f"{f}: enthält den Wert von {k}")
	return p


def nachricht(datei):
	t = Path(datei).read_text(encoding="utf-8", errors="replace")
	zeilen = [z for z in t.splitlines() if not z.startswith("#")]
	return ["Commit-Nachricht enthält Co-Authored-By, Claude-Session oder Generated with"] \
		if any(regeln.VERBOTEN_IN_NACHRICHT.search(z) for z in zeilen) else []


def main():
	sys.stdout.reconfigure(encoding="utf-8")
	p = vorher() if sys.argv[1] == "vorher" else nachricht(sys.argv[2])
	for z in p:
		print("COMMIT ABGELEHNT:", z)
	return 1 if p else 0


if __name__ == "__main__":
	sys.exit(main())
