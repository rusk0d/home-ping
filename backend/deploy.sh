#!/usr/bin/env bash
# Deploy the backend to the server (#259): copy the files that differ from main, install the
# requirements when they changed, restart iot-backend and wait for /api/health.
#
#   backend/deploy.sh --check   compare only, change nothing (exit 1 when something differs)
#   backend/deploy.sh           show the differences, ask, then deploy
#   backend/deploy.sh --yes     the same without asking (e.g. from Claude Code's ! prompt)
#
# The server (user@host) comes from $DEPLOY_HOST or backend/.deploy-host (not committed).
# Copies what git has at HEAD (LF line endings), never the working copy, and never touches
# .env, the Firebase JSON, data.db or backups. The systemd units and fail2ban files are copied
# to /opt/iot too, but installing them stays manual: the script only says when the installed
# ones differ. Files only on the server are listed, not deleted.
set -euo pipefail

APP=/opt/iot
cd "$(git -C "$(dirname "$0")" rev-parse --show-toplevel)"

mode=ask
case "${1:-}" in
  --check) mode=check ;;
  --yes) mode=yes ;;
  "") ;;
  *) echo "usage: $0 [--check | --yes]" >&2; exit 2 ;;
esac

host="${DEPLOY_HOST:-}"
if [ -z "$host" ] && [ -f backend/.deploy-host ]; then
  host=$(tr -d ' \r\n' < backend/.deploy-host)
fi
if [ -z "$host" ]; then
  echo "No server: set DEPLOY_HOST=user@host or put it in backend/.deploy-host" >&2
  exit 2
fi

# Only a clean, pushed main is deployed; --check just warns
git fetch -q origin main
problems=""
[ "$(git rev-parse --abbrev-ref HEAD)" = main ] || problems+=" not on main;"
[ -z "$(git status --porcelain)" ] || problems+=" uncommitted changes;"
[ "$(git rev-parse HEAD)" = "$(git rev-parse origin/main)" ] || problems+=" HEAD is not origin/main (pull or push);"
if [ -n "$problems" ]; then
  if [ "$mode" = check ]; then
    echo "warning:$problems comparing with HEAD ($(git rev-parse --short HEAD))"
  else
    echo "Not deploying:$problems" >&2
    exit 1
  fi
fi

# What goes to the server: backend/ without the tests and dev-only files
mapfile -t files < <(git ls-files backend \
  | grep -vE '^backend/(tests/|\.env\.example$|requirements-dev\.txt$|ruff\.toml$|deploy\.sh$)' \
  | sed 's|^backend/||')

# Installed copies to compare (not written by this script)
installed_path() {
  case "$1" in
    systemd/*) echo "/etc/systemd/system/${1#systemd/}" ;;
    fail2ban/*.conf) echo "/etc/fail2ban/filter.d/${1#fail2ban/}" ;;
    fail2ban/*.local) echo "/etc/fail2ban/jail.d/${1#fail2ban/}" ;;
  esac
}

# Server side: a checksum per path (CRLF ignored: older deploys copied Windows line endings),
# then the .py files that only exist there
targets=()
for f in "${files[@]}"; do
  targets+=("$APP/$f")
  inst=$(installed_path "$f")
  if [ -n "$inst" ]; then targets+=("$inst"); fi
done
remote=$(ssh "$host" "bash -s -- $APP ${targets[*]}" <<'EOF'
app=$1; shift
for p in "$@"; do
  if [ -f "$p" ]; then echo "$(tr -d '\r' < "$p" | sha256sum | cut -c1-64) $p"; else echo "missing $p"; fi
done
cd "$app" && ls -1 ./*.py tools/*.py 2>/dev/null | sed 's|^\./||; s|^|server-file |'
EOF
)
remote_sum() { awk -v p="$1" '$2 == p { print $1 }' <<< "$remote"; }

changed=()
install_hints=()
for f in "${files[@]}"; do
  sum=$(git show "HEAD:backend/$f" | tr -d '\r' | sha256sum | cut -c1-64)
  [ "$(remote_sum "$APP/$f")" = "$sum" ] || changed+=("$f")
  inst=$(installed_path "$f")
  if [ -n "$inst" ] && [ "$(remote_sum "$inst")" != "$sum" ]; then
    install_hints+=("$f -> $inst")
  fi
done
mapfile -t extra < <(awk '$1 == "server-file" { print $2 }' <<< "$remote" \
  | while read -r f; do printf '%s\n' "${files[@]}" | grep -qxF "$f" || echo "$f"; done)

echo "Server $host vs main $(git rev-parse --short HEAD):"
if [ ${#changed[@]} -eq 0 ]; then
  echo "  $APP matches main"
else
  for f in "${changed[@]}"; do
    if [ "$(remote_sum "$APP/$f")" = "" ]; then echo "  new:     $f"; else echo "  differs: $f"; fi
  done
fi
for h in "${install_hints[@]}"; do echo "  installed copy differs (manual): $h"; done
for f in "${extra[@]}"; do echo "  only on the server (left alone): $f"; done

if [ "$mode" = check ]; then
  [ ${#changed[@]} -eq 0 ] && [ ${#install_hints[@]} -eq 0 ]
  exit
fi
if [ ${#changed[@]} -eq 0 ]; then
  [ ${#install_hints[@]} -eq 0 ] || echo "Nothing to copy; install the units/filters above by hand."
  exit 0
fi
if [ "$mode" = ask ]; then
  read -r -p "Deploy ${#changed[@]} file(s)? [y/N] " answer
  [ "$answer" = y ] || [ "$answer" = Y ] || { echo "Cancelled."; exit 1; }
fi

# Restart only for what the running backend loads (or its dependencies)
restart=no
pip=no
for f in "${changed[@]}"; do
  case "$f" in
    requirements.txt) pip=yes; restart=yes ;;
    backup_db.py|test_push.py|*/*) ;;
    *.py) restart=yes ;;
  esac
done

# Copy: git archive of HEAD with LF endings and 644/755 modes (its default umask 002 would make
# them group-writable; tar as root keeps the archive's modes), unpacked as root, owned by iot
paths=("${changed[@]/#/backend/}")
git -c core.autocrlf=false -c core.eol=lf -c tar.umask=022 archive --format=tar HEAD -- "${paths[@]}" \
  | ssh "$host" "tar -x --no-same-owner --strip-components=1 -C $APP -f - && cd $APP && chown iot:iot ${changed[*]}"
echo "Copied ${#changed[@]} file(s)."

if [ "$pip" = yes ]; then
  echo "requirements.txt changed: pip install..."
  ssh "$host" "cd $APP && sudo -u iot venv/bin/pip install -q -r requirements.txt"
fi

if [ "$restart" = yes ]; then
  echo "Restarting iot-backend..."
  ssh "$host" 'bash -s' <<'EOF'
since=$(date '+%Y-%m-%d %H:%M:%S')
systemctl restart iot-backend
for _ in $(seq 30); do
  sleep 1
  if curl -s 127.0.0.1:5000/api/health | grep -q '"ok": *true'; then
    echo "iot-backend is up, /api/health ok"
    exit 0
  fi
done
echo "iot-backend not healthy after 30 s; warnings since the restart:" >&2
journalctl -u iot-backend --since "$since" -p warning --no-pager | tail -20 >&2
exit 1
EOF
else
  echo "No backend module changed: no restart."
fi
for h in "${install_hints[@]}"; do echo "Install by hand (then systemctl daemon-reload / fail2ban reload): $h"; done
