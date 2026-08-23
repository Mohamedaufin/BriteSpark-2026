# No Wrong Door — Unified Resident View

Brite Spark 2026, Problem 3. A single API that assembles one view of a
resident from two systems that have never spoken to each other: a paginated
REST resident index, and a slow, unreliable legacy XML benefits register.
See [`DECISIONS.md`](DECISIONS.md) for what was built and why.

## Requirements

Python 3.9+, standard library only. Nothing to `pip install` — for the mock
services or for this solution.

> **Windows note:** if `python3` isn't on your PATH, use `python` instead —
> everything here is plain stdlib and runs identically either way.

## Running it

**1. Start the two mock services** (from the repo root):

```bash
bash "data pack/services/run_both.sh"
```

If that fails with `python3: command not found` (some Windows PATHs only
have `python`, not the `python3` alias `run_both.sh` calls directly), start
them individually with whichever alias exists instead:

```bash
python3 "data pack/services/rest_service.py" --port 8081 &
python3 "data pack/services/xml_service.py"  --port 8082 &
# or, if python3 isn't found:
python  "data pack/services/rest_service.py" --port 8081 &
python  "data pack/services/xml_service.py"  --port 8082 &
```

**2. Start the unified API** (from the repo root, in another terminal):

```bash
python3 -m app.api --port 8090
```

**3. Query it:**

> **Windows PowerShell note:** `curl` there is an alias for
> `Invoke-WebRequest`, which prompts `Security Warning: Script Execution
> Risk [Y/N]` before showing any response - this is generic PowerShell
> behaviour for any web content, unrelated to this API. Either answer `Y`,
> or avoid the prompt entirely with `curl.exe` (the real curl binary,
> already on modern Windows) instead of bare `curl`.

```bash
curl http://127.0.0.1:8090/health

# Either identifier opens the same view - that is the point of the problem.
curl http://127.0.0.1:8090/unified/residents/R-10697
curl http://127.0.0.1:8090/unified/residents/NO/2019/4697

# One of the 200 people who exist only in the Benefits Register.
curl http://127.0.0.1:8090/unified/residents/AS/2024/4702

# Matching is on by default; opt out with ?match=off.
curl "http://127.0.0.1:8090/unified/residents/R-10697?match=off"

curl http://127.0.0.1:8090/unified/residents
```

**Or skip the HTTP layer entirely** with the CLI demo:

```bash
python3 -m app.cli --demo
python3 -m app.cli R-10697
python3 -m app.cli NO/2019/4697
python3 -m app.cli R-10697 --no-match
```

**Or open `http://localhost:8090/` in a browser** for a small optional
display page — search either identifier, see the same JSON rendered, with a
"Backend API →" button that opens the exact raw endpoint it just called.
This exists purely to make manual poking faster; it's a pure client of the
three JSON endpoints below (fetch, then render — nothing is recomputed in
the browser), and the problem statement is explicit that interface quality
isn't assessed on this problem, so it isn't a scored part of the solution.

## API

| Endpoint | What it does |
|---|---|
| `GET /` | Optional display page (see above) - not part of the graded solution |
| `GET /health` | This service's health, plus up/down for each upstream source |
| `GET /unified/residents/<identifier>` | Unified view of one resident, by **either** identifier |
| `GET /unified/residents/<identifier>?match=off` | Same, without attempting the cross-source join |
| `GET /unified/residents` | Unified view of every resident (de-duplicated) |
| `GET /unified/residents?match=off` | Same, without matching |

`<identifier>` is either a Resident Index id (`R-10697`) or a Benefits
Register ref (`NO/2019/4697`). Whichever one staff are holding opens the
same view — that is the problem's own framing, and it means the 200 people
who exist only in the register are reachable rather than invisible.
`found_by` in the response says which door was used.

Every response carries a `resident_source` / `benefits_source` status block
explaining exactly what happened and why — see the degradation table in
`DECISIONS.md`. The API returns HTTP 200 for a degraded-but-partial result;
it never turns an upstream hiccup into a 5xx. When an identifier isn't found
*and* a source was unreachable, the response says so explicitly rather than
letting you read it as "no such person".

Matching is on by default, because the problem asks for one call returning
everything known about a resident. There's no shared key between the
sources, so it joins on a deterministic normalized name+DOB match, states a
`confidence` on every match, and declines with a reason whenever the key is
ambiguous on either side — being wrong quietly is worse than not merging.
`?match=off` disables it entirely. See `DECISIONS.md` for the measured
accuracy and the full reasoning.

Both sources are cached for 20 seconds (`XML_CACHE_TTL` / `REST_CACHE_TTL`),
so the register's ~1.5s delay isn't paid on every call — measured, a cold
call takes 3.55s and a cached one under a millisecond. **The staleness
accepted in exchange is up to 20 seconds.**

If a source stops answering *after* its cache has expired, you still get
data rather than an error: the last copy we successfully fetched is served
along with `stale_seconds` and a plain-language note saying the values may
have changed. Verified live with the register killed mid-run — the caller
still received the matched benefits record, labelled `stale_seconds: 8.0`.
Past `XML_MAX_SNAPSHOT_AGE` / `REST_MAX_SNAPSHOT_AGE` (default 120s) we
stop vouching for it and report `unavailable` as normal. Old data is never
served *silently*; it's served labelled, or not at all.

The register also sits behind a circuit breaker: after
`XML_BREAKER_FAILURE_THRESHOLD` fully-failed requests in a row (default 3)
it stops calling the source for `XML_BREAKER_COOLDOWN` seconds (default 15)
and fails immediately instead. When the cooldown passes, the recovery check
asks the cheap `/health` endpoint before spending the retry budget.
Measured against a register killed mid-run: the request before the breaker
opens takes 4.17s, the one after takes 0.000s and never touches the
network, and the recovery check costs 2.05s instead of 7.12s.

All of it is explained in `DECISIONS.md`, including a real finding from
testing the breaker live.

## Testing

```bash
python3 -m unittest tests.test_solution -v
```

31 tests. Spins up its own copies of both mock services (including one
forced to fail 100% of the time) on separate ports, and checks pagination
de-dup and its page bound, degradation for every source-failure mode,
idempotency, both doors reaching the same pair, every matching outcome
including ambiguity from *either* side, the stated confidence, `count:
null` rather than `0` when the index is unreachable, malformed
XML/JSON/shape handling, cache hit and expiry measured against real
upstream request counts, the last-known-data fallback (served within its
bound, refused past it, and `stale_seconds` reaching the response), and the
circuit breaker opening, failing fast without touching the network, probing
`/health` on recovery, and closing again.

To see how accurate the matching heuristic actually is (offline analysis
using the raw data files' hidden ground-truth id, which the running
services never expose — see `DECISIONS.md`):

```bash
python3 scripts/match_accuracy_check.py
```

## Layout

```
app/
  adapters/
    resident_index.py      REST client: pagination + de-dup + cache
    benefits_register.py   XML client: retry + circuit breaker + cache
  assembly.py               combines both into a unified view; owns all degradation logic
  api.py                    HTTP layer
  cli.py                    CLI demo
  demo.html                 optional display page, served at GET / (see above) - not scored
tests/test_solution.py      integration tests against real (not mocked) service instances
scripts/match_accuracy_check.py   offline validation of the matching heuristic
data pack/                  as provided
```

See [`DECISIONS.md`](DECISIONS.md) for the reasoning behind all of the
above, [`WALKTHROUGH.md`](WALKTHROUGH.md) for a plain-language tour of what
every file actually does, and [`AI-USAGE.md`](AI-USAGE.md) for AI usage
disclosure.
