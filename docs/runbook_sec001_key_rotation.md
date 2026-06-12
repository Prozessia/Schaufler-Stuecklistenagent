# Runbook SEC-001 — Azure-OpenAI-Key-Rotation + Git-Historie bereinigen

**Befund:** Im Initial-Commit des Repos wurde eine `.env` mit echtem `AZURE_OPENAI_KEY` eingecheckt. Die Datei wurde später entfernt, der Key liegt aber weiterhin in der Git-Historie (`git log --all -p -- .env`) — lokal, in allen Clones und auf dem GitHub-Remote.

**Diese Schritte kann nur ein Mensch mit Azure-/GitHub-Zugriff ausführen.**

## 1. Key sofort rotieren (zuerst! — unabhängig von der Historie)

1. Azure Portal → Azure OpenAI-Ressource → **Keys and Endpoint**.
2. **Regenerate Key 1** (und Key 2, falls beide je verwendet wurden).
3. Neuen Key in die Server-`.env` auf dem VPS eintragen (`AZURE_OPENAI_KEY=...`).
4. `docker compose -f docker-compose.prod.yml up -d backend` (Neustart zieht die neue .env).
5. Funktionstest: ein Test-PDF hochladen, Job muss durchlaufen.

## 2. Git-Historie bereinigen (danach)

> Erst nach der Rotation sinnvoll — und nur, wenn alle Beteiligten informiert sind: die Historie wird neu geschrieben, alle Clones müssen neu geholt werden.

```bash
pip install git-filter-repo
git clone --mirror <repo-url> repo-mirror && cd repo-mirror
git filter-repo --invert-paths --path .env
git push --force --all && git push --force --tags
```

Danach:
- Alle Entwickler-Clones löschen und neu klonen (alte Clones enthalten den Key weiter).
- GitHub: Settings → prüfen, ob Forks existieren (Forks behalten die alte Historie!).
- Offene PR-/Branch-Refs prüfen (`git for-each-ref`), Caches (GitHub "events"-API) altern von selbst aus.

## 3. Wiederholung verhindern

- `gitleaks` als CI-Schritt (siehe OPS-003-Ticket im BACKLOG).
- `.gitignore` deckt `.env`/`.env.*` bereits ab (verifiziert).
- Pre-commit-Hook optional: `gitleaks protect --staged`.

**Status-Tracking:** Nach Schritt 1 bitte Datum/Uhrzeit der Rotation hier eintragen: ____________
