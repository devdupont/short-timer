#!/usr/bin/env zsh
#
# Ops helper for the shortimer API on Azure Container Apps.
#
# Everything here is a thin wrapper around `az containerapp` with the resource
# group and app name filled in, because those are the parts nobody remembers.
# Run with no arguments for the list of subcommands.
#
# The deployment itself is not driven from here — GitHub Actions builds the
# image and rolls the revision (see .github/workflows/deploy-api.yml). This is
# for the configuration around it, which lives only in Azure.
#
# Override the targets if you ever run a second environment:
#   RG=other-rg APP=other-api ./scripts/azure.zsh status

set -euo pipefail

: ${RG:=shortimer-rg}
: ${APP:=shortimer-api}
#: Set AZ_YES=1 (or pass -y) to skip the confirmation on anything that writes.
: ${AZ_YES:=0}

readonly SCRIPT=${0:t}
readonly ENV_EXAMPLE=${0:A:h:h}/.env.example

# --- output ------------------------------------------------------------------

if [[ -t 1 ]]; then
  readonly C_DIM=$'\e[2m' C_RED=$'\e[31m' C_YELLOW=$'\e[33m' C_GREEN=$'\e[32m' C_OFF=$'\e[0m'
else
  readonly C_DIM='' C_RED='' C_YELLOW='' C_GREEN='' C_OFF=''
fi

die() { print -u2 -- "${C_RED}error:${C_OFF} $*"; exit 1 }
note() { print -u2 -- "${C_DIM}$*${C_OFF}" }
warn() { print -u2 -- "${C_YELLOW}note:${C_OFF} $*" }

# --- guards ------------------------------------------------------------------

# Fails before touching anything, so a missing login reads as a missing login
# rather than as a mysterious permissions error halfway through.
require_login() {
  command -v az >/dev/null 2>&1 || die "the Azure CLI isn't installed (brew install azure-cli)"
  az account show >/dev/null 2>&1 || die "not signed in — run: az login"
}

# Every write goes through here. Reads never do.
confirm() {
  [[ $AZ_YES == 1 ]] && return 0
  print -u2 -- "${C_YELLOW}About to change ${APP} in ${RG}:${C_OFF} $*"
  local reply
  # Read from the terminal rather than stdin, so this still prompts when the
  # script's output is being piped somewhere.
  read -r "reply?Continue? [y/N] " </dev/tty || return 1
  [[ $reply == [yY] ]] || { note "Nothing was changed."; return 1 }
}

app_query() { az containerapp show -n "$APP" -g "$RG" --query "$1" -o "${2:-tsv}" }

# The revision serving traffic. Single-revision mode is the default, so the
# latest ready one is the answer; the fallback covers multiple-revision mode.
active_revision() {
  local rev
  rev=$(app_query properties.latestReadyRevisionName 2>/dev/null || true)
  if [[ -z $rev || $rev == None ]]; then
    rev=$(az containerapp revision list -n "$APP" -g "$RG" \
      --query "[?properties.active] | [0].name" -o tsv 2>/dev/null || true)
  fi
  [[ -n $rev && $rev != None ]] || die "couldn't find an active revision for $APP"
  print -- "$rev"
}

# --- subcommands -------------------------------------------------------------

cmd_status() {
  require_login
  print -- "${C_DIM}app${C_OFF}       $APP  ${C_DIM}(rg: $RG)${C_OFF}"
  print -- "${C_DIM}fqdn${C_OFF}      $(app_query properties.configuration.ingress.fqdn)"
  print -- "${C_DIM}revision${C_OFF}  $(active_revision)"
  print -- "${C_DIM}image${C_OFF}     $(app_query 'properties.template.containers[0].image')"
  print -- ""
  az containerapp revision list -n "$APP" -g "$RG" \
    --query "[?properties.active].{revision:name, created:properties.createdTime, replicas:properties.replicas, state:properties.runningState}" \
    -o table
}

cmd_env() {
  require_login
  # Azure never returns a secret's value — a var backed by one shows its
  # secretRef instead, which is why this is safe to print.
  az containerapp show -n "$APP" -g "$RG" \
    --query "properties.template.containers[0].env[].{NAME:name, VALUE:value, SECRET:secretRef}" \
    -o table
}

