#!/usr/bin/env bash
# Server hardening of the HealthCore VPS (Ubuntu 22.04) — OWASP Top 10 audit, A05 Security
# Misconfiguration / A07 Identification and Authentication Failures at the host level.
#
# Idempotent and staged, so it can never lock the operator out:
#
#   sudo ./harden.sh user  <ssh-public-key-file>   1. operational user `deploy` (no root), key-only
#   (from your machine: ssh deploy@HOST 'sudo -v'  →  must work BEFORE the next stage)
#   sudo ./harden.sh ssh                           2. no root login, no passwords, only `deploy`
#   sudo ./harden.sh firewall                      3. ufw: deny everything incoming except SSH (rate limited)
#   sudo ./harden.sh fail2ban                      4. ban IPs that keep failing SSH authentication
#   sudo ./harden.sh perms                         5. explicit folder permissions: code / config / logs
#   sudo ./harden.sh all <ssh-public-key-file>     everything, in this order (only on a fresh server)
#
# The application (docker compose) publishes its ports on 127.0.0.1 only; it is reached through an
# SSH tunnel (ssh -L 3001:127.0.0.1:3001 deploy@HOST), so SSH is the only port the Internet needs.
# Verify with ./verify.sh (on the server) and the external checks in README.md.

set -euo pipefail

OPS_USER="${OPS_USER:-deploy}"
OPS_GROUP="${OPS_GROUP:-healthcore}"
APP_DIR="${APP_DIR:-/opt/healthcore}"
CONF_DIR="${CONF_DIR:-/etc/healthcore}"
LOG_DIR="${LOG_DIR:-/var/log/healthcore}"
SSH_PORT="${SSH_PORT:-22}"

log() { printf '[harden] %s\n' "$*"; }
need_root() { [[ $EUID -eq 0 ]] || { echo "run with sudo" >&2; exit 1; }; }

stage_user() {
  local key_file="${1:?usage: harden.sh user <ssh-public-key-file>}"
  grep -qE '^(ssh-ed25519|ecdsa-sha2-|ssh-rsa) ' "$key_file" || { echo "not an SSH public key: $key_file" >&2; exit 1; }
  getent group "$OPS_GROUP" >/dev/null || groupadd --system "$OPS_GROUP"
  if ! id "$OPS_USER" >/dev/null 2>&1; then
    useradd --create-home --shell /bin/bash --groups sudo,"$OPS_GROUP" "$OPS_USER"
    log "user $OPS_USER created (groups: sudo, $OPS_GROUP)"
  else
    usermod -aG sudo,"$OPS_GROUP" "$OPS_USER"
  fi
  # NOT in the docker group: membership of docker is root-equivalent; operations use `sudo docker`.
  install -d -m 700 -o "$OPS_USER" -g "$OPS_USER" "/home/$OPS_USER/.ssh"
  install -m 600 -o "$OPS_USER" -g "$OPS_USER" "$key_file" "/home/$OPS_USER/.ssh/authorized_keys"
  chmod 750 "/home/$OPS_USER"
  if [[ -n "${OPS_PASSWORD_FILE:-}" ]]; then  # password only for sudo (SSH stays key-only)
    printf '%s:%s\n' "$OPS_USER" "$(cat "$OPS_PASSWORD_FILE")" | chpasswd
    log "sudo password set for $OPS_USER"
  fi
  log "authorized key installed for $OPS_USER — test 'ssh $OPS_USER@HOST sudo -v' before stage ssh"
}

