#!/usr/bin/env bash
# SafeCell bootstrap — run this ON a fresh Ubuntu VM as root.
#
#   bash bootstrap.sh <public-repo-url>                      # localhost:8000
#   bash bootstrap.sh <public-repo-url> --public             # 0.0.0.0:8080
#   bash bootstrap.sh <public-repo-url> --public demo.sslip.io   # Caddy + TLS on 443
#
# Installs bubblewrap + python, creates a locked-down service user, installs
# SafeCell, starts it under systemd, and prints the health check.
#
# Why the app never binds :80 itself: giving the service CAP_NET_BIND_SERVICE
# makes bubblewrap abort with "Unexpected capabilities but not setuid", because
# a bwrap process holding capabilities is treated as a misconfigured setuid
# binary. So the app stays on a high port and a proxy owns 80/443.
set -euo pipefail

REPO_URL="${1:-}"
MODE="${2:-}"
DOMAIN="${3:-}"
APP_DIR=/opt/safecell
SVC_USER=safecell

if [[ -z "$REPO_URL" ]]; then
  echo "usage: bash bootstrap.sh <public-git-repo-url> [--public [domain]]" >&2
  exit 1
fi

if [[ "$MODE" == "--public" && -n "$DOMAIN" ]]; then
  WANT_HOST=127.0.0.1
  WANT_PORT=8000
elif [[ "$MODE" == "--public" ]]; then
  WANT_HOST=0.0.0.0
  WANT_PORT=8080
else
  WANT_HOST=127.0.0.1
  WANT_PORT=8000
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
# Ubuntu 23.10+ ships kernel.apparmor_restrict_unprivileged_userns=1, which makes
# bubblewrap's clone(CLONE_NEWUSER) fail. Lift it and persist the setting.
if sysctl -n kernel.apparmor_restrict_unprivileged_userns 2>/dev/null | grep -q 1; then
  echo "    lifting apparmor userns restriction"
  sysctl -qw kernel.apparmor_restrict_unprivileged_userns=0 || true
  grep -q '^kernel.apparmor_restrict_unprivileged_userns' /etc/sysctl.conf 2>/dev/null \
    || echo 'kernel.apparmor_restrict_unprivileged_userns=0' >> /etc/sysctl.conf
fi

# Run from the project root: the venv has the deps but the package itself is
# imported from the working directory, not installed into site-packages.
if ! ( cd "$APP_DIR" && sudo -u "$SVC_USER" ./.venv/bin/python -m safecell.cli doctor ); then
  echo "!! the sandbox self-check failed on this host." >&2
  echo "!! If the message above is 'no namespaces', enable unprivileged userns:" >&2
  echo "!!   sysctl -w kernel.apparmor_restrict_unprivileged_userns=0" >&2
  echo "!! SafeCell refuses to execute rather than running unsandboxed (by design)." >&2
  exit 2
fi

echo "==> env file"
if [[ ! -f "$APP_DIR/.env" ]]; then
  cat > "$APP_DIR/.env" <<'ENV'
# Fill these in to use a real model. Without them the deterministic
# ScriptedPlanner handles the built-in demo tasks.
SAFECELL_LLM_BASE_URL=
SAFECELL_MODEL=
SAFECELL_API_KEY=
ENV
  chown "$SVC_USER:$SVC_USER" "$APP_DIR/.env"
  chmod 600 "$APP_DIR/.env"
fi

# Reconcile the bind on every run so a re-invocation with a different mode
# actually takes effect.
sed -i "s|^SAFECELL_HOST=.*||" "$APP_DIR/.env" 2>/dev/null || true
sed -i "s|^SAFECELL_PORT=.*||" "$APP_DIR/.env" 2>/dev/null || true
printf 'SAFECELL_HOST=%s\nSAFECELL_PORT=%s\n' "$WANT_HOST" "$WANT_PORT" >> "$APP_DIR/.env"
echo "    bind ${WANT_HOST}:${WANT_PORT}"

echo "==> systemd unit"
install -m 644 "$APP_DIR/deploy/safecell.service" /etc/systemd/system/safecell.service
systemctl daemon-reload
systemctl enable --now safecell
systemctl restart safecell
sleep 3

echo "==> firewall"
ufw allow OpenSSH >/dev/null 2>&1 || true
ufw allow 80/tcp  >/dev/null 2>&1 || true
ufw allow 443/tcp >/dev/null 2>&1 || true
ufw allow 8080/tcp >/dev/null 2>&1 || true
yes | ufw enable >/dev/null 2>&1 || true

echo "==> health check"
curl -fsS "http://127.0.0.1:${WANT_PORT}/api/health" | python3 -m json.tool || {
  echo "service did not come up; journalctl -u safecell -n 50" >&2
  exit 3
}

if [[ -n "$DOMAIN" ]]; then
  echo "==> Caddy (TLS for ${DOMAIN})"
  apt-get install -yqq debian-keyring debian-archive-keyring apt-transport-https || true
  if ! command -v caddy >/dev/null 2>&1; then
    curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' \
      | gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
    curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' \
      > /etc/apt/sources.list.d/caddy-stable.list
    apt-get update -qq
    apt-get install -yqq caddy
  fi
  cat > /etc/caddy/Caddyfile <<CADDY
${DOMAIN} {
	encode zstd gzip
	reverse_proxy 127.0.0.1:${WANT_PORT} {
		flush_interval -1
	}
}
CADDY
  systemctl enable --now caddy
  systemctl reload caddy || systemctl restart caddy
  sleep 6
  if curl -fsS -m 25 "https://${DOMAIN}/api/health" >/dev/null 2>&1; then
    echo "    TLS OK — https://${DOMAIN}/api/health"
  else
    echo "    !! TLS not answering yet. Check: journalctl -u caddy -n 30" >&2
    echo "    !! ACME needs public DNS for ${DOMAIN} and inbound 80+443." >&2
  fi
fi

echo
echo "SafeCell is listening on ${WANT_HOST}:${WANT_PORT}."
if [[ -n "$DOMAIN" ]]; then
  echo "Public URL: https://${DOMAIN}/"
elif [[ "$MODE" == "--public" ]]; then
  echo "Public URL: http://<this-host>:${WANT_PORT}/  (plain HTTP; pass a domain for TLS)"
else
  echo "Local only. Re-run with --public <domain> to expose it behind Caddy with TLS."
fi
