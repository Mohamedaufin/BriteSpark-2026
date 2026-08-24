# Brite Spark 2026 — Day 2

## Problem 3 — No Wrong Door

### The requirements have changed

The Benefits Register has degraded permanently. It now fails on roughly **40% of calls**, and it is not going to be fixed.

Restart it with the new failure rate and leave it there:

```bash
python3 services/xml_service.py --port 8082 --failure-rate 0.40
```

**Your demo still has to work.**

### In this folder

Nothing. This change is a configuration change to a service you are already running — there is no new data.

Run your solution against the register at 40% before you present. If you have not seen it fail, you have not tested it.

---

### What has not changed

The floor in your problem document still applies in full, and it applies **after** this change, not before it. A requirement you were meeting yesterday that this change breaks is a requirement you are no longer meeting.

Everything else stands: the same deliverables, the same demo, the same rules on AI use.

### Two things worth doing

**Record how you handled it.** Add an entry to `DECISIONS.md` covering what you changed, what you chose not to change, and anything you would have done differently had you known this was coming. That entry is one of the more useful things in your repository at judging.

**Ask if something is genuinely unclear.** A question about what this change *means* is always fair and will never count against you. A question about how to implement it will get a polite non-answer.

Good luck.
