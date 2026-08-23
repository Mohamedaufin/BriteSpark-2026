# DECISIONS.md
## Unified Resident API — Problem 3: No Wrong Door

---

## Tech Stack

**Chose: Python Standard Library only.**

The floor requirement says the solution must run from a clean clone. That single constraint eliminates every third-party framework. FastAPI is excellent but it requires `pip install fastapi uvicorn`, and on an evaluator's machine that could silently fail — wrong Python version, no internet access, a corporate proxy. I am not going to let a dependency manager decide whether my code runs. The standard library is already there. It runs everywhere. That is not a compromise, that is the correct call for this problem.

**Fetches are sequential, not concurrent.** The resident index is queried, then the benefits register, one after the other within a single request. This is a real limitation rather than a design choice — a matched lookup on a cold cache pays the full XML latency (~1.5s) on top of the REST latency, added together instead of overlapped. It's listed again under *What I Would Fix First* below rather than glossed over here.

---

## Degradation Policy

**The exact policy, source by source, failure by failure.**

This is what the caller gets, and how they know:

| Failure Mode | `resident_source` | `benefits_source` | How the caller knows |
|---|---|---|---|
| Both sources healthy | `ok` | `ok` / `matched` | Normal full response. |
| XML 500, transient (within retry budget) | `ok` | `ok` / `matched` | Invisible — retry handled it. |
| XML 500, sustained — cache still fresh (<20s) | `ok` | its real status | Served from cache. Invisible to caller. |
| XML 500, sustained — cache stale (20–120s) | `ok` | its real status + `stale_seconds` | Staleness is a **field alongside the real status**, not a status of its own — a stale match is still `"matched"`, with `stale_seconds` and `stale_detail` attached. |
| XML 500, sustained — cache expired (>120s) | `ok` | `unavailable` | `benefits_source.status = "unavailable"` with reason string. |
| XML circuit breaker blocking calls | `ok` | `unavailable`, or its real status + `stale_seconds` if a usable snapshot exists | Fails fast (well under a second) rather than paying the full ~8s retry budget — see the measured table below, not literally 0ms. |
| REST pagination mid-walk failure | `ok`, with the residents collected before the break and a `partial_detail` string explaining what's missing | — | There is no `resident_source.status = "partial"` — the field that carries this is `partial_detail`, on the top-level `resident_source` block, not a fourth status value. |
| REST completely down — looked up by **register ref** | `unavailable` | `ok` | The register answers in full, so real benefits data is still served. `resident_source.status = "unavailable"` with reason. |
| REST completely down — looked up by **resident id** | `unavailable` | `not_found` | The register is reachable but holds no record under a *resident* id, so `not_found` is the honest answer, not `unavailable`. A `warning` field says absence could not be confirmed, since a source that didn't answer is not evidence the person doesn't exist. |
| REST completely down — **bulk listing** | `unavailable` | `not_attempted` | There is no population to match against, so matching is not attempted rather than attempted-and-failed. `count` is `null`, not `0` — we could not look, as opposed to looked and found nobody. |
| Both sources offline | `unavailable` | `unavailable` | Empty resident + benefits; both statuses `unavailable`. |
| Malformed XML or JSON from upstream | `ok` / `unavailable` | `ok` / `unavailable` | Parse error captured, treated same as upstream failure. |
| Identifier not found in either source | `not_found` | `not_found` | Not `no_match` — `no_match` means matching was attempted and failed to link two real records; `not_found` means the identifier itself doesn't exist in that source. Conflating them would say "we tried to link this and failed" about an identifier that was never there to begin with. |
| Ambiguous identity match (duplicate Name+DOB) | `ok` | `ambiguous` | `benefits_source.status = "ambiguous"` — system refuses to guess. Checked from **both** sides: two register rows sharing a key is the obvious case, but two *residents* sharing a key is the quiet one, since a single matching register row can't be attributed to either of them. |

