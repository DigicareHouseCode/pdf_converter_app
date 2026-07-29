# Deployment

Pushing to `main` triggers [.github/workflows/deploy.yml](.github/workflows/deploy.yml), which SSHes
into the VPS, pulls the new commit, reinstalls dependencies, and restarts the service. If the
service fails to come up, the deploy automatically rolls back to the previous commit.

The steps below are **one-time setup**. Run them on the VPS as `root` unless noted.

---

## 1. Let the VPS pull from the private repo (deploy key)

The repo is private, so the server needs its own read-only key.

```bash
ssh-keygen -t ed25519 -C "vps-deploy" -f ~/.ssh/github_deploy -N ""
cat ~/.ssh/github_deploy.pub
```

Copy that public key into GitHub:
**Repo → Settings → Deploy keys → Add deploy key**. Paste it, name it `vps`, and leave
*Allow write access* **unchecked**.

Tell SSH to use it for GitHub:

```bash
cat >> ~/.ssh/config <<'EOF'
Host github.com
    HostName github.com
    User git
    IdentityFile ~/.ssh/github_deploy
    IdentitiesOnly yes
EOF
chmod 600 ~/.ssh/config

# Verify - expect "successfully authenticated, but GitHub does not provide shell access"
ssh -T git@github.com
```

## 2. Turn the existing folder into a git clone

Your `pdf_converter_app` folder already has the code and, crucially, a live `.env`, plus
`uploads/` and `outputs/`. This attaches git history **without touching those** — they are
gitignored, so `git reset --hard` leaves them alone.

```bash
cd /root/pdf_converter_app          # adjust if your path differs

cp .env /root/env.backup            # paranoia, in case something goes wrong

git init -b main
git remote add origin git@github.com:DigicareHouseCode/pdf_converter_app.git
git fetch origin
git reset --hard origin/main

ls -la .env && echo ".env survived"
```

> `git reset --hard` discards uncommitted edits to **tracked** files. If you hand-edited any
> `.py` or template directly on the server, copy those changes into the repo and commit them
> first, or they will be overwritten here and on every future deploy.

## 3. Create the systemd service

Skip if you already have one — just note its name for step 5. Otherwise:

```bash
cat > /etc/systemd/system/pdf_converter.service <<'EOF'
[Unit]
Description=PDF Converter Flask app
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=/root/pdf_converter_app
EnvironmentFile=/root/pdf_converter_app/.env
ExecStart=/root/pdf_converter_app/venv/bin/gunicorn \
    --workers 3 \
    --bind 127.0.0.1:8000 \
    --timeout 600 \
    app:app
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable --now pdf_converter
systemctl status pdf_converter
```

The `--timeout 600` matters: PDF extraction through Gemini can take minutes, and gunicorn's
default 30s would kill those requests.

> `EnvironmentFile` does not understand quotes or `#` the way a shell does. Your `.env`
> contains values with `#` in them (`SECRET_KEY`, `DB_PASSWORD`), which systemd reads
> literally — that happens to be correct here, but if you ever wrap a value in quotes,
> systemd will keep the quotes. If this bites you, drop `EnvironmentFile` and let
> `python-dotenv` load `.env` from `WorkingDirectory` instead.

## 4. Give GitHub Actions SSH access to the VPS

Generate a **separate** keypair for CI (do this on your local machine or the VPS):

```bash
ssh-keygen -t ed25519 -C "github-actions" -f ~/.ssh/gha_deploy -N ""
```

Authorize the public half on the VPS:

```bash
cat ~/.ssh/gha_deploy.pub >> /root/.ssh/authorized_keys
chmod 600 /root/.ssh/authorized_keys
```

Then get the values for step 5:

```bash
cat ~/.ssh/gha_deploy        # -> VPS_SSH_KEY (the whole thing, BEGIN/END lines included)
ssh-keyscan -H <your-vps-ip> # -> VPS_KNOWN_HOSTS
```

## 5. Configure the repo

**Repo → Settings → Secrets and variables → Actions**

Secrets tab:

| Secret | Value | Required |
|---|---|---|
| `VPS_HOST` | VPS IP or hostname | yes |
| `VPS_USER` | `root` | yes |
| `VPS_SSH_KEY` | Full private key from `~/.ssh/gha_deploy` | yes |
| `VPS_PORT` | SSH port, if not 22 | no |
| `VPS_KNOWN_HOSTS` | Output of `ssh-keyscan` | no, but recommended |

Variables tab — only set these if your paths differ from the defaults:

| Variable | Default |
|---|---|
| `DEPLOY_PATH` | `/root/pdf_converter_app` |
| `SERVICE_NAME` | `pdf_converter` |
| `VENV_PATH` | `/root/pdf_converter_app/venv` |
| `HEALTH_URL` | *(unset — health check is skipped)* |

Set `HEALTH_URL` to a real URL (e.g. `https://yourdomain.com/`) to make the deploy verify the
site actually responds with 200, not just that the process is alive.

## 6. Test it

Trigger a run manually first: **Actions → Deploy to VPS → Run workflow**. That way you're not
debugging SSH config and a code change at the same time.

---

## Operating notes

**Rollback is automatic.** If `systemctl restart` leaves the service dead, the workflow resets
to the previous commit, reinstalls, restarts, dumps the last 60 journal lines into the Actions
log, and fails the run. The site stays up on the old code.

**Rolling back by hand:**

```bash
cd /root/pdf_converter_app
git reset --hard <old-sha>
venv/bin/pip install -r requirements.txt
systemctl restart pdf_converter
```

Note the next push to `main` will move the server back to the tip of `main`. To undo a bad
commit properly, `git revert` it and push.

**The server is not the place to edit code.** Every deploy does `git reset --hard`, so
server-side edits to tracked files are silently destroyed. Change things locally, commit, push.

**`.env` is deliberately not deployed.** It's gitignored and lives only on the server. When you
add a new variable, add it to [.env.example](.env.example) in the repo *and* to the real `.env`
on the VPS by hand — otherwise the deploy will succeed and the app will fail at runtime.

**Database schema changes are not automated.** There's no migration framework here
(`init_database.py` / `update_db.py` are manual scripts). If a commit changes `models.py`, SSH
in and run the appropriate script yourself after the deploy.
