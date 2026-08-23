# Decisions

**If you're short on time:** the floor is met and verified from a genuine
fresh clone (see *Verification evidence*), the degradation policy this
problem asks for by name is the table under *The degradation policy*, and
*What this does not do* states every cut plainly. All three "if you have
time" items are attempted (*Identity matching*, *Caching*, *Circuit
breaking*) - each scoped so none of them touch the floor's
behaviour. *The simplification pass, and why* records the features that
were built, measured, and then deliberately removed — that section is the
one to read if this looks simpler than it needed to be. *Day two* is empty
because the change hasn't landed yet.

## Stack

Python 3, standard library only — no dependencies, nothing to `pip install`. Both
mock services are already stdlib-only Python; matching that keeps the whole
project zero-install and removes an entire category of clean-clone failure
(wrong package versions, a missing venv, a proxy blocking pip). `http.server`,
`urllib.request`, and `xml.etree.ElementTree` do everything this problem needs.

**A framework was considered and rejected on the record**, because the
obvious question is why this isn't FastAPI. Three reasons, in order of
weight: *runs from a clean clone* is a floor item, and trading zero
dependencies for a venv plus pinned packages adds failure modes without
removing any; automatic OpenAPI documentation is the main thing a
framework would add here, and this problem states plainly that interface
quality isn't assessed and no UI is required; and the one genuine
technical argument — concurrency — turned out not to need a framework at
all. `ThreadingHTTPServer` already gives per-request concurrency, and
reading both sources on a thread pool bought a measured 2.2s on the cold
path without changing the stack. A rewrite would have re-implemented
working, tested behaviour on new machinery for a benefit that was
available without it.

## Architecture

Three layers, each independent of the others:

- `app/adapters/resident_index.py` — talks to the REST source. Nothing else
  knows this source is paginated or that pagination has a boundary bug.
- `app/adapters/benefits_register.py` — talks to the XML source. Nothing else
  knows this source is slow or that it fails ~15% of the time.
- `app/assembly.py` — combines whatever the adapters hand back. Doesn't make
  HTTP calls, doesn't know about pagination or retries, only knows how to
  turn "resident got this, benefits got that (or didn't)" into one response.

This is deliberate for the day-two change: whichever of "a source's shape
changes," "a third source appears," or "the merge rule changes" happens, it
should land in exactly one of these three places without touching the other
two.

**The two adapters are completely independent files.** Neither imports the
other. Neither shares a base class with the other. Each one is a small,
self-contained class you can read top to bottom: how it fetches, how it
parses, how it handles that source's particular bad day.

That choice was made twice, and reversed once, which is worth being honest
about. A shared base class was built — one place for the cache, the retry
budget and the breaker, so the degradation policy couldn't drift between
sources. It worked, and it was measurably less code. It was removed anyway,
because it meant that understanding "how does the resident index work"
required reading two files and following inheritance between them, and
**a design nobody can walk a reviewer through is worse than a design with
some repetition in it.** The repetition here is small and visible; the
indirection it replaced was not.

The two adapters also aren't symmetrical, which is the other reason a shared
base earned less than it looked like it did:

