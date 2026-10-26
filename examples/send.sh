#!/bin/sh
# Post one message to a Claude Code session's inbox socket with nc or socat.
#
#   send.sh [-s SOCKET] [-p now|next|later] [-n] TEXT...
#
# SOCKET defaults to $CLAUDE_CODE_MESSAGING_SOCKET, the session this shell runs
# under. When $CLAUDE_CODE_MESSAGING_TOKEN is set the auth line goes first, so a
# hook or Bash command posting to its own session is verified as its own child
# even where process evidence is missing; -n leaves it out.
#
# Exit status: 0 when the frame was written; the receiver answers nothing on
# this connection, so success means "taken off the socket", not "read".
set -eu

sock=${CLAUDE_CODE_MESSAGING_SOCKET:-}
priority=next
auth=1
while [ $# -gt 0 ]; do
  case $1 in
    -s) sock=$2; shift 2 ;;
    -p) priority=$2; shift 2 ;;
    -n) auth=0; shift ;;
    -h|--help) sed -n '2,13p' "$0"; exit 0 ;;
    --) shift; break ;;
    -*) echo "send.sh: unknown option $1" >&2; exit 2 ;;
    *) break ;;
  esac
done
[ $# -gt 0 ] || { echo "send.sh: no text given" >&2; exit 2; }
[ -n "$sock" ] || { echo "send.sh: no socket: pass -s or run under a Claude Code session" >&2; exit 2; }
[ -S "$sock" ] || { echo "send.sh: $sock is not a socket (session gone?)" >&2; exit 1; }
case $priority in now|next|later) ;; *) echo "send.sh: priority must be now, next or later" >&2; exit 2 ;; esac

# JSON-escape the text: backslash, quote, tab and newline. Anything else that is
# not printable ASCII is sent as is, which JSON allows for UTF-8.
text=$(printf '%s' "$*" | awk 'BEGIN{ORS=""} {gsub(/\\/,"\\\\"); gsub(/"/,"\\\""); gsub(/\t/,"\\t"); if (NR>1) print "\\n"; print}')

frames=""
if [ "$auth" = 1 ] && [ -n "${CLAUDE_CODE_MESSAGING_TOKEN:-}" ]; then
  frames=$(printf '{"type":"auth","token":"%s"}\n' "$CLAUDE_CODE_MESSAGING_TOKEN")
  frames="$frames
"
fi
frames="$frames$(printf '{"type":"user","message":{"role":"user","content":"%s"},"priority":"%s"}' "$text" "$priority")"

# Build the whole payload first, then connect: the receiver closes a connection
# that has not sent a complete line within 30 seconds.
if command -v nc >/dev/null 2>&1 && nc -h 2>&1 | grep -q -- '-U'; then
  printf '%s\n' "$frames" | nc -N -U "$sock"
elif command -v socat >/dev/null 2>&1; then
  printf '%s\n' "$frames" | socat - "UNIX-CONNECT:$sock"
else
  echo "send.sh: need OpenBSD nc (with -U) or socat" >&2; exit 1
fi
