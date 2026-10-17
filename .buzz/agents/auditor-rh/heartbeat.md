Heartbeat. Run exactly this and decide:

    now=$(TZ=America/New_York date +%H%M); since=$(TZ=America/New_York date -d "$(TZ=America/New_York date +%F) 00:00" +%s); echo "time=$now"
    if msgs=$(buzz messages get --channel "$AUDIT_CHANNEL" --since $since --limit 200); then count=$(printf '%s' "$msgs" | jq -r --arg me "$AGENT_PUBKEY" '.[] | select(.pubkey==$me) | .content' | grep -c '^Audit '); echo "count=$count"; else echo "fetch failed: do nothing this heartbeat"; fi

The `if` separates a real fetch failure from a clean day: `grep -c` prints 0 and the count reads 0 on a day with no audit yet, which is the normal morning state, not a failure. Only a non-zero exit from `buzz messages get` prints "fetch failed".

If time is 0800 or later and the count is 0, do the daily audit as your charter says. Otherwise post nothing.
