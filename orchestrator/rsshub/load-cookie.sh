#!/usr/bin/env bash
# load-cookie.sh — put the owner's x.com session into RSSHub's .env without any
# human or agent reading it.
#
# X shut off free API access, so RSSHub has to read it as a logged-in browser.
# That needs two cookie values, `auth_token` and `ct0`, which together grant full
# access to the account. This script moves them from Chrome to a local file and
# **never prints them** — not to the terminal, not to a log, not into an agent's
# transcript. It reports lengths and nothing else.
#
# Same mechanism `ingest.sh` already uses for Instagram: the vault's standing
# auth rule is the owner's own session, read at call time, never an agent account.
#
#   ./load-cookie.sh          # read Chrome, write .env, restart RSSHub
#   ./load-cookie.sh --check  # is the current .env working?
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="$HERE/.env"
# The port RSSHub is published on in docker-compose.yml. A default, not a
# constant: anyone whose 1200 is already taken sets RSSHUB_URL and this works.
RSSHUB_URL="${RSSHUB_URL:-http://localhost:1200}"
# A temp DIRECTORY, then a path inside it that does not exist yet. `mktemp` on a
# file creates it empty, and yt-dlp refuses to write a cookie jar over a file
# that lacks the Netscape header — so the obvious version silently produced
# nothing. [measured 2026-08-17]
TMPDIR_="$(mktemp -d -t xcookies.XXXXXX)"
TMP="$TMPDIR_/jar.txt"
trap 'rm -rf "$TMPDIR_"' EXIT

check() {
  local code
  code=$(curl -s -o /dev/null -w "%{http_code}" --max-time 45 \
    "$RSSHUB_URL/twitter/user/karpathy")
  if [ "$code" = "200" ]; then
    echo "✅ X route works (200)"
    return 0
  fi
  echo "✗ X route returned $code"
  [ "$code" = "503" ] && echo "  503 means the cookie is missing, expired, or rejected."
  return 1
}

if [ "${1:-}" = "--check" ]; then
  check
  exit $?
fi

command -v yt-dlp >/dev/null || { echo "yt-dlp not installed — brew install yt-dlp"; exit 1; }

echo "→ reading x.com cookies from Chrome (macOS may prompt for Keychain access)"
# yt-dlp writes the jar even though it cannot extract from x.com, so the URL
# error is expected and ignored. Do not add --quiet: it suppresses the write.
yt-dlp --cookies-from-browser chrome --cookies "$TMP" \
       --skip-download --simulate "https://x.com/home" >/dev/null 2>&1 || true

[ -s "$TMP" ] || { echo "✗ no cookies exported. Is Chrome installed and logged in to x.com?"; exit 1; }

# Netscape cookie format: domain, flag, path, secure, expiry, name, value
# EXACT domain match. `/x\.com$/` also matches `a-mx.com` and any other host
# ending in those characters, so a loose pattern can lift a token from an ad
# network instead of X. Anchor it to x.com or .x.com and nothing else.
AUTH=$(awk -F'\t' '($1 == "x.com" || $1 == ".x.com") && $6 == "auth_token" {print $7}' "$TMP" | tail -1)
CT0=$(awk -F'\t'  '($1 == "x.com" || $1 == ".x.com") && $6 == "ct0"        {print $7}' "$TMP" | tail -1)

if [ -z "$AUTH" ] || [ -z "$CT0" ]; then
  echo "✗ could not find both values for x.com."
  echo "  auth_token: ${#AUTH} chars · ct0: ${#CT0} chars"
  echo "  Open x.com in Chrome, make sure you are logged in, and re-run."
  exit 1
fi

umask 077
{
  # TWITTER_AUTH_TOKEN is the one this build actually reads; TWITTER_COOKIE is
  # written too so a future image that prefers it keeps working.
  printf 'TWITTER_AUTH_TOKEN="%s"\n' "$AUTH"
  printf 'TWITTER_COOKIE="auth_token=%s; ct0=%s"\n' "$AUTH" "$CT0"
} > "$ENV_FILE"
chmod 600 "$ENV_FILE"
echo "→ wrote $ENV_FILE  (auth_token ${#AUTH} chars, ct0 ${#CT0} chars, mode 600)"

echo "→ restarting RSSHub so it picks the cookie up"
(cd "$HERE" && docker compose up -d >/dev/null 2>&1)
sleep 12
check
