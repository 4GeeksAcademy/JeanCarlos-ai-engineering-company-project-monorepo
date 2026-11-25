#!/usr/bin/env bash
# Checks of the hardening, run ON the server (sudo ./verify.sh). Prints PASS/FAIL per control and
# exits non-zero if any fails. External checks (root login refused, port scan) are in README.md.
set -uo pipefail

OPS_USER="${OPS_USER:-deploy}"
OPS_GROUP="${OPS_GROUP:-healthcore}"
APP_DIR="${APP_DIR:-/opt/healthcore}"
CONF_DIR="${CONF_DIR:-/etc/healthcore}"
LOG_DIR="${LOG_DIR:-/var/log/healthcore}"
failed=0

check() {  # check "description" command...
  local name="$1"; shift
  if "$@" >/dev/null 2>&1; then printf 'PASS  %s\n' "$name"; else printf 'FAIL  %s\n' "$name"; failed=1; fi
}
SSHD_EFFECTIVE="$(sshd -T 2>/dev/null)"  # read once: `sshd -T | grep -q` + pipefail fails on SIGPIPE
sshd_is() { grep -qx "$1" <<<"$SSHD_EFFECTIVE"; }

echo "== SSH"
check "PermitRootLogin no" sshd_is "permitrootlogin no"
check "PasswordAuthentication no" sshd_is "passwordauthentication no"
check "KbdInteractiveAuthentication no" sshd_is "kbdinteractiveauthentication no"
check "AllowUsers $OPS_USER" sshd_is "allowusers $OPS_USER"
check "MaxAuthTries <= 3" bash -c "awk '/^maxauthtries/ {exit !(\$2 <= 3)}' <<<\"\$0\"" "$SSHD_EFFECTIVE"

echo "== Operational user"
check "$OPS_USER exists and is not uid 0" bash -c "[[ \$(id -u $OPS_USER) -ne 0 ]]"
check "only root has uid 0" bash -c "[[ \$(awk -F: '\$3 == 0' /etc/passwd | wc -l) -eq 1 ]]"
check "$OPS_USER in sudo and $OPS_GROUP" bash -c "id -nG $OPS_USER | grep -qw sudo && id -nG $OPS_USER | grep -qw $OPS_GROUP"
check "$OPS_USER NOT in docker (root-equivalent)" bash -c "! id -nG $OPS_USER | grep -qw docker"
check "$OPS_USER has a key, mode 600" bash -c "[[ \$(stat -c %a /home/$OPS_USER/.ssh/authorized_keys) == 600 ]]"

echo "== Firewall"
check "ufw active" bash -c "ufw status | grep -q '^Status: active'"
check "default deny incoming" bash -c "ufw status verbose | grep -q 'Default: deny (incoming)'"
check "only SSH allowed" bash -c "[[ \$(ufw status | grep -cE 'ALLOW|LIMIT') -le 2 ]] && ufw status | grep -E 'ALLOW|LIMIT' | grep -qE '^22/tcp'"
check "only SSH listens on a public interface" bash -c "! ss -Htuln | awk '{print \$5}' | grep -vE '^(127\\.|\\[::1\\]|\\[?::ffff:127\\.)' | grep -vE ':22\$'"
check "fail2ban sshd jail" bash -c "fail2ban-client status sshd"

echo "== Folder permissions"
check "$APP_DIR is $OPS_USER:$OPS_GROUP 750" bash -c "[[ \$(stat -c '%U:%G %a' $APP_DIR) == '$OPS_USER:$OPS_GROUP 750' ]]"
check "no world-readable file in $APP_DIR (outside .git)" bash -c "[[ -z \$(find $APP_DIR -path $APP_DIR/.git -prune -o -perm -o+r -print -quit) ]]"
check "$CONF_DIR is root:$OPS_GROUP 750" bash -c "[[ \$(stat -c '%U:%G %a' $CONF_DIR) == 'root:$OPS_GROUP 750' ]]"
check "$LOG_DIR is $OPS_USER:$OPS_GROUP 750" bash -c "[[ \$(stat -c '%U:%G %a' $LOG_DIR) == '$OPS_USER:$OPS_GROUP 750' ]]"

exit $failed
