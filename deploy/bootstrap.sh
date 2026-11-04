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
PUBLIC=0
[[ "${2:-}" == "--public" ]] && PUBLIC=1
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
  cat > "$APP_DIR/.env" <<ENV
# Fill these in to use a real model. Without them the deterministic
# ScriptedPlanner handles the built-in demo tasks.
SAFECELL_LLM_BASE_URL=
SAFECELL_MODEL=
SAFECELL_API_KEY=
# Set by bootstrap: --public listens on all interfaces :80, otherwise localhost:8000
SAFECELL_HOST=$([[ $PUBLIC -eq 1 ]] && echo 0.0.0.0 || echo 127.0.0.1)
SAFECELL_PORT=$([[ $PUBLIC -eq 1 ]] && echo 80 || echo 8000)
ENV
  chown "$SVC_USER:$SVC_USER" "$APP_DIR/.env"
  chmod 600 "$APP_DIR/.env"
fi

# Reconcile the bind on every run, not just on first install: a re-run with
# --public must actually move the listener off localhost, otherwise the
# security group looks open while nothing is answering on :80.
WANT_HOST=$([[ $PUBLIC -eq 1 ]] && echo 0.0.0.0 || echo 127.0.0.1)
WANT_PORT=$([[ $PUBLIC -eq 1 ]] && echo 80 || echo 8000)
sed -i "s|^SAFECELL_HOST=.*|SAFECELL_HOST=${WANT_HOST}|" "$APP_DIR/.env"
sed -i "s|^SAFECELL_PORT=.*|SAFECELL_PORT=${WANT_PORT}|" "$APP_DIR/.env"
grep -q '^SAFECELL_HOST=' "$APP_DIR/.env" || echo "SAFECELL_HOST=${WANT_HOST}" >> "$APP_DIR/.env"
grep -q '^SAFECELL_PORT=' "$APP_DIR/.env" || echo "SAFECELL_PORT=${WANT_PORT}" >> "$APP_DIR/.env"
echo "    bind ${WANT_HOST}:${WANT_PORT}"

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
BIND=$(grep '^SAFECELL_HOST=' "$APP_DIR/.env" | cut -d= -f2)
PORT=$(grep '^SAFECELL_PORT=' "$APP_DIR/.env" | cut -d= -f2)
[[ "$BIND" == "0.0.0.0" ]] && BIND=127.0.0.1
curl -fsS "http://${BIND}:${PORT}/api/health" | python3 -m json.tool || {
  echo "service did not come up; journalctl -u safecell -n 50" >&2
  exit 3
}

echo
echo "SafeCell is listening on ${BIND}:${PORT}."
if [[ "$PUBLIC" -eq 1 ]]; then
  echo "Public mode: reachable at http://<this-host>/ . TLS still recommended for a demo URL."
else
  echo "Expose it: install Caddy (https://caddyserver.com/docs/install) and use deploy/Caddyfile,"
  echo "or re-run with --public to listen on 0.0.0.0:80 behind a security group."
fi
