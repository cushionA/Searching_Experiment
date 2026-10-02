#!/bin/sh
set -eu
if [ -n "${BOT_DIAGNOSTICS_CA:-}" ]; then
  # The supplied public CA is trusted only in this container's ephemeral NSS store.
  diagnostics_user_dir="$(getent passwd "$(id -u)" | cut -d: -f6)"
  diagnostics_nss_dir="$diagnostics_user_dir/.pki/nssdb"
  mkdir -p "$diagnostics_nss_dir"
  if [ ! -f "$diagnostics_nss_dir/cert9.db" ]; then
    certutil -N --empty-password -d "sql:$diagnostics_nss_dir"
  fi
  certutil -A -n bot-diagnostics-provided-ca -t 'C,,' -d "sql:$diagnostics_nss_dir" -i "$BOT_DIAGNOSTICS_CA"
fi
case "${1:-}" in
  smoke) shift; exec node /app/experiments/bot-diagnostics/smoke.mjs "$@" ;;
  options-smoke) shift; exec node /app/experiments/bot-diagnostics/options-smoke.mjs "$@" ;;
  detectors) shift; exec node /app/experiments/bot-diagnostics/runner.mjs detectors "$@" ;;
  export) shift; exec python3 -B /app/experiments/bot-diagnostics/export.py "$@" ;;
  *) exec node /app/experiments/bot-diagnostics/framework.mjs "$@" ;;
esac