The API never returns a 5xx to the caller. Every upstream failure maps to a named status field inside a 200 OK. The caller always knows exactly what is missing and why.

---

## XML Adapter: Circuit Breaker, Retry, Cache

Three layers, in order of when they activate:

**Retry (innermost):** Up to 3 attempts, 0.3s base delay with exponential backoff and jitter. Handles transient 500s from the 40% failure rate without surfacing them to the caller. Retry count is configurable in `config.py`.

**Cache (middle layer):** 20-second TTL. The XML service takes 0.7–2.4 seconds per call. Without caching, a listing request would hammer the XML service repeatedly. The 20s TTL was chosen to be short enough that caseworkers see reasonably current data, long enough to absorb burst traffic without battering a slow upstream. Staleness accepted: up to 20 seconds on a live source.

Measured live over three full cold/warm cycles against the register at 40%, waiting out the TTL between each:

| Cycle | Cold call | Immediately after (cached) |
|---|---|---|
| 1 | 3.075s | 0.034s |
| 2 | 2.607s | 0.049s |
| 3 | 6.523s | 0.031s |

Cold: **2.6s–6.5s**. Cached: **0.031s–0.049s**. The cold spread is wide because a cold call pays the register's random 0.7–2.4s delay *plus* a retry cycle whenever it draws one of the 40% failures — cycle 3 clearly hit at least one. The cached figure is the stable one, and the ~30–50ms floor that remains is the always-live resident-index `get_by_id` call, which isn't cached (see *What This Solution Does Not Do*); it's what's left once the XML fetch leaves the critical path. An earlier draft of this file claimed "under 1ms," and a later one claimed a ~120ms floor; neither reproduces — these numbers do.

**Stale-on-error fallback:** If the cache expires and the fresh fetch fails, the adapter serves the last known good snapshot up to 120 seconds old. The response includes the exact `stale_seconds` value. Past 120 seconds, the adapter stops vouching for the data and reports `unavailable`. This boundary was set at 120s to be long enough to cover a service restart (typical 30–90s), but not so long that a caseworker acts on data that could be 10 minutes stale.

**Circuit Breaker (outermost):** Not a formally named three-state machine in the code — just a consecutive-failure counter and a "don't call before this time" timestamp, which is the whole implementation and is deliberately explainable in one sentence. After `XML_BREAKER_FAILURE_THRESHOLD` (default 3) consecutive *requests* fail — requests, not individual attempts; a request that burns all 3 retries counts as one failure — the breaker sets a block-until timestamp `XML_BREAKER_COOLDOWN` seconds out (default 15).

There are two distinct phases after that, and it's worth being precise about which does what, because they behave very differently:

1. **Inside the cooldown:** the call is rejected on the timestamp alone. No `/health` check, no network traffic of any kind. This is the cheap path.
2. **The first call after the cooldown expires:** `/health` is probed first. That endpoint is exempt from the source's slowness and failure rate (per the mock service's own README), so it answers the "is it back?" question far cheaper than a full retry cycle. If `/health` fails, the breaker re-blocks for another cooldown without ever touching `/records`. If it succeeds, one real attempt is let through to test recovery.

This protects the register from being hammered during a real outage. There is no thread pool being protected — fetches are sequential (see *Tech Stack*).

**Measured live, register killed outright** (nothing listening on its port):

| Request | Time | Mechanism |
|---|---|---|
| 1 | 7.615s | Full retry budget — 3 attempts, all fail |
| 2 | 7.139s | Same; still below the failure threshold |
| 3 | 7.171s | Third consecutive failed request — breaker trips |
| 4 | 0.034s | Rejected on the timestamp. No network touched at all. |
| first after the 15s cooldown | 2.075s | `/health` probed, fails in ~2s, breaker re-blocks — **~3.5x cheaper than the ~7.2s full retry cycle it replaces** |

**Measured live, register alive but failing 100% of `/records` calls** — the more awkward case, included because it's the one that costs something:

