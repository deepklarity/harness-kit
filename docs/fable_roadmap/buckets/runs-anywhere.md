# Runs anywhere

**Why.** The kit lives on one MacBook that sleeps. Everything the shift
fought — frozen pipelines, zombie sandboxes, lost hours — traces back to
one deployment on one fragile host. A kit that runs as instances (a second
box, a server, side-by-side dev and stable) turns those incidents into
non-events, and a kit that installs anywhere is one that other people can
actually run. This is neither Ease (the first-timer's hour) nor Trust (not
lying) — it is the capability of existing in more than one place.

**Where it stands.** install.sh reaches a green doctor on this machine.
INSTANCE=dev port-offsets exist in dev.sh. A Linux box is available and
nothing has ever run on it. Multi-instance concerns (separate DBs, boards,
sandbox images per host) are undesigned.

**Ideas.**
- Second instance on the available Linux box: own ports, own DB, sandboxes
  working — the sleep-freeze class dies and deployment claims get proof.
- Instance identity: a kit instance knows its name, its host, its boards;
  doctor says which instance you are talking to.
- The stranger's path on a machine that isn't ours: install → doctor →
  quickstart on fresh Linux, timed.
- Work travels: a board's spec branches and config move between instances
  without surgery.

When an idea here becomes real work, it gets its own section below this
line with the design, the tasks, and the before and after numbers.
