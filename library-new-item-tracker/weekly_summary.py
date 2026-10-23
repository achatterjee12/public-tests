"""Weekly summary / heartbeat: one notification listing the games added in the last 7 days.

It is always sent, even when nothing was added, so a steady weekly message also shows
that the daily check is alive. It reads the games.json the daily run saves (no catalog
requests) and says when the last check ran; if that is more than a day and a half ago
the message becomes a high-priority warning.

usage: python weekly_summary.py games.json message.txt
Writes the message body to message.txt and, when run in GitHub Actions, title, priority
and tags to $GITHUB_OUTPUT for notify.sh.
"""
import json
import os
import sys
from datetime import datetime, timedelta, timezone

WINDOW = timedelta(days=7)
STALE_AFTER = timedelta(hours=36)   # the daily check runs every 24 hours
LIMIT = 25                          # titles listed; the page has the rest


def parse(ts):
    return datetime.fromisoformat(ts)


def ago(delta):
    hours = int(delta.total_seconds() // 3600)
    return f"{hours} hours ago" if hours < 48 else f"{hours // 24} days ago"


def where(item, names, max_branches=3):
    name = names.get(item["library"], item["library"])
    branches = sorted(item.get("branches") or [])
    if not branches:
        return name
    shown = branches[:max_branches]
    more = len(branches) - len(shown)
    return f"{name}: {', '.join(shown)}" + (f" +{more}" if more > 0 else "")


def build(data, now=None):
    """Returns {title, body, priority, tags} for the weekly message."""
    now = now or datetime.now(timezone.utc)
    names = {l["key"]: l["name"] for l in data.get("libraries", [])}
    fresh = sorted((i for i in data["items"]
                    if not i["baseline"] and now - parse(i["added"]) <= WINDOW),
                   key=lambda i: i["added"], reverse=True)
    checked = parse(data["generated_at"])
    stale = now - checked > STALE_AFTER

    if fresh:
        n = len(fresh)
        lines = [f"{n} new PS5 game{'s' if n != 1 else ''} in the last 7 days:"]
        lines += [f"- {i['title']} ({where(i, names)})" for i in fresh[:LIMIT]]
        if n > LIMIT:
            lines.append(f"...and {n - LIMIT} more (see the tracker page)")
        title = f"Weekly PS5 summary: {n} new game{'s' if n != 1 else ''}"
    else:
        lines = ["No new PS5 games in the last 7 days."]
        title = "Weekly PS5 summary: no new games"

    lines.append("")
    lines.append(f"Last daily check: {checked:%b %d, %H:%M} UTC. "
                 f"{data['total']} games tracked across {len(names)} libraries.")
    priority, tags = "default", "calendar"
    if stale:
        lines.insert(0, f"WARNING: the daily check last ran {ago(now - checked)}, "
                        "so this summary may be out of date.")
        lines.insert(1, "")
        title = "Weekly PS5 summary: daily check is behind"
        priority, tags = "high", "warning"
    return {"title": title, "body": "\n".join(lines), "priority": priority, "tags": tags}


def main(argv):
    games_path, out_path = argv[1], argv[2]
    with open(games_path) as f:
        msg = build(json.load(f))
    with open(out_path, "w") as f:
        f.write(msg["body"])
    print(f"{msg['title']}\n\n{msg['body']}")
    if os.environ.get("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a") as f:
            for key in ("title", "priority", "tags"):
                f.write(f"{key}={msg[key]}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
