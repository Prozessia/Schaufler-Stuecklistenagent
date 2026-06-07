# Deployment auf einem Linux-VPS (Docker + Caddy)

Stack: **FastAPI-Backend** (v1.0) + **Next.js-Frontend** (Login/Statistik) +
**Caddy** (automatisches HTTPS). Eine Domain, same-origin → das Cookie-Login
(admin/admin) funktioniert.

Branch zum Deployen: **`deploy/vps-v1`** (enthält Backend **und** Frontend in
einem Repo — daher reicht ein einfacher `git clone`).

---

## 0. Voraussetzungen auf dem VPS (Ubuntu/Debian)

```bash
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker $USER   # danach neu einloggen
sudo ufw allow 80 && sudo ufw allow 443
```
DNS: **A-Record** deiner Domain (z. B. `bom.example.com`) → VPS-IP.

---

## 1. Repo klonen (Branch `deploy/vps-v1`)

```bash
cd /opt
sudo mkdir -p stuecklistenagent && sudo chown $USER stuecklistenagent
git clone -b deploy/vps-v1 https://github.com/Prozessia/Schaufler-Stuecklistenagent.git stuecklistenagent
cd stuecklistenagent
```
(Privates Repo → bei der Abfrage GitHub-Username + **Personal Access Token** als
Passwort eingeben.)

---

## 2. .env anlegen

```bash
cp .env.deploy.example .env
nano .env
```
Setzen: `DOMAIN`, `ACME_EMAIL`, `AZURE_OPENAI_ENDPOINT`, `AZURE_OPENAI_KEY`,
und **`LOGIN_ADMIN_PASSWORD`** (nicht „admin" lassen!). `.env` bleibt lokal auf
dem VPS (ist in `.gitignore`, kommt nie ins Repo).

---

## 3. Starten

```bash
chmod +x deploy.sh
./deploy.sh
```
Baut beide Images (Frontend backt `NEXT_PUBLIC_API_URL = https://$DOMAIN` ein),
startet Backend + Frontend + Caddy. Caddy holt das HTTPS-Zertifikat automatisch.

→ **https://<deine-domain>** · Login **admin / admin** (bzw. dein Passwort).

---

## 4. Updates einspielen

```bash
cd /opt/stuecklistenagent
git pull
./deploy.sh
```
`data/` (Jobs/Uploads/Exporte) bleibt erhalten.

---

## Architektur

```
Browser ──HTTPS──> Caddy (:443)
                     ├── /auth /jobs /upload /feedback /stats /settings ─> backend:8000
                     └── alles andere ────────────────────────────────> frontend:3000
```
Backend/Frontend sind **nicht** direkt öffentlich (`expose`, nicht `ports`) —
nur Caddy. Same-origin → Login-Cookie funktioniert.

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| `unauthorized` / Login klappt nicht | Nach `.env`-Änderung Frontend neu bauen: `docker compose -f docker-compose.prod.yml build frontend && docker compose -f docker-compose.prod.yml up -d`. |
| TLS-Zertifikat kommt nicht | DNS-A-Record / Ports 80+443 prüfen: `docker compose -f docker-compose.prod.yml logs caddy`. |
| Azure-Fehler / Jobs scheitern | Keys/Deployment-Namen in `.env`: `docker compose -f docker-compose.prod.yml logs backend`. |
| Frontend zeigt `localhost:8000` | `NEXT_PUBLIC_API_URL` war beim Build leer → `docker compose -f docker-compose.prod.yml build --no-cache frontend`. |

Logs: `docker compose -f docker-compose.prod.yml logs -f` ·
Stoppen: `docker compose -f docker-compose.prod.yml down`

---

## Hinweis zur Repo-Struktur

Das Frontend wurde aus seinem früheren separaten Repo in dieses Hauptrepo
**eingefaltet** (Branch `deploy/vps-v1`), damit ein einzelner `git clone` reicht.
Die alte separate Frontend-Git-Historie liegt als Backup unter `$HOME` auf dem
Dev-Rechner (`frontend-dotgit-backup-*`).
