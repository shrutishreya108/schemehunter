# GoDaddy launch checklist (organizer requirements → what satisfies them)

GoDaddy Inclusive Innovation Challenge 2026, Build Phase Nov 13–20. Each of the 7 organizer items below is
treated as a judged requirement. For every item: the file(s) that implement it, the exact steps, and how to
show evidence.

> **About the `gddy` CLI.** This doc deliberately gives no `gddy` flags or subcommands, because guessed syntax
> is worse than none. Where a step needs the CLI it says what to do and asks you to **find the command with
> `gddy --help` / `gddy tree`** (look under the domains, DNS or email command groups). Anything you can do in
> the CLI can also be done in the godaddy.com dashboard.

Placeholders: `<domain>` = your domain (e.g. `schemehunter.in`), `<vps-ip>` = the VPS's public IPv4,
`hello@<domain>` = the Titan mailbox.

---

## 1. VPS reachable by SSH key

| | |
|---|---|
| **Files** | `scripts/deploy.sh` (uses `ssh -o BatchMode=yes`, so it fails instead of falling back to a password prompt) |
| **Evidence** | `ssh -o PasswordAuthentication=no deploy@<vps-ip> 'hostname && docker --version'` succeeds |

Steps:
1. On your laptop: `ssh-keygen -t ed25519 -C "schemehunter-deploy"`. This creates `~/.ssh/id_ed25519` and `.pub`.
2. Add the **public** key to the VPS. Either paste it in the GoDaddy VPS dashboard's SSH-keys section,
   or, if you already have a session, append it to `~/.ssh/authorized_keys` (`chmod 600`).
3. Create a non-root deploy user in the `docker` group:
   `sudo adduser deploy && sudo usermod -aG docker deploy`, then copy the key into `/home/deploy/.ssh/`.
4. Confirm key login works in a **second** terminal before changing anything else.
5. Then, in `/etc/ssh/sshd_config`, set `PasswordAuthentication no` and `PermitRootLogin prohibit-password`,
   and run `sudo systemctl reload ssh`.
6. Firewall: `sudo ufw allow 22,80,443/tcp && sudo ufw enable`.

## 2. App packaged with Docker and running on the VPS

| | |
|---|---|
| **Files** | `Dockerfile`, `docker-compose.yml` (caddy, web, worker, postgres; all `restart: unless-stopped`; one uvicorn worker; worker with one thread), `.env.example`, `scripts/deploy.sh`, `Makefile` (`up`, `deploy`) |
| **Evidence** | `docker compose ps` on the VPS shows 4 services `running`; `curl -fsS https://<domain>/healthz` → `{"ok":true,"db":"ok"}` |

Steps (first time, on the VPS as `deploy`):
1. `sudo mkdir -p /srv/schemehunter && sudo chown deploy /srv/schemehunter`, then
   `git clone <repo-url> /srv/schemehunter`.
2. `cd /srv/schemehunter && cp .env.example .env && chmod 600 .env`, then fill it in (see item 7).
3. `docker compose up -d --build` (migrations run at web startup), then `make demo-reset` for demo data.

After that, every deploy from your laptop is:
`VPS=deploy@<vps-ip> DOMAIN=<domain> make deploy`.
That runs check-secrets → ssh → git pull → compose up → migrations → `/healthz`.

Sizing (4 vCPU / 8 GB): one web process (2 brain threads), one worker thread, and Postgres with defaults.
Expected memory is well under 1.5 GB. Check with `docker stats --no-stream`.

## 3. Own domain pointed at the VPS

| | |
|---|---|
| **Files** | `.env` → `DOMAIN=<domain>`, `APP_BASE_URL=https://<domain>`; `Caddyfile` reads `{$DOMAIN}` |
| **Evidence** | `dig +short <domain> A` returns `<vps-ip>` |

Steps:
1. Domain bought on godaddy.com with store credit, with DNS managed by GoDaddy.
2. Create or replace the **A record for `@`** (the apex) with value `<vps-ip>`, TTL 600.
   Optionally add `www` as a CNAME to `@`, or as a second A record.
   With the CLI: find the DNS record command with `gddy --help` / `gddy tree` (DNS group). The intent is
   "set A record `@` → `<vps-ip>` on `<domain>`".
3. **Don't touch the MX / TXT records that Titan created** (item 5).
4. Wait for propagation: `dig +short <domain> A` from your laptop.

## 4. HTTPS working

