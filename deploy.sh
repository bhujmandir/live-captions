#!/bin/bash
# ──────────────────────────────────────────────────────────────────────────────
# Live captions deploy script.
#
# Sets up the live caption server on a fresh Mac and (optionally) starts it.
# Idempotent: safe to re-run; only installs what's missing.
#
# ── Usage ────────────────────────────────────────────────────────────────────
#   1. Copy this whole `live-captions/` directory to the target Mac. For example:
#
#        # From your laptop, push to the target Mac:
#        rsync -avz --exclude='.venv' --exclude='results' --exclude='samples' \
#          live-captions/ user@target-mac:~/live-captions/
#
#      (Drop `--exclude='samples'` if you want the test audio for offline
#      sanity checks. Drop `.env` exclusion intentionally — see below.)
#
#   2. SSH into the target Mac (or sit at it):
#
#        ssh user@target-mac
#        cd ~/live-captions
#
#   3. Run:
#
#        bash deploy.sh                  # install + start the server
#        bash deploy.sh --no-start       # install only
#        bash deploy.sh --start-only     # start without re-checking prereqs
#
#   4. Visit  http://localhost:8765/  in a browser on that Mac.
#
# ── What it installs ─────────────────────────────────────────────────────────
#   * Homebrew                       (if missing)
#   * portaudio                      (required by Python's sounddevice)
#   * uv                             (Python package manager)
#   * Python dependencies            (via `uv sync` from pyproject.toml)
#   * .env scaffold + SARVAM_API_KEY (prompted if not present)
#
# ── What you still must do manually ──────────────────────────────────────────
#   * Microphone permission for whichever terminal app you're running this in
#     (System Settings → Privacy & Security → Microphone). macOS will not
#     prompt — the captions just won't capture audio until you grant it.
#   * Quit and reopen the terminal after granting the permission. macOS only
#     applies it to processes started AFTER the grant.
# ──────────────────────────────────────────────────────────────────────────────

set -euo pipefail

# ── Argument parsing ─────────────────────────────────────────────────────────
START=true
SKIP_INSTALL=false
PORT="${PORT:-8765}"
for arg in "$@"; do
  case "$arg" in
    --no-start)    START=false ;;
    --start-only)  SKIP_INSTALL=true; START=true ;;
    --port=*)      PORT="${arg#*=}" ;;
    -h|--help)
      sed -n '3,40p' "$0" | sed 's/^# \{0,1\}//'
      exit 0 ;;
    *)
      echo "Unknown option: $arg (try --help)" >&2
      exit 1 ;;
  esac
done

# ── Output helpers ───────────────────────────────────────────────────────────
RED=$'\033[1;31m'; GREEN=$'\033[1;32m'; YELLOW=$'\033[1;33m'; BLUE=$'\033[1;34m'; BOLD=$'\033[1m'; NC=$'\033[0m'
info() { echo "${BLUE}→${NC} $*"; }
ok()   { echo "${GREEN}✓${NC} $*"; }
warn() { echo "${YELLOW}⚠${NC} $*"; }
err()  { echo "${RED}✗${NC} $*" >&2; }
hdr()  { echo; echo "${BOLD}── $* ─────────────────────────────────────${NC}"; }

CAPTIONS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$CAPTIONS_DIR"

# ── Sanity checks ────────────────────────────────────────────────────────────
if [[ "$(uname)" != "Darwin" ]]; then
  err "This script is for macOS. Detected $(uname)."
  exit 1
fi
if [[ "$(id -u)" == "0" ]]; then
  err "Don't run this with sudo. Run as your normal user — Homebrew refuses to install as root."
  exit 1
fi

echo
echo "${BOLD}Live captions${NC}"
echo "Working directory: $CAPTIONS_DIR"
echo "Server port:        $PORT"

# Make sure Homebrew / uv on PATH for the rest of this session even if the
# user hasn't sourced their shell rc yet (fresh terminal after install).
[[ -d /opt/homebrew/bin ]] && export PATH="/opt/homebrew/bin:$PATH"
[[ -d /usr/local/bin    ]] && export PATH="/usr/local/bin:$PATH"
[[ -d "$HOME/.local/bin" ]] && export PATH="$HOME/.local/bin:$PATH"

