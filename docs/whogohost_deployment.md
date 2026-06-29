# Hosting Daikot Worktools on Whogohost

This app is a Python FastAPI application. For Whogohost, use a Linux VPS, not ordinary shared/WordPress hosting, because the app needs a long-running Python server process.

## Recommended Setup

- Domain: buy or point a domain such as `statement.yourdomain.com`.
- Hosting: choose a Whogohost Linux VPS so you have root access and can install Python, Nginx, and systemd services.
- App server: run Uvicorn on `127.0.0.1:8011`.
- Reverse proxy: use Nginx to expose the app on your domain.
- SSL: use Certbot/Let's Encrypt after the domain points to the VPS.
- Runtime files: store uploads and generated Excel files outside `/tmp` with `STATEMENT_ANALYZER_RUNTIME_DIR=/var/lib/statement-analyzer`.

## Server Setup

SSH into the VPS, then install the base packages:

```bash
sudo apt update
sudo apt install -y python3.11 python3.11-venv python3-pip nginx git certbot python3-certbot-nginx
```

Create an app user and folders:

```bash
sudo useradd --system --create-home --shell /usr/sbin/nologin statement-analyzer
sudo mkdir -p /opt/statement-analyzer /var/lib/statement-analyzer
sudo chown -R statement-analyzer:www-data /opt/statement-analyzer /var/lib/statement-analyzer
```

Upload or clone this project into `/opt/statement-analyzer`, then install dependencies:

```bash
cd /opt/statement-analyzer
sudo -u statement-analyzer python3 -m venv .venv
sudo -u statement-analyzer .venv/bin/pip install --upgrade pip
sudo -u statement-analyzer .venv/bin/pip install -r requirements.txt
```

## Run With Systemd

Copy the service template:

```bash
sudo cp deploy/statement-analyzer.service.example /etc/systemd/system/statement-analyzer.service
sudo systemctl daemon-reload
sudo systemctl enable --now statement-analyzer
sudo systemctl status statement-analyzer
```

Check the local health endpoint:

```bash
curl http://127.0.0.1:8011/health
```

## Put It On Your Domain

Point your domain DNS `A` record to the VPS public IP address.

Copy the Nginx template and replace `example.com` with your real domain:

```bash
sudo cp deploy/nginx-statement-analyzer.conf.example /etc/nginx/sites-available/statement-analyzer
sudo nano /etc/nginx/sites-available/statement-analyzer
sudo ln -s /etc/nginx/sites-available/statement-analyzer /etc/nginx/sites-enabled/statement-analyzer
sudo nginx -t
sudo systemctl reload nginx
```

Enable HTTPS:

```bash
sudo certbot --nginx -d example.com -d www.example.com
```

After that, visitors can open:

```text
https://example.com
```

## Updating The App Later

Upload the new files or pull the latest code, then restart:

```bash
cd /opt/statement-analyzer
sudo -u statement-analyzer .venv/bin/pip install -r requirements.txt
sudo systemctl restart statement-analyzer
```

## Important Notes

- Avoid uploading sensitive customer statements unless you control who can access the website.
- Add login/authentication before making the public URL widely available.
- Increase `client_max_body_size` in the Nginx config if users need to upload large PDFs.
- Back up `/var/lib/statement-analyzer` if you want to keep generated files.
