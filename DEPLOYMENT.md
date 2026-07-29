# Deployment

Pushing to `main` triggers [.github/workflows/deploy.yml](.github/workflows/deploy.yml), which SSHes
into the VPS, pulls the new commit, reinstalls dependencies, and restarts the service. If the
service fails to come up, the deploy automatically rolls back to the previous commit.

**There is no manual setup to do on the VPS.** The first deploy bootstraps the server itself:
it detects that `/var/www/pdf_converter_app` isn't a git repo, runs `git init`, adds the
remote, and adopts the existing directory as a working tree. No deploy key is needed — the
workflow forwards its own `GITHUB_TOKEN`, which expires when the job ends, so no credential is
ever stored on the server or written into `.git/config`.

The only configuration lives in the repo settings (section 2).

---

## 1. Supervisor program (already configured)

The app runs under supervisor as `pdf_app`:

```ini
[program:pdf_app]
directory=/var/www/pdf_converter_app
command=/var/www/pdf_converter_app/venv/bin/gunicorn -w 3 -b 127.0.0.1:8000 app:app
user=root
autostart=true
autorestart=true
stderr_logfile=/var/log/pdf_app.err.log
stdout_logfile=/var/log/pdf_app.out.log
```

`.env` is picked up because `directory=` makes it the working directory and `app.py` calls
`load_dotenv()` — no `environment=` needed.

**One change worth making:** add `--timeout 600` to the gunicorn command.

```ini
command=/var/www/pdf_converter_app/venv/bin/gunicorn -w 3 -b 127.0.0.1:8000 --timeout 600 app:app
```

Gunicorn's default worker timeout is 30 seconds. PDF extraction through Gemini routinely takes
longer, and when it does, gunicorn kills the worker mid-request — the user sees a truncated
response or a 502, with nothing obviously wrong in the app logs. Apply it with:

```bash
supervisorctl reread && supervisorctl update && supervisorctl restart pdf_app
```

## 2. GitHub Actions SSH access to the VPS

Generate a keypair for CI (do this on your local machine or the VPS):

```bash
ssh-keygen -t ed25519 -C "github-actions" -f ~/.ssh/gha_deploy -N ""
```

Authorize the public half on the VPS:

```bash
cat ~/.ssh/gha_deploy.pub >> /root/.ssh/authorized_keys
chmod 600 /root/.ssh/authorized_keys
```

Then get the values for the next section:

```bash
cat ~/.ssh/gha_deploy        # -> VPS_SSH_KEY (the whole thing, BEGIN/END lines included)
ssh-keyscan -H <your-vps-ip> # -> VPS_KNOWN_HOSTS
```

## 3. Configure the repo

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
| `DEPLOY_PATH` | `/var/www/pdf_converter_app` |
| `SERVICE_NAME` | `pdf_app` (the supervisor program name) |
| `VENV_PATH` | `/var/www/pdf_converter_app/venv` |
| `HEALTH_URL` | *(unset — health check is skipped)* |

Set `HEALTH_URL` to a real URL (e.g. `https://yourdomain.com/`) to make the deploy verify the
site actually responds with 200, not just that the process is alive.

## 4. Test it

Trigger a run manually first: **Actions → Deploy to VPS → Run workflow**. That way you're not
debugging SSH config and a code change at the same time.

On the very first run you'll see `First deploy: initializing git repo`. That run replaces
every **tracked** file with the repo's version. `.env`, `uploads/`, `outputs/`, and
`instance/` are gitignored and are left exactly as they are — but if you hand-edited any `.py`
or template directly on the server, those edits are overwritten. Get them into the repo first
if you want to keep them.

---

## Operating notes

**Rollback is automatic.** If the program isn't `RUNNING` after restart, the workflow resets
to the previous commit, reinstalls, restarts, dumps the tail of `pdf_app` stderr into the
Actions log, and fails the run. The site stays up on the old code.

**Rolling back by hand:**

```bash
cd /var/www/pdf_converter_app
git reset --hard <old-sha>
venv/bin/pip install -r requirements.txt
supervisorctl restart pdf_app
```

**Reading logs:** `supervisorctl tail -f pdf_app stderr`, or directly at
`/var/log/pdf_app.err.log` and `/var/log/pdf_app.out.log`.

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