| Request | Time | Mechanism |
|---|---|---|
| 1–3 | 6.825s, 5.211s, 4.940s | Full retry budget each |
| 4–5 | 0.042s, 0.009s | Rejected on the timestamp |
| first after cooldown | 6.346s | `/health` returns 200 — it's exempt from the failure rate — so the breaker believes the source is back and lets one real attempt through. That attempt burns the full retry budget and fails, and the breaker re-arms. |

**The honest limitation in that second table:** when a source's `/health` says "fine" while its data endpoint is failing, the recovery probe is worth nothing and each cooldown period costs one full retry cycle. That's the cost of trusting a health endpoint, and it's a real trade rather than a flaw in the implementation — the alternative, probing `/records` itself, would pay that cost on *every* check instead of once per cooldown. Every response in both tables was `200 OK`.

These are real measurements from single runs and will vary with retry jitter and OS-level connection timing — the shape (slow, slow, slow, then flat) is the reproducible part, not the third decimal place.

---

## REST Adapter: Pagination Deduplication

The REST service paginates 620 residents across 27 pages of 25. The instability causes the same record to appear on two adjacent pages when the sort order shifts between calls.

Fix: accumulate records into a dictionary keyed on `id`. A duplicate write is a no-op. No sorting required, no second-pass deduplication scan.

A `MAX_PAGES` hard limit prevents infinite loops if the service drops its `has_more` flag permanently. If mid-walk the service returns an error, the adapter does **not** report a `resident_source.status = "partial"` — that status doesn't exist. It returns the residents collected from the pages that did succeed, with a `partial_detail` string on `resident_source` explaining what's missing, so a walk that breaks on page 7 of 27 doesn't discard the 6 pages of real, current residents already in hand.

`list_all()` itself still raises on a partial walk — its contract stays "a complete list, or an exception," so nothing can mistake a short list for the whole population. The partial copy is stashed and only handed out by `list_all_or_last_known()`, which prefers, in order: a fresh complete walk, then a complete-but-stale cached copy within 120s, then this walk's partial result. A fuller slightly-older list beats a fresher smaller one, because a resident missing from a partial list reads as "this person doesn't exist," which is a worse lie than a value being a minute out of date. Both caveats are separate fields (`stale_seconds` vs `partial_detail`) rather than degrees of one, because they are different problems and a caller may reasonably treat them differently.

---

## Retry-Safety and Idempotency

The floor asks that the same request made twice not produce a different or duplicated result, and that a retried write not double anything.

**The "retried write" half is satisfied structurally, not carefully.** Every endpoint is a `GET`. Nothing in this API writes, creates, updates, or deletes anything in either source — there is no write path to double. That is worth stating plainly rather than claiming credit for defending against a case that cannot arise here.

**The "same request twice" half is a real property, and it comes from the same decision that fixes the pagination bug.** Records are accumulated into a dictionary keyed on their identifier, never appended to a list. A record arriving twice — from a page-boundary slip, or from an internal retry re-fetching a document it already partly had — overwrites itself instead of appearing twice. This holds at both levels: repeated calls to the API, and repeated attempts inside a single call. Pinned by `test_list_all_is_idempotent_across_calls` (two real walks, cache disabled, compared key-for-key) and `test_repeated_calls_are_idempotent`.

**What is *not* claimed, precisely because it would be false:** that two identical requests always return byte-identical JSON. The Benefits Register fails roughly 40% of calls at random. Two calls seconds apart can legitimately differ — one lands inside the retry budget and returns `matched`, the next exhausts it and returns `unavailable`, or returns a stale-labelled answer instead. That is the source being unreliable, which is the entire premise of the problem, not the API being non-deterministic.

The line worth being exact about is this: **the assembly is deterministic; the upstream availability is not.** Given the same underlying data actually reaching it, this code produces the same answer every time. No request ever duplicates a resident, silently drops one, or flips a match between two calls that both genuinely reached the register. What can vary is how much of the picture was obtainable at that moment — and every response says which, in its status fields, rather than leaving the caller to diff two payloads and guess.