cmd_get() {
  (( $# == 1 )) || die "usage: $SCRIPT get NAME"
  require_login
  local out
  out=$(az containerapp show -n "$APP" -g "$RG" \
    --query "properties.template.containers[0].env[?name=='$1'].[value, secretRef]" -o tsv)
  [[ -n $out ]] || { warn "$1 is not set — the app will use its built-in default"; return 1 }
  print -- "$out"
}

cmd_set() {
  (( $# >= 1 )) || die "usage: $SCRIPT set KEY=VALUE [KEY=VALUE ...]"
  local kv
  for kv in "$@"; do
    [[ $kv == *=* ]] || die "expected KEY=VALUE, got: $kv"
  done
  require_login
  # Only the names are echoed. A value could be a token pasted on the command
  # line, and this output tends to end up in a scrollback or a bug report.
  confirm "set ${(j:, :)${(@)${(@)argv%%=*}}}" || return 1
  # "$@" is passed through untouched so values containing spaces survive —
  # EMAIL_FROM is "shortimer <no-reply@send.shortimer.com>".
  az containerapp update -n "$APP" -g "$RG" --set-env-vars "$@" -o none
  print -- "${C_GREEN}done${C_OFF} — a new revision is rolling out"
}

cmd_unset() {
  (( $# >= 1 )) || die "usage: $SCRIPT unset NAME [NAME ...]"
  require_login
  confirm "remove ${(j:, :)argv}" || return 1
  az containerapp update -n "$APP" -g "$RG" --remove-env-vars "$@" -o none
  print -- "${C_GREEN}done${C_OFF} — a new revision is rolling out"
}

cmd_secrets() {
  require_login
  # Names only, deliberately. `az containerapp secret show` will print a value
  # if you ever genuinely need one, but it shouldn't be the easy path.
  az containerapp secret list -n "$APP" -g "$RG" --query "[].name" -o tsv
}

cmd_secret_set() {
  (( $# >= 1 )) || die "usage: $SCRIPT secret-set NAME=VALUE [NAME=VALUE ...]"
  local kv
  for kv in "$@"; do
    [[ $kv == *=* ]] || die "expected NAME=VALUE, got: $kv"
  done
  require_login
  confirm "set secret ${(j:, :)${(@)${(@)argv%%=*}}}" || return 1
  az containerapp secret set -n "$APP" -g "$RG" --secrets "$@" -o none
  print -- "${C_GREEN}done${C_OFF}"
  # The gotcha this script exists to stop you rediscovering: changing a
  # secret's *value* does not roll a revision, so replicas keep serving the
  # old one until they're restarted.
  warn "changing a secret value does not restart replicas — run: $SCRIPT restart"
}

cmd_restart() {
  require_login
  local rev
  rev=$(active_revision)
  confirm "restart revision $rev" || return 1
  az containerapp revision restart -n "$APP" -g "$RG" --revision "$rev" -o none
  print -- "${C_GREEN}restarted${C_OFF} $rev"
}

cmd_logs() {
  require_login
  az containerapp logs show -n "$APP" -g "$RG" --tail "${1:-50}" --follow
}

# Compares what's set in Azure against the names in .env.example. It reports
# rather than judges: most settings have a sensible default and are meant to be
# absent. The row worth looking at is "unknown", which is how a typo'd name
# shows up — Settings ignores anything it doesn't recognise, so a misspelled
# var is silently no var at all.
cmd_check() {
  require_login
  [[ -r $ENV_EXAMPLE ]] || die "can't read $ENV_EXAMPLE"

  local -a known live
  known=(${(f)"$(grep -oE '^[A-Z][A-Z0-9_]*=' $ENV_EXAMPLE | tr -d '=')"})
  live=(${(f)"$(az containerapp show -n "$APP" -g "$RG" \
    --query 'properties.template.containers[0].env[].name' -o tsv)"})

  print -- "${C_DIM}set in Azure:${C_OFF}"
  local name
  for name in ${(o)live}; do
    if (( ${known[(Ie)$name]} )); then
      print -- "  ${C_GREEN}✓${C_OFF} $name"
    else
      print -- "  ${C_YELLOW}?${C_OFF} $name  ${C_DIM}(not in .env.example — typo, or newer than the file)${C_OFF}"
    fi
  done

  print -- ""
  print -- "${C_DIM}using built-in defaults (absent from Azure):${C_OFF}"
  for name in ${(o)known}; do
    (( ${live[(Ie)$name]} )) || print -- "  ${C_DIM}·${C_OFF} $name"
  done
}

usage() {
  print -- "Ops helper for $APP in $RG."
  print -- ""
  print -- "usage: $SCRIPT <command> [args]"
  print -- ""
  print -- "  status              active revision, image, and ingress hostname"
  print -- "  env                 list environment variables"
  print -- "  get NAME            show one variable"
  print -- "  set KEY=VALUE ...   add or update variables (rolls a new revision)"
  print -- "  unset NAME ...      remove variables (rolls a new revision)"
  print -- "  check               compare what's set against .env.example"
  print -- "  secrets             list secret names (never values)"
  print -- "  secret-set N=V ...  create or update a secret"
  print -- "  restart             restart the active revision"
  print -- "  logs [lines]        tail the container log (default 50)"
  print -- ""
  print -- "Reads never prompt. Anything that writes asks first; -y or AZ_YES=1 skips that."
  print -- "Targets come from \$RG and \$APP, currently $RG / $APP."
}

# --- dispatch ----------------------------------------------------------------

main() {
  (( $# )) || { usage; return 0 }

  local -a rest
  local arg
  for arg in "$@"; do
    case $arg in
      -y|--yes) AZ_YES=1 ;;
      -h|--help) usage; return 0 ;;
      *) rest+=("$arg") ;;
    esac
  done
  (( ${#rest} )) || { usage; return 0 }

  local sub=${rest[1]}
  shift rest

  case $sub in
    status)              cmd_status "${rest[@]}" ;;
    env|list)            cmd_env "${rest[@]}" ;;
    get)                 cmd_get "${rest[@]}" ;;
    set)                 cmd_set "${rest[@]}" ;;
    unset|rm)            cmd_unset "${rest[@]}" ;;
    check)               cmd_check "${rest[@]}" ;;
    secrets)             cmd_secrets "${rest[@]}" ;;
    secret-set)          cmd_secret_set "${rest[@]}" ;;
    restart|reboot)      cmd_restart "${rest[@]}" ;;
    logs)                cmd_logs "${rest[@]}" ;;
    *)                   die "unknown command: $sub  (run $SCRIPT --help)" ;;
  esac
}

main "$@"