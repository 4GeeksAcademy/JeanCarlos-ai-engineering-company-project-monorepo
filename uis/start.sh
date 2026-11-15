#!/usr/bin/env bash
set -u

cd /app/talent-pipeline-tracker
./node_modules/.bin/next dev --hostname 0.0.0.0 --port 3000 &
tracker_pid=$!

cd /app/backoffice
./node_modules/.bin/next dev --hostname 0.0.0.0 --port 3001 &
backoffice_pid=$!

cleanup() {
  kill "$tracker_pid" "$backoffice_pid" 2>/dev/null || true
  wait "$tracker_pid" "$backoffice_pid" 2>/dev/null || true
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

status=0
wait -n "$tracker_pid" "$backoffice_pid" || status=$?
exit "$status"