---

## Identity Resolution

No shared primary key between the two sources. Records describing the same person do not say so.

**Approach:** Normalize `first_name`, `last_name`, and `date_of_birth` from both sources. Exact match only. If a match is found and is unambiguous — exactly one record matches on both sides, checked from both directions — the records are merged with a `confidence: 0.99` score.

**Why not fuzzy matching:** This is public service resident data. A wrong merge is worse than no merge — a caseworker acts on incorrect information about a real person. Fuzzy matching would require a tuned threshold I cannot justify without training data. Deterministic exact-match with a stated confidence is defensible. Quiet guessing is not.

**Ambiguity handling, from both sides:** If multiple benefits records share identical normalized Name and DOB, the system tags the result `ambiguous` and declines to merge — the obvious case. The quieter case is the reverse: two *residents* sharing the same normalized Name and DOB, where a single matching register row cannot be attributed to either of them. Both directions are checked, and both decline with a reason naming which side collided. No data is hidden. No wrong merge happens silently.

**Measured accuracy: 306/340 true cross-source pairs found (90% recall), 0 matched to the wrong record (100% precision)** against the data pack's hidden ground truth. See `scripts/match_accuracy_check.py`. An earlier draft of this file, and the `confidence_basis` string returned by the live API, both said "306/306" — that overstated recall by implying every attempt succeeded when 34 true pairs are correctly declined instead, because the register has a blank `Born` field for exactly those 34 records. That's a real data quality gap in the source, not a matching failure, and the matcher correctly refuses to guess on name alone rather than loosen the key and risk a false merge. Both the file and the running code were corrected to state recall and precision separately, since neither number alone was the whole truth.

The API supports bi-directional lookups. A caseworker can query with either a REST ID or an XML reference number and get the same unified view — including matching run in reverse from the register's side.

---

## Day 2 Change (40% XML Failure Rate)

The Day 2 brief made the Benefits Register's degradation permanent: it now fails roughly 40% of calls, up from 15%, and is not going to be fixed. Restarted with `--failure-rate 0.40` and left there, as instructed. The floor still applies *after* the change, which is the part that actually matters.

### What I changed

**In the solution: nothing.** Not one value. `XML_MAX_RETRIES` is still `3`, the cache TTL is still 20s, the breaker threshold is still 3 and its cooldown still 15s. No change to `assembly.py`, `api.py`, or any adapter interface. (An earlier draft of this file claimed `XML_MAX_RETRIES` was tuned in response; git history doesn't support that — it has been `3` since it was first added. The true version is the stronger one, so it's worth getting right.)

**In the test suite: two real changes**, and this is the honest asymmetry worth reporting. The suite's "normal, flaky, but working" register fixture now runs at `BENEFITS_FAILURE_RATE=0.40` — a suite still testing 15% would no longer be testing the system that exists. And two fixtures that make repeated register calls had their retry budget raised to 10, purely so the *suite* isn't flaky: at 40%, a handful of independent calls at the shipped default of 3 carries a non-trivial cumulative chance of a spurious failure (0.4³ ≈ 6.4% per call). That number is a test-reliability fixture, not a recommendation — the comments in `test_solution.py` say so explicitly, so nobody mistakes it for the shipped default.

**In the docs:** the comments in `config.py` and `benefits_register.py` that named the old rate, so the code doesn't describe a world that no longer exists.

### What I chose not to change

**The retry count.** Tempting, and wrong. Three retries against a 40% failure rate leaves ~6.4% of requests fully failing — but the cache, the stale-snapshot fallback, and the breaker are what actually carry those, not a bigger retry number. Raising retries would have made every failed request slower (each attempt costs 0.7–2.4s of real upstream latency) in exchange for a marginally lower failure rate, and would have hit an already-struggling source harder. That trade is backwards: retrying *harder* into a source that is failing 40% of the time is how you turn a degraded source into a dead one.

