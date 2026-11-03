#!/usr/bin/env bash
# SafeCell bootstrap — run this ON the fresh Ubuntu VM as root (or via sudo).
#
#   bash bootstrap.sh <public-repo-url>
#
# It installs bubblewrap + python, creates a locked-down service user, installs
# SafeCell, starts it under systemd on 127.0.0.1:8000, and prints the health
# check. TLS is a separate, optional step (see Caddyfile).
set -euo pipefail

REPO_URL="${1:-}"
APP_DIR=/opt/safecell
SVC_USER=safecell

if [[ -z "$REPO_URL" ]]; then
  echo "usage: bash bootstrap.sh <public-git-repo-url>" >&2
  exit 1
fi

echo "==> installing packages"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -yqq bubblewrap python3-venv python3-pip git curl ufw

echo "==> creating service user"
id -u "$SVC_USER" >/dev/null 2>&1 || useradd --system --create-home --shell /usr/sbin/nologin "$SVC_USER"

echo "==> fetching SafeCell"
rm -rf "$APP_DIR"
git clone --depth 1 "$REPO_URL" "$APP_DIR"
chown -R "$SVC_USER:$SVC_USER" "$APP_DIR"

echo "==> python env"
sudo -u "$SVC_USER" python3 -m venv "$APP_DIR/.venv"
sudo -u "$SVC_USER" "$APP_DIR/.venv/bin/pip" install -q --upgrade pip
sudo -u "$SVC_USER" "$APP_DIR/.venv/bin/pip" install -q -r "$APP_DIR/requirements.txt"

echo "==> verifying the sandbox actually works on this host"
if ! sudo -u "$SVC_USER" "$APP_DIR/.venv/bin/python" -m safecell.cli doctor; then
  echo "!! bubblewrap cannot create namespaces on this VM." >&2
  echo "!! On some providers you must enable unprivileged userns:" >&2
  echo "!!   sysctl -w kernel.unprivileged_userns_clone=1" >&2
  echo "!! SafeCell will refuse to execute until this is fixed (by design)." >&2
  exit 2
fi

echo "==> env file"
if [[ ! -f "$APP_DIR/.env" ]]; then
  cat > "$APP_DIR/.env" <<'ENV'
# Fill these in to use a real model. Without them the deterministic
# ScriptedPlanner handles the built-in demo tasks.
SAFECELL_LLM_BASE_URL=https://api.vultrinference.com/v1
SAFECELL_MODEL=
SAFECELL_API_KEY=
ENV
  chown "$SVC_USER:$SVC_USER" "$APP_DIR/.env"
  chmod 600 "$APP_DIR/.env"
fi

echo "==> systemd unit"
install -m 644 "$APP_DIR/deploy/safecell.service" /etc/systemd/system/safecell.service
systemctl daemon-reload
systemctl enable --now safecell
sleep 2

echo "==> firewall"
ufw allow OpenSSH >/dev/null 2>&1 || true
ufw allow 80/tcp  >/dev/null 2>&1 || true
ufw allow 443/tcp >/dev/null 2>&1 || true
yes | ufw enable >/dev/null 2>&1 || true

echo "==> health check"
curl -fsS http://127.0.0.1:8000/api/health | python3 -m json.tool || {
  echo "service did not come up; journalctl -u safecell -n 50" >&2
  exit 3
}

echo
echo "SafeCell is running on 127.0.0.1:8000."
echo "Expose it: install Caddy (https://caddyserver.com/docs/install) and use deploy/Caddyfile,"
echo "or proxy through whatever you already run. Keep the 127.0.0.1 bind — do not expose :8000 directly."
