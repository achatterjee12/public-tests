#!/usr/bin/env bash
# Send a push notification through ntfy.
#   notify.sh new <message-file>   new games found (does nothing if the file is missing)
#   notify.sh failure <run-url>    the daily run failed
#   notify.sh test                 sample message, to check the phone end (manual runs)
# Needs NTFY_TOPIC (the workflow passes in the LIBRARY_NEW_ITEM_TRACKER_NTFY_TOPIC secret: on the public server the topic name is
# the only thing keeping others out). Skips quietly if it is not set.
set -euo pipefail

SERVER="${NTFY_SERVER:-https://ntfy.sh}"
PAGE_URL="${PAGE_URL:-https://achatterjee12.github.io/public-tests/library-new-item-tracker/}"

if [ -z "${NTFY_TOPIC:-}" ]; then
  if [ "${1:-}" = "test" ]; then
    echo "::error::The LIBRARY_NEW_ITEM_TRACKER_NTFY_TOPIC secret is not set (or is misspelled)"
    exit 1
  fi
  echo "::notice::NTFY_TOPIC is not set; skipping notification"
  exit 0
fi

case "${1:-}" in
  new)
    file="${2:?message file required}"
    [ -s "$file" ] || { echo "No new games; nothing to send"; exit 0; }
    count=$(grep -c '^- ' "$file" || true)
    curl -fsS --retry 3 \
      -H "Title: New PS5 games at your libraries" \
      -H "Tags: video_game" \
      -H "Click: $PAGE_URL" \
      --data-binary @"$file" "$SERVER/$NTFY_TOPIC" > /dev/null
    echo "Notification sent ($count listed)"
    ;;
  failure)
    curl -fsS --retry 3 \
      -H "Title: PS5 tracker failed" \
      -H "Tags: warning" \
      -H "Priority: high" \
      -H "Click: ${2:-$PAGE_URL}" \
      -d "The daily check hit an error. Tap to see which library failed." \
      "$SERVER/$NTFY_TOPIC" > /dev/null
    ;;
  test)
    curl -fsS --retry 3 \
      -H "Title: Test: PS5 tracker notifications" \
      -H "Tags: white_check_mark" \
      -H "Click: $PAGE_URL" \
      -d "If you can read this, notifications are working. Tap to open the tracker page." \
      "$SERVER/$NTFY_TOPIC" > /dev/null
    echo "Test notification sent"
    ;;
  *)
    echo "usage: notify.sh new <file> | failure <run-url> | test" >&2
    exit 2
    ;;
esac
