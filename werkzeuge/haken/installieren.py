#!/usr/bin/env python3
"""Richtet die Git-Haken pre-commit und commit-msg in .git/hooks ein (einmal nach dem Klonen)."""
from pathlib import Path

WURZEL = Path(__file__).resolve().parent.parent.parent
HAKEN = {
	"pre-commit": '#!/bin/sh\nexec python werkzeuge/haken/commit_pruefung.py vorher\n',
	"commit-msg": '#!/bin/sh\nexec python werkzeuge/haken/commit_pruefung.py nachricht "$1"\n',
}
for name, inhalt in HAKEN.items():
	ziel = WURZEL / ".git" / "hooks" / name
	ziel.write_text(inhalt, encoding="utf-8", newline="\n")
	ziel.chmod(0o755)
	print("eingerichtet:", ".git/hooks/" + name)
