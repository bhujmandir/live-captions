#!/usr/bin/env bash
# Live captions - the proper way to stop the server.
#
# Asks the server to shut down cleanly, so the network advert is withdrawn,
# any reprocessing job drains, and the log records a clean stop.
#
# WHY THIS EXISTS: a stray Ctrl+C in the server's console used to end the
# captions instantly, and twice did exactly that during a live event. Ctrl+C is
# now refused. See issue #61.
#
# The X-Captions-Control header is not decoration. Without it, any web page
# open in a browser ON THIS MACHINE could auto-submit a form to the same URL
# and stop the captions, because that browser is also 127.0.0.1. A form cannot
# set a custom header; this script can.
#
# Usage: ./stop-captions.sh [port]     (default 8765)

set -uo pipefail
PORT="${1:-8765}"

echo "Asking the captions server on port ${PORT} to stop..."

# -f so an HTTP error is an error here. Not `set -e`: the server sets its
# shutdown event a moment AFTER replying, so a dropped connection on a
# successful stop must be reported honestly rather than as a bare failure.
if curl -f -s -S -X POST \
     -H "X-Captions-Control: shutdown" \
     "http://127.0.0.1:${PORT}/api/shutdown" >/dev/null; then
  echo "Sent. The server is shutting down."
  exit 0
fi

echo "FAILED. The server did not accept the request on port ${PORT}." >&2
echo "It may already be stopped, or it may be on a different port." >&2
exit 1