**The cache TTL and the breaker thresholds.** Both were verified live against the register at 40% (the measured circuit-breaker table above is from that exact configuration) and held. Changing a value that is demonstrably working, because a number elsewhere moved, is churn.

### What I'd have done differently, knowing this was coming

**I'd have written the test suite against a configurable failure rate from day one.** The suite changes above were the only real work this created, and they were retrofitted under time pressure — the failure rate is set via an environment variable in `setUpModule`, and two fixtures carry hand-tuned retry budgets with explanatory comments. That works, but a suite parameterised over failure rate from the start would have absorbed this with a single constant instead, and would have let me *prove* the defaults hold across a range rather than at two sampled points.

**I'd have prioritised concurrent fetches over the stretch goals.** Sequential fetching (see *Tech Stack*) costs more at 40% than at 15%, because a retrying register call now blocks the resident lookup for longer, more often. It's listed under *What I Would Fix First* for exactly this reason — the day-two change raised its cost, and it's the one limitation here that got materially worse rather than staying neutral.

**I'd have exposed breaker state on `/health` earlier.** With a source failing 40% of the time, "is this response slow because it's retrying, or because the breaker is about to trip?" is a question worth being able to answer from outside the process. It's in *What I Would Fix First*, but honestly it should have been in before the change, not after.

### What this validated

The adapter isolation the problem statement recommended is what made "change nothing" a defensible answer rather than a lucky one. The assembly layer never learned anything had changed, because everything that knows the register is unreliable lives inside `benefits_register.py`. That was the design bet made on day one, and this is the event it was made for.

---

## What Was Cut

**Per-endpoint metrics / Prometheus:** Useful in production, not assessed here. Cut early.

**Address-line matching as identity tiebreaker:** Would reduce ambiguous cases where two people share a name and DOB. Rejected because address data is inconsistently formatted between sources (every overlapping pair in this data pack differs only by street abbreviation, e.g. `Dr` vs `Drive`) and would encode that formatting accident as identity.

**External cache (Redis):** Adds a third dependency and a third service to manage. In-memory TTL is sufficient for a single-process API. Not cut for time — not appropriate for the problem scope.

**Authentication layer:** Explicitly out of scope per the prompt. Not built.

**Concurrent source fetches:** Not a cut so much as unfinished — see *What I Would Fix First*.

---

## What This Solution Does Not Do

- Does not persist anything. No database, no disk writes. All state is in-process memory, and a restart loses the cache and the breaker's state.
- Does not handle more than two sources. The adapter split makes a third straightforward to add, but only REST and XML are implemented.
- Does not retry REST failures. The REST source is not observed to fail in this problem's fixtures. Retries were not implemented to avoid retry-storm risk across an already-paginated, in-flight walk.
- Does not match on phone number or address. Name + DOB only.
- Does not authenticate callers.
- Does not fetch the two sources concurrently. Sequential, one after the other — see *Tech Stack*.
- Does not cache `get_by_id` / `get_by_ref` — only the bulk `list_all` paths are cached, since that's what's actually on the matching hot route.

---

## What I Would Fix First

**Fetches should be concurrent, not sequential.** The single biggest real latency win available on a cold cache, and the one item on this list that's a genuine gap rather than a considered cut.

**The 20s TTL is a magic number.** It should be environment-configurable with a clearly documented rationale per environment (e.g., 5s for a live caseworker terminal, 60s for a batch reporting job). Currently it is a single hardcoded default in `config.py` — configurable via env var, but the *value itself* isn't reasoned about per deployment.

**`get_by_id` / `get_by_ref` should be cached too**, on the same terms as `list_all`. Repeat lookups of the same identifier currently pay full latency every time.

**The circuit breaker state should be observable.** The current `/health` endpoint reports source up/down but doesn't expose the breaker's internal state (consecutive-failure count, blocked-until timestamp), so there's no way to tell from outside whether a slow response is "a normal retry" or "about to trip the breaker."
