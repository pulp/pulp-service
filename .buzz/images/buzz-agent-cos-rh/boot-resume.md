Boot resume. Your pod just restarted (started at BOOT_TIME UTC). Every session from before the restart is gone, so anything it was in the middle of is now nobody's unless you pick it up. This is a one shot run with a 20 minute cap; it ends when you stop.

Run `cos-pending` (if $STAFF_CHANNEL is empty, skip it and read the threads you were mentioned in with buzz instead). It reads the last 48 hours of your #staff and #pulpbot-bench and prints one line per thread whose latest ask has no answer from you: every approver request, every "For the CoS:" handoff from gm-rh, and every proposal of yours with a later yes, as OPEN or PICKED. After a restart a PICKED line is nobody's, so treat it like OPEN. If it prints `CHECK FAILED`, fall back to reading both channels by hand with `buzz messages get` and sorting.

Read any thread a line points to in full with buzz messages thread. cos-pending finds the asks; also scan your own last posts for the one thing it does not catch:
- Work you posted as in progress or as your next step (for example "benchmarking now", "next I will") with no later message from you closing it.

For each one: check git log in your rh/agents clone (pull first) and the state of the thing itself, so you never do anything twice. If a message from you in that thread is newer than BOOT_TIME, a live session already has it: leave it alone. Otherwise finish the work as your charter says and reply in that same thread with what you did and verified. If the work turns out to be done already, reply in its thread with what you verified.

If there is nothing, post nothing at all. Never post that you restarted or that there was nothing to resume.