| | Resident Index | Benefits Register |
|---|---|---|
| Fails? | No, not in this problem | Yes, ~1 in 7 calls |
| Retry loop | Not needed | Yes |
| Circuit breaker | Not needed | Yes |
| Cache | Yes (a full page walk is expensive) | Yes (it's slow) |
| Pagination | Yes, with a duplicate bug | No, one call returns everything |

Building shared machinery for that meant building it for one source and
giving the other a copy it doesn't use.

## The degradation policy

Every response carries a `resident_source` and a `benefits_source` block,
each a `{status, error}` pair. Each is one of `ok`, `matched`, `no_match`,
`ambiguous`, `not_found`, `unavailable`, `not_attempted` — never conflated,
so the caller always knows *why* something is missing rather than getting a
bare `null` or a generic failure. `found_by` names which identifier opened
the view.

Read the table by which door was used. `ok` means "this source answered
directly for the identifier you gave"; `matched` means "this source was
reached across the join, with a stated confidence".

| Situation | `resident` | `resident_source` | `benefits` | `benefits_source` |
|---|---|---|---|---|
| Index id, both healthy, join found | present | `ok` | present | `matched` (+ `confidence`) |
| Index id, no register record shares name+DOB | present | `ok` | `null` | `no_match` |
| Index id, key ambiguous on either side | present | `ok` | `null` | `ambiguous` — declines to merge |
| Index id, resident has no date of birth | present | `ok` | `null` | `not_attempted` (nothing to join on) |
| Index id, `?match=off` | present | `ok` | `null` | `not_attempted` (caller opted out) |
| Index id, register 500ing after retries | present | `ok` | `null` | `unavailable` (+ reason) |
| Register ref, both healthy, join found | present | `matched` (+ `confidence`) | present | `ok` |
| Register ref, register-only person | `null` | `no_match` | present | `ok` |
| Register ref, index unreachable | `null` | `unavailable` (+ reason) | present | `ok` |
| Identifier in neither, both healthy | `null` | `not_found` | `null` | `not_found` |
| Identifier not found, but a source was down | `null` | `not_found` / `unavailable` | `null` | `unavailable` / `not_found` | 
| Index unreachable on the bulk endpoint | — | `unavailable` | — | `not_attempted`; **`count` is `null`, not `0`** |
| A source stopped answering, but we hold a recent copy | present | `ok` + **`stale_seconds`** | present | `ok` / `matched` + **`stale_seconds`** |

That last row is the strongest form of "partial data beats an error page"
in here, so it is worth being explicit about. If the register goes down but
we successfully read it a minute ago, the caller does **not** get
`unavailable` — they get the resident, the matched benefits record, and
`stale_seconds: 60` plus a `stale_detail` saying the values may have
changed since. Verified live, with the register killed mid-run:

```json
"benefits_source": {
  "status": "matched",
  "confidence": 0.99,
  "stale_seconds": 8.0,
  "stale_detail": "this source did not answer, so these values come from
                   the last copy we successfully fetched - they may have
                   changed since"
}
```

Old data is never served *silently* — that would be the exact failure this
problem warns about. It is served labelled, or not at all. See *Caching*
below for where the bound is.

Two more rows are the ones worth arguing about, and both come down to
the same rule — *we only report absence when we actually looked*:

- **Identifier not found while a source was down** carries an extra
  `warning` saying the identifier "cannot be confirmed as absent — only as
  not found in the sources that answered." A 404-shaped answer built on an
  unreachable source is a guess wearing a fact's clothes.
- **`count: null` on the bulk endpoint** when the index is unreachable.
  `0` would assert an empty population; the truth is an unknown one. This
  was a real defect found in review — the code said `0` while this document
  claimed we never pretend a missing source had nothing to say. Fixed, and
  pinned by `test_population_count_is_null_not_zero_when_the_index_is_down`.

The API returns **HTTP 200 with a status field, not a 5xx**, for every case
above. `app/api.py`'s only path to a 5xx is a last-resort `try/except`
around the whole HTTP handler, for anything genuinely unanticipated —
nothing in the source-failure paths above ever reaches it, because
`assembly.py` catches every `SourceUnavailable` itself. Verified both by
hand (killing each service mid-run and re-querying the same endpoint) and
by `tests/test_solution.py::UnifiedAssemblyTests`.

## Either identifier opens the view

The problem is called *No Wrong Door*, and the scenario is explicit:
whichever office a resident walks into, they should not have to tell their
story again. An API that only accepts a Resident Index id fails that on its
own terms — staff holding a Benefits Register ref would have to go and find
an index id first, which is the browser-tab hunting this is meant to
replace.

`GET /unified/residents/<identifier>` therefore accepts either. It tries
the index id first (one cheap direct call), and on a miss tries the
register ref. `found_by` reports which door opened, and the two doors reach
identical resident/benefits payloads for the same pair — pinned by
`test_both_doors_reach_the_same_pair`.

The consequence that matters most: **200 people in the data pack exist only
in the Benefits Register.** Anchoring solely on the index made every one of
them invisible to this API. They are now reachable by ref, returning their
register record with `resident_source: no_match` — present, with an honest
account of what is not known about them, rather than absent.

Register refs contain slashes (`NO/2019/4697`), so the route takes the
whole remainder of the path rather than one segment. Percent-encoded refs
work too; both are tested.

## Pagination safety: de-dup and a bound

`ResidentIndexClient.list_all()` walks every page and inserts into a dict
keyed by `id`. Two things follow from that one design choice:

- **De-dup.** The service's boundary-slip bug serves a small number of
  records on two consecutive pages; keying on `id` collapses that for free,
  without needing to know which pages it happened on, and does so
  structurally rather than just for the observed ~60% slip rate — a record
  repeated any number of times still collapses to one entry. Verified:
  `list_all()` returns exactly 620 unique ids against the 620-record
  dataset, on every run (`test_list_all_has_no_duplicate_ids`).
- **A bound.** The walk itself had no limit on page count until a resilience
  audit caught it: a source that pathologically never set `has_more=false`
  would page forever. Each individual HTTP call already has a timeout, but
  the *walk* didn't — an unbounded loop is the same "your API hangs" failure
  as a slow request, just reached by a different door. `MAX_PAGES = 10_000`
  (far beyond any plausible page count here) caps it: once exceeded,
  `list_all()` raises `SourceUnavailable` with a clear message instead of
  looping indefinitely, and the caller gets the same clean `unavailable`
  status as any other source failure. Covered by
  `test_pagination_walk_is_bounded_against_a_source_that_never_stops`,
  using a stub server that always answers `has_more: true`.

## Retry policy

Retrying lives in `BenefitsRegisterClient._get`, and only there. The
resident index has no retry loop, because it has no failure mode in this
problem — adding one would be machinery that never runs, which is harder to
justify in review than its absence.

Only network errors and non-2xx/404 status codes are retried — 3 attempts
by default, with the wait doubling after each failure plus a little
randomness (`XML_MAX_RETRIES` / `XML_RETRY_BASE_DELAY` env vars). A single
500 is normal operation for that source, not a fault — treating it as
"down" after one failed call would degrade far more aggressively than the
source actually warrants.

A response that arrives but fails to parse (bad XML, bad JSON, an
unexpected shape) is deliberately **not** retried — it fails once and
surfaces immediately as `unavailable`. The distinction is transience: a 500
this second might succeed next second, so retrying it is buying real
information. A malformed response is a deterministic fact about that
payload; retrying would just re-fetch the same garbage and burn the retry
budget for no gain. Only a real, sustained failure (network error,
persistent 500s, or an unparseable response) surfaces as
`SourceUnavailable`, which the assembly layer turns into `unavailable`
rather than letting it bubble into a stack trace.

## Retry-safety / idempotency

The API is read-only — no endpoint writes anything, so there's no
double-submit case to guard against. What idempotency means here is: the
same request, called twice, returns the same result, and repeated internal
retries never produce duplicates in the output. Both are handled by the
same mechanism as the pagination bug — dict-keyed assembly rather than
list-appending — and both are covered by
`test_list_all_is_idempotent_across_calls` and
`test_repeated_calls_are_idempotent`.

## Identity matching: on by default, deterministic, never a silent guess

The problem statement is explicit that this is a stretch goal, not a
requirement, and that a wrong silent merge is worse than no merge. Four
consequences follow:

1. **On by default — reversed from an earlier decision, deliberately.** It
   was originally opt-in behind `?match=basic`, reasoning that the floor
   must not depend on matching. That reasoning was right; the conclusion
   was wrong. The scenario asks for *"one call, one resident, everything
   known about them"*, and a default response that hides the join behind a
   flag the caller has to know about does not deliver that. Nothing is
   protected by defaulting it off, because **declining is always safe**: the
   worst case is `no_match` or `ambiguous`, never a wrong merge. The floor
   still does not depend on it — `?match=off` turns it off completely and
   the resident half of every response is unchanged.
2. **Deterministic, not fuzzy.** The match key is normalized
   `(last_name, first_name, date_of_birth)` — uppercased, punctuation
   stripped, exact string equality. No nickname handling, no
   edit-distance/fuzzy matching. Fuzzy matching is exactly where false
   positives creep in, and this problem punishes that specifically.
   Addresses are deliberately *not* a join key: every overlapping pair in
   this pack differs only by street abbreviation (`Dr` vs `Drive`), so
   joining on them would encode a formatting accident as identity.
3. **Ambiguity is checked from both sides.** Two register rows sharing a
   key is the obvious case. The quiet one is two *residents* sharing a key,
   where a single matching register row cannot be attributed to either of
   them — attaching it to whichever was seen first would be exactly the
   silent wrongness the problem warns about. Both decline with
   `ambiguous` and a reason naming which side collided. This dataset has no
   resident-side collisions, so the check costs nothing today (recall is
   unchanged at 306/340); it exists so that a data change cannot turn a
   collision into a wrong answer. Pinned by
   `test_two_residents_sharing_a_key_cannot_both_claim_one_register_row`.
4. **A match states its confidence, in the response.** The stretch goal
   asks for matching *"with a stated confidence"*, so the number belongs in
   the payload, not only in this file: every `matched` carries
   `confidence: 0.99` and a `confidence_basis` string. The value is
   constant on purpose. The rule is exact equality on a normalized triple,
   unique on both sides — every match it produces rests on identical
   evidence, so a per-pair score that varied would be invented rather than
   measured. 0.99 rather than 1.00 because 306 correct out of 306 is
   evidence of a very good rule, not proof of an infallible one.

**This was measured, not assumed.** `scripts/match_accuracy_check.py` reads
the raw data files directly (permitted per the data pack README — "you may
read these, but your solution must go through the services") and uses the
hidden `_pid` field, present in the raw JSON but stripped by both live
services, as ground truth for which records actually describe the same
person. It is not part of the running solution and the API never sees
`_pid`. Result, run through the actual `app.assembly` matching code:

```
True cross-source pairs in the dataset: 340
  matched correctly:                    306
  matched to the WRONG record:            0
  declined as ambiguous:                  0
  no candidate found (declined to guess): 34

Recall:    90.0%
Precision: 100.0%
```

The 34 misses are not a matching failure — the Benefits Register has a
blank `Born` field for exactly those 34 records (a real data quality gap in
the legacy source), and the matcher correctly refuses to match on name
alone rather than loosen the key and reintroduce false-positive risk.

**Both outcomes are regression-tested, not just measured once by script.**
`test_finds_a_true_cross_source_match` and
`test_no_match_when_no_benefits_record_shares_the_key` pin the *positive*
and *negative* case against two specific, confirmed residents (R-10697
matches, R-10394 doesn't). A matcher that only gets tested on "does it find
matches" can silently start matching everything — including things it
shouldn't — without a single test failing. Testing the negative case is
what would actually catch that regression;
`test_matching_never_returns_a_false_positive_on_a_key_collision` covers
the third case (ambiguous collisions) the same way.

## Caching, and the staleness accepted in exchange

Both sources cache their full-population call for `XML_CACHE_TTL` /
`REST_CACHE_TTL` seconds (default 20 each, env-configurable). The cache is
deliberately the simplest thing that works: the last successful result, plus
the time we fetched it. If that time is less than the TTL ago, return the
saved copy; otherwise fetch again.

**The staleness accepted, stated plainly: up to 20 seconds.** A resident
whose benefit code changed in the last 20 seconds may be shown their
previous one. That is the trade, and it is a good one here, because the
alternative is paying the register's ~1.5s delay on every single request
including repeat lookups of the same person.

Measured live against the real mock service: a cold call takes **3.55s**, a
cached call takes **under a millisecond**.

Both sources are cached, for different reasons. The register because it is
genuinely slow. The index because a full page walk (25 requests) is on the
single-resident path — the register-ref door and the uniqueness check both
need the whole population, and paying 25 requests per lookup would be
careless.

**Only successful fetches are cached.** A failure is never cached, so a
source that is failing can never poison the cache with an empty answer.

### When the source is down and the cache has expired

The TTL above answers "how old can data be on a normal day." This answers
"what happens on a bad one," and it is where the floor's first requirement
does the most work.

`list_all_or_last_known()` returns `(records, seconds_old)`. It tries a
fresh fetch; if that fails and we still hold a copy younger than
`max_snapshot_age` (`REST_MAX_SNAPSHOT_AGE` / `XML_MAX_SNAPSHOT_AGE`, both
default 120s), it returns that copy along with its exact age. The whole
thing is eight lines:

```python
try:
    return self.list_all(), None
except SourceUnavailable:
    if self._cache is not None:
        age = time.monotonic() - self._cached_at
        if age < self.max_snapshot_age:
            return self._cache, age
    raise
```

Two bounds keep this honest rather than fuzzy:

- **It is never silent.** A fallback answer always carries `stale_seconds`
  and `stale_detail` in the response. Serving old data *without saying so*
  would blur "this is current" and "we could not reach the source" —
  exactly the distinction the degradation policy exists to keep crisp.
- **It gives up at `max_snapshot_age`.** Past two minutes we stop vouching
  for the copy and report `unavailable` as normal, because at some point
  old data stops being useful and starts being misleading. With no copy at
  all — a cold start during an outage — it fails immediately, unchanged.

**Why 120s rather than something longer:** a staff member acting on
two-minute-old benefits data is making a reasonable decision; one acting on
ten-minute-old data during an unnoticed outage is not. The number is a
judgement about how long a human can act on stale information before it
becomes a liability, not a technical limit.

Covered by `test_list_all_is_cached_within_ttl`, `test_cache_expires_after_ttl`
(both asserting on real upstream hit counts, not timing), and
`LastKnownDataTests` — serves within the bound, refuses past it, reports a
fresh answer as fresh, and gets `stale_seconds` all the way through to the
API response.

## Circuit breaking: two numbers

The retry budget above absorbs the register's normal ~15% flakiness. It does
nothing about a source that is *comprehensively* down — every request would
still sit through the full retry-and-backoff cycle before failing, so an
outage makes every caller slow as well as unlucky.

The breaker is two variables on the client:

```python
self._consecutive_failures = 0   # failed requests in a row
self._blocked_until = 0.0        # don't call the source before this time
```

And four rules, all in `BenefitsRegisterClient._get`:

1. Before calling, if `time.monotonic() < self._blocked_until`, raise
   immediately without touching the network.
2. If we had given up on this source and the cooldown has now passed, this
   call is the one that finds out whether it came back — so ask `/health`
   first (see below).
3. On any success, reset `_consecutive_failures` to 0 and clear the block.
4. When a request fails every retry, increment `_consecutive_failures`.
   Once it reaches `XML_BREAKER_FAILURE_THRESHOLD` (default 3), set
   `_blocked_until` to now + `XML_BREAKER_COOLDOWN` (default 15s).

There is no state machine and no "half-open" enum, because the behaviour
falls out of those four rules: the first request after the cooldown expires
*is* the trial, it just doesn't need a name.

**Rule 2 is worth its own paragraph.** The data pack's own README says
`/health` is exempt from this source's slowness and its failure rate — so
it can answer "is it back yet?" in one fast request, where a data call
costs the full retry-and-backoff budget. Spending that on a source we
already believe is down, once per cooldown for the length of an outage, is
waste we can see coming. Measured live: **7.12s** for a blind retry cycle
versus **2.05s** for the probe-and-decline path, 3.5x cheaper.

Measured live against a register that had been killed mid-run:

```
request before the breaker opens (full retry cycle) : 4.17s
request after the breaker opens                     : 0.000s
recovery check (probes /health, source still down)  : 2.05s
the same recovery check without the probe           : 7.12s
```

The source is not contacted at all once the breaker is open. Covered by
`test_circuit_breaker_opens_after_threshold_and_fails_fast` (which asserts
on upstream hit counts — an open breaker must not touch the network),
`test_recovery_check_asks_health_before_paying_for_the_data_call` (which
asserts `/health` was hit and `/records` was *not*), and
`test_circuit_breaker_recovers_after_cooldown`.

**A real finding, from testing this live rather than trusting the tests
alone:** with a short cooldown chosen for a fast manual demo (6s), one live
reproduction let a request through when a fail-fast rejection was expected.
Investigated rather than dismissed: the gap turned out to be time spent
between two separate manual test invocations, not the code misbehaving —
and that gap counts against the cooldown just as much as network latency
does. Not a logic bug, but a reminder that the cooldown needs comfortable
margin over the worst-case failed request (~4-7s on this machine, since
connection-refused is not instant on Windows the way it is on Linux). That
is exactly why the shipped default is 15s rather than the 6s used for the
demo.

## What this does not do

- **No persistence, no auth, no UI.** Not required; not built.
- **`GET /unified/residents` (bulk) re-fetches the full population and full
  benefits dump on every cache miss.** Fine at 620/540 records; would not
  scale to a much larger register without the caching above.
- **The cache is in memory.** A restart refetches, and a restart *during*
  an outage leaves the last-known-data fallback with nothing to fall back
  on. A file would fix it; not built, because nothing in this problem
  restarts the process and persistence is explicitly not required.
- **Two API instances would hold independent caches** and could briefly
  disagree by up to one TTL. Single-process is the only shape this problem
  asks for.
- **The two sources are read one after the other, not in parallel.** Reading
  them concurrently on a thread pool was built and measured — it saved 2.2s
  on a cold bulk call — and was then removed. The saving is real, but it is
  latency on a call that is already cached 20 seconds at a time, and it cost
  more in explainability than it returned. `list_all()` on one source, then
  the other, then combine.
- **No fuzzy fallback for the 34 blank-DOB misses.** Deliberate — see
  above. A name-only match would recover some of these but reintroduce the
  exact false-positive risk the problem doc warns against.

## Hardening against malformed upstream responses

The floor's degradation policy above was originally built against the two
failure modes the mock services actually produce (HTTP error codes and
unreachability). It had a gap: a response that comes back with a `200` but
isn't valid XML or valid JSON, or is valid JSON in an unexpected shape, was
not handled — `xml.etree.ElementTree.ParseError`, `json.JSONDecodeError`,
and `KeyError` on a missing `id`/`results` field would all have escaped
past the adapters and become an unhandled exception instead of a
`resident_source`/`benefits_source: unavailable` status.

Neither mock service actually sends malformed data — this is defending
against a plausible future failure mode, not a bug either service has. Both
adapters now treat "got a response but can't trust its shape" identically
to "didn't get a response at all": it raises `SourceUnavailable`, which
`assembly.py` already knew how to turn into a clean status. Nothing new was
taught to the assembly layer — the fix is entirely in the adapters staying
honest about what they can and can't parse. Covered by
`tests/test_solution.py::MalformedUpstreamTests`, using small stub servers
since the real services can't be made to emit garbage.

## Verification evidence

Every claim above is checked, not asserted, and re-checked from a genuine
`git clone` of the pushed repository — not just the local working copy —
before being relied on:

```
$ git clone https://github.com/Mohamedaufin/BriteSpark-2026.git
$ cd BriteSpark-2026
$ python3 -m unittest tests.test_solution -v
...
Ran 31 tests in ~42s

OK
```

31/31 pass: pagination de-dup and its page bound, idempotency, degradation
for every source-failure mode, both doors reaching the same pair, all four
matching outcomes (matched, no_match, ambiguous from each side,
not_attempted), the stated confidence, `count: null` rather than `0` on an
unreachable index, malformed XML/JSON/shape handling, cache-hit and
cache-expiry measured against real upstream hit counts rather than timing,
the last-known-data fallback (served within the bound, refused past it, a
fresh answer not mislabelled, and `stale_seconds` reaching the API
response), and the circuit breaker opening, failing fast without touching
the network, probing `/health` on recovery, and closing again.

Also manually verified end-to-end against the real mock services, several
times over: health check; a default lookup by index id returning the joined
view with its confidence; the same pair reached by register ref; one of the
200 register-only people returning their record with `resident_source:
no_match`; an unknown identifier returning `not_found` from both sources;
killing the benefits register mid-run and re-querying (HTTP 200, resident
data intact, benefits explicitly `unavailable`); and an unknown identifier
*while* a source was down, carrying the "cannot be confirmed as absent"
warning.

The performance numbers quoted in this document are measured, not
estimated. They come from one self-contained script run against the real
services — it starts them as subprocesses, measures, kills the register
mid-run, and measures again, with no gaps between separate invocations
(those gaps are what made an earlier breaker measurement ambiguous until it
was re-run this way):

```
bulk view of all 620 residents, matched         : 1.55s
cold call to the register                       : 3.55s
the same call, served from cache                : under 1ms

request before the breaker opens                : 4.17s
request after the breaker opens                 : 0.000s
recovery check, probing /health first           : 2.05s
the same recovery check without the probe       : 7.12s

register killed mid-run, cache expired:
  benefits_source.status                        : "matched"  (not unavailable)
  benefits_source.stale_seconds                 : 8.0
  the benefit code was still returned to the caller
```

The matching accuracy script was re-run after the bidirectional ambiguity
check was added, to confirm the stricter rule cost no recall: still 306/340,
precision still 100%. Re-run again after the simplification pass below, for
the same reason — a refactor that quietly changed matching behaviour would
be exactly the kind of regression a "nothing else changed" claim hides:
still 306/340, precision still 100%.

## The simplification pass, and why

Late in the build this solution grew a shared adapter base class, concurrent
source reads on a thread pool, a health-gated circuit probe, a
last-known-data fallback, a refresh lock, retry and breaker machinery on the
resident index, and partial data on a truncated page walk. All of it worked
and was tested; some of it was measurably faster.

Most of it was then removed, and this section exists so that reads as a
decision rather than as something never attempted.

The reason is that **the submission includes a Q&A, and the handbook is
explicit that "the model wrote that" is not an answer.** Code I cannot walk
a reviewer through line by line is a liability no benchmark offsets. Each
of those features added a concept to defend — inheritance and method
resolution, thread pools and shared state, a three-state circuit machine —
and together they made the solution harder to hold in one head than the
problem it solves.

Two were then deliberately restored, on the test of whether they earn their
explanation cost *against the problem statement* rather than in general:

| Kept / restored | Why it earns its keep | Removed | Why it doesn't |
|---|---|---|---|
| Last-known-data fallback | Floor item 1: "partial data beats an error page." Turns an `unavailable` into real data plus its age | Thread-pool concurrency | Nothing in the rubric grades latency; threads are the hardest concept here to defend |
| Health-gated recovery probe | 3.5x cheaper recovery on a named stretch goal; one sentence to explain | Shared adapter base class | Arguably works against the day-two wording about adapter independence |
| Circuit breaker (two variables) | Named stretch goal | Truncated-walk partial data | The path cannot trigger with the supplied data pack; costs a response field a judge will never see |
| TTL cache on both sources | Named stretch goal | Retry + breaker on the index | That source does not fail in this problem; machinery that never runs |
| | | Refresh lock | Good practice, but not something the problem asks about |

The one measurable cost of the removals is that a cold bulk call takes
~2.2s longer than it did with concurrency. That is latency on a call
already cached 20 seconds at a time, and nothing in the problem statement
grades it. The trade was clarity for speed, made knowingly.

## A note on the optional `/` display page

`app/demo.html`, served at `GET /`, is a small static page: a search box,
a "Sources" health panel, and a "Backend API →" button that opens the exact
raw JSON endpoint it just called. It exists to make manual poking around
the API faster during review, nothing more.

It is deliberately **not** part of the graded solution — the problem
statement says explicitly that interface quality isn't assessed here and a
CLI/curl demonstration is fine, and nothing in the floor or in this
document depends on it existing. To keep that boundary real rather than
just claimed: the page contains zero business logic. It calls the same
three JSON endpoints anyone else would (`/health`,
`/unified/residents/<id>`), renders exactly what comes back, and recomputes
nothing — matching, degradation, and dedup all still happen only in
`app/assembly.py`. If it were deleted entirely, the actual solution
(API + CLI + tests) would be unaffected.

## A note on `run_both.sh`

It hardcodes `python3`, which isn't on PATH on at least one Windows setup we
tested against (only `python` was). We left the provided script untouched —
it's not ours to change — and instead gave a `python`-fallback command
sequence in the README, verified against a real `git clone` into a fresh
directory before relying on it.

## Day two

*(To be filled in once the change lands, with what it was and where it went.)*