| | |
|---|---|
| **Files** | `Caddyfile` (automatic Let's Encrypt certificate, HSTS, nosniff), `docker-compose.yml` (ports 80/443, `caddy_data` volume keeps certificates across restarts) |
| **Evidence** | `curl -I https://<domain>/healthz` → `HTTP/2 200`; browser padlock; `curl -I http://<domain>` redirects to https |

Steps: none beyond items 2 and 3. Caddy requests the certificate on first boot once DNS points at the VPS
and ports 80/443 are open. If it fails, check `docker compose logs caddy` (the usual cause is DNS not
propagated yet, or ufw blocking port 80).

## 5. Mailbox on the domain (Titan Business Email)

| | |
|---|---|
| **Files** | `.env` → `SMTP_USER=hello@<domain>`, `EMAIL_FROM=hello@<domain>`, `CONTACT_TO=hello@<domain>` |
| **Evidence** | Log in to Titan webmail as `hello@<domain>` and show the inbox; `dig +short <domain> MX` shows Titan's MX hosts |

Steps:
1. Buy Titan Business Email for `<domain>` on godaddy.com and create the mailbox `hello@<domain>`, with a
   strong password stored in your password manager. With the CLI: find the email command with `gddy --help` /
   `gddy tree`; the intent is "create mailbox `hello` on `<domain>`".
2. Because DNS is on GoDaddy, setup normally adds the **MX**, **SPF (TXT)** and **DKIM** records
   automatically. Verify that they exist (CLI: list DNS records; find the command with `gddy tree`). They are
   what keep our outbound mail out of spam.
3. Log in to webmail once (some providers require that before SMTP works), and check that SMTP access is enabled
   for the mailbox in Titan's settings.

## 6. Contact form on the site that sends to that mailbox over SMTP

| | |
|---|---|
| **Files** | `app/routes/contact.py` (GET/POST `/contact`; honeypot; 5/hour per IP; stores `messages(channel='web', direction='in')`; thank-you page), `app/templates/contact.html`, `contact_thanks.html`, `app/mouth/email.py` (smtplib, STARTTLS on 587 with SSL-465 fallback), `tests/test_contact.py` (fake SMTP server) |
| **Evidence** | Submit `https://<domain>/contact` → "Thank you!" page → the email arrives in `hello@<domain>` with Reply-To = the sender |

SMTP settings (`.env`):

```
SMTP_HOST=smtpout.secureserver.net   # GoDaddy's relay for Titan mailboxes; alternative: smtp.titan.email
SMTP_PORT=587                        # STARTTLS; if STARTTLS fails the adapter retries SSL on 465
SMTP_USER=hello@<domain>             # full email address
SMTP_PASSWORD=<mailbox password>     # only in .env on the VPS
EMAIL_FROM=hello@<domain>
CONTACT_TO=hello@<domain>
```

**Test-email step** (do this before the form test, so SMTP problems show up on their own):

```sh
docker compose exec web python -c "from app.mouth.email import SmtpEmail; \
print(SmtpEmail.from_settings().send('hello@<domain>', 'SMTP test from the VPS', subject='Scheme Hunter SMTP test'))"
```

- **It prints a Message-ID:** SMTP works. Now submit the contact form.
- **`smtp auth failed`:** check `SMTP_USER` is the full address and the password is right, then try `SMTP_HOST=smtp.titan.email`.
- **`smtp send failed: TimeoutError`:** some VPS providers block outbound 587/465. Check with `nc -vz smtpout.secureserver.net 587` and ask GoDaddy support to open it.

If sending fails, the form still stores the message (`processed=false`), so nothing is lost.

Sign-in codes and one-tap links use the same SMTP settings (see `app/signin/mail.py`). If they don't arrive, check `/admin/actions` for the recorded error.

## 7. SMTP password (and every secret) kept out of code and repo

| | |
|---|---|
| **Files** | `.gitignore` (`.env`, `/data`), `.env.example` (keys only, empty secrets), `app/config.py` (reads env), `app/mouth/email.py` (password excluded from `repr`, never logged, never in error text: tested in `tests/mouth/test_email.py`), `scripts/check_secrets.sh` + `make check-secrets` (tested in `tests/test_scripts.py`) |
| **Evidence** | `make check-secrets` → `check-secrets: ok`; `git ls-files .env` prints nothing; `grep -rn SMTP_PASSWORD app/` shows only the settings field |

Steps:
1. Secrets live only in `/srv/schemehunter/.env` on the VPS (`chmod 600`, owned by `deploy`).
2. Run `make check-secrets` before every push (`make deploy` runs it automatically). It fails on:
   - an Anthropic key pattern anywhere in the tree
   - the SMTP password value appearing outside `.env`
   - any other secret-looking `.env` value appearing outside `.env`
   - `.env` being tracked by git
3. Rotate immediately if a secret ever lands in git history: mailbox password in Titan, API key on
   platform.claude.com.