# ──────────────────────────────────────────────────────────────────────────────
# Install phase
# ──────────────────────────────────────────────────────────────────────────────
if [[ "$SKIP_INSTALL" == "false" ]]; then

  hdr "1. Homebrew"
  if ! command -v brew >/dev/null 2>&1; then
    info "Homebrew not found — installing (will prompt for sudo password)…"
    /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
    # Re-add to PATH post-install
    [[ -d /opt/homebrew/bin ]] && export PATH="/opt/homebrew/bin:$PATH"
    [[ -d /usr/local/bin    ]] && export PATH="/usr/local/bin:$PATH"
    ok "Homebrew installed."
  else
    ok "Homebrew already installed ($(brew --version | head -1))."
  fi

  hdr "2. portaudio"
  if brew list --formula portaudio >/dev/null 2>&1; then
    ok "portaudio already installed."
  else
    info "Installing portaudio (required by Python sounddevice)…"
    brew install portaudio
    ok "portaudio installed."
  fi

  hdr "3. uv"
  if ! command -v uv >/dev/null 2>&1; then
    info "Installing uv (Python package manager)…"
    curl -LsSf https://astral.sh/uv/install.sh | sh
    [[ -d "$HOME/.local/bin" ]] && export PATH="$HOME/.local/bin:$PATH"
    if ! command -v uv >/dev/null 2>&1; then
      err "uv install completed but 'uv' is not on PATH. Open a new terminal and re-run, or:  export PATH=\"\$HOME/.local/bin:\$PATH\""
      exit 1
    fi
    ok "uv installed ($(uv --version))."
  else
    ok "uv already installed ($(uv --version))."
  fi

  hdr "4. Python dependencies"
  info "Running uv sync…"
  uv sync
  ok "Python environment ready in .venv/"

  hdr "5. .env (Sarvam API key)"
  if [[ ! -f .env ]]; then
    if [[ -f .env.template ]]; then
      cp .env.template .env
      info ".env created from .env.template"
    else
      cat > .env <<'EOF'
# Sarvam AI streaming STT/translation — get a key at https://dashboard.sarvam.ai/
SARVAM_API_KEY=

# Optional branding (browser title, accent colour for buttons/highlights)
# APP_NAME=Live Captions
# ACCENT_COLOR=#FF8C00

# Optional default language direction (Sarvam codes; e.g. gu-IN, hi-IN)
# DEFAULT_SOURCE_LANG=en-IN
# DEFAULT_TARGET_LANG=en-IN

# ProPresenter (only used when --propresenter is passed)
# PP_HOST=127.0.0.1
# PP_PORT=49566
EOF
      info ".env created (empty template)"
    fi
  fi

  if ! grep -Eq '^SARVAM_API_KEY=..+' .env; then
    warn "SARVAM_API_KEY is not set in .env"
    read -r -p "    Paste your Sarvam API key now (or press Enter to fill in manually later): " api_key
    if [[ -n "$api_key" ]]; then
      tmpf="$(mktemp)"
      grep -v '^SARVAM_API_KEY=' .env > "$tmpf" || true
      printf 'SARVAM_API_KEY=%s\n' "$api_key" >> "$tmpf"
      mv "$tmpf" .env
      ok "SARVAM_API_KEY written to .env"
    else
      warn "Skipped. Edit .env later and re-run.  Captions won't work without a key."
    fi
  else
    ok "SARVAM_API_KEY present."
  fi

  hdr "6. Microphone permission (manual)"
  echo "    macOS requires the terminal app you're running this from to have"
  echo "    microphone access. To grant it:"
  echo
  echo "      ${BOLD}System Settings → Privacy & Security → Microphone${NC}"
  echo "        → enable the toggle next to Terminal (or iTerm, etc.)"
  echo "        → ${BOLD}Quit and reopen the terminal${NC} after granting."
  echo
  echo "    The captions UI will show MIC level at 0% / [SILENT] if permission"
  echo "    is missing — that's the symptom to watch for."

fi  # install phase

# ──────────────────────────────────────────────────────────────────────────────
# Run phase
# ──────────────────────────────────────────────────────────────────────────────
if [[ "$START" == "false" ]]; then
  echo
  ok "Install phase complete. Skipping server start (--no-start)."
  echo "    Start later with:  cd $CAPTIONS_DIR && bash deploy.sh --start-only"
  exit 0
fi

hdr "7. Port check"
if lsof -nP -iTCP:"$PORT" -sTCP:LISTEN >/dev/null 2>&1; then
  err "Port $PORT is already in use. Either stop the other process, or:"
  echo "        bash deploy.sh --port=8766"
  echo
  lsof -nP -iTCP:"$PORT" -sTCP:LISTEN || true
  exit 1
fi
ok "Port $PORT is free."

hdr "8. Starting captions server"
echo
echo "    Operator URL:    ${BOLD}http://localhost:$PORT/${NC}"
echo "    Overlay URL:     ${BOLD}http://localhost:$PORT/?overlay=1${NC}"
echo "    Debug panel:     append  ?debug=1  to the operator URL"
echo
echo "    Stop with Ctrl+C. Re-start with:"
echo "        cd $CAPTIONS_DIR && bash deploy.sh --start-only"
echo

# Exec replaces the shell so Ctrl+C goes straight to Python, no orphan shell.
exec uv run python live_captions.py --port "$PORT"