stage_ssh() {
  id "$OPS_USER" >/dev/null 2>&1 && [[ -s "/home/$OPS_USER/.ssh/authorized_keys" ]] \
    || { echo "stage user first: $OPS_USER has no authorized key (refusing to lock root out)" >&2; exit 1; }
  # 00- so it wins over 50-cloud-init.conf (sshd keeps the FIRST value it reads for a keyword)
  cat > /etc/ssh/sshd_config.d/00-healthcore-hardening.conf <<EOF
# HealthCore hardening (infra/hardening/harden.sh) — OWASP A05/A07 at the host level
Port $SSH_PORT
PermitRootLogin no
PasswordAuthentication no
KbdInteractiveAuthentication no
PermitEmptyPasswords no
PubkeyAuthentication yes
AuthenticationMethods publickey
AllowUsers $OPS_USER
MaxAuthTries 3
LoginGraceTime 30
MaxStartups 10:30:60
X11Forwarding no
AllowAgentForwarding no
# local forwarding stays: the application is reached through an SSH tunnel
AllowTcpForwarding local
ClientAliveInterval 300
ClientAliveCountMax 2
EOF
  chmod 644 /etc/ssh/sshd_config.d/00-healthcore-hardening.conf
  sshd -t  # never reload an invalid configuration
  systemctl reload ssh
  log "sshd reloaded: root login and passwords disabled, only $OPS_USER (existing sessions stay open)"
}

stage_firewall() {
  command -v ufw >/dev/null || apt-get install -y -q ufw
  ufw --force reset >/dev/null
  ufw default deny incoming
  ufw default allow outgoing
  ufw limit "$SSH_PORT"/tcp comment 'SSH (rate limited)'
  ufw logging low
  ufw --force enable
  # Docker publishes the app ports on 127.0.0.1 only (docker-compose.yml / compose.vps.yml): not
  # reachable from outside even though Docker's own chains bypass ufw for published ports.
  log "ufw enabled: deny incoming, allow $SSH_PORT/tcp (limit)"
  ufw status verbose
}

stage_fail2ban() {
  command -v fail2ban-client >/dev/null || apt-get install -y -q fail2ban
  cat > /etc/fail2ban/jail.d/healthcore-sshd.local <<EOF
[sshd]
enabled = true
port = $SSH_PORT
backend = systemd
maxretry = 5
findtime = 10m
bantime = 1h
EOF
  systemctl enable --now fail2ban >/dev/null
  systemctl restart fail2ban
  log "fail2ban sshd jail enabled"
}

stage_perms() {
  getent group "$OPS_GROUP" >/dev/null || groupadd --system "$OPS_GROUP"
  # code: owned by the operational user, readable by the group, nothing for others
  if [[ -d "$APP_DIR" ]]; then
    chown -R "$OPS_USER:$OPS_GROUP" "$APP_DIR"
    chmod 750 "$APP_DIR"
    find "$APP_DIR" -path "$APP_DIR/.git" -prune -o -type d -exec chmod 750 {} + 2>/dev/null || true
    find "$APP_DIR" -path "$APP_DIR/.git" -prune -o -type f ! -perm -u+x -exec chmod 640 {} + 2>/dev/null || true
    find "$APP_DIR" -path "$APP_DIR/.git" -prune -o -type f -perm -u+x -exec chmod 750 {} + 2>/dev/null || true
  fi
  # sensitive configuration (env files with secrets): root writes, the group reads, nobody else
  install -d -m 750 -o root -g "$OPS_GROUP" "$CONF_DIR"
  find "$CONF_DIR" -type f -exec chown root:"$OPS_GROUP" {} + -exec chmod 640 {} +
  find "$APP_DIR" -maxdepth 3 -name '.env' -type f -exec chmod 600 {} + 2>/dev/null || true
  # application logs: written by the operational user, read by the group (and adm for logrotate)
  install -d -m 750 -o "$OPS_USER" -g "$OPS_GROUP" "$LOG_DIR"
  log "permissions: $APP_DIR ($OPS_USER:$OPS_GROUP 750/640), $CONF_DIR (root:$OPS_GROUP 750/640), $LOG_DIR ($OPS_USER:$OPS_GROUP 750)"
}

need_root
case "${1:-}" in
  user) stage_user "${2:-}" ;;
  ssh) stage_ssh ;;
  firewall) stage_firewall ;;
  fail2ban) stage_fail2ban ;;
  perms) stage_perms ;;
  all) stage_user "${2:-}"; stage_perms; stage_fail2ban; stage_firewall; stage_ssh ;;
  *) sed -n '2,20p' "$0"; exit 2 ;;
esac
