# Code walkthrough

A plain-language tour of every file, in the order the code actually runs.
`DECISIONS.md` explains *why* things were chosen; this explains *what the
code does*, line by line, so it can be talked through out loud.

---

## The shape of the whole thing

```
a request arrives
   ↓
app/api.py        works out which URL was asked for
   ↓
app/assembly.py   decides what to fetch and how to combine it
   ↓
app/adapters/     actually talk to the two source systems
   ↓
app/assembly.py   builds one dictionary describing what was found
   ↓
app/api.py        turns that dictionary into JSON
```

Four files do the work. Two of them (`api.py`, `cli.py`) are just doors
into the same logic — one for HTTP, one for the command line.

**The one rule that shapes everything:** a source failing is never allowed
to become an error page. It becomes a *status field* in a normal 200
response. Everything below is a consequence of that rule.

---

## `app/errors.py` — 2 lines

```python
class SourceUnavailable(Exception):
    """A source could not answer this call. Message is caller-facing."""
```

One custom exception. The adapters raise it whenever a source can't answer;
`assembly.py` catches it and turns it into a status. That's the entire
contract between the two layers.

*Why a custom exception rather than letting `urllib` errors escape?* Because
"the resident index is down" and "there's a bug in my parsing code" should
not look the same to the layer above. Anything the adapter can blame on the
source becomes `SourceUnavailable`; anything else is a genuine bug and is
allowed to crash loudly.

---

## `app/config.py` — settings

Every tunable value, read from an environment variable with a default:

```python
XML_CACHE_TTL = float(os.environ.get('XML_CACHE_TTL', '20'))
```

Nothing clever. It exists so that no number is buried in the middle of the
code, and so a reviewer can change the cache TTL or the retry count without
editing logic.

---

## `app/adapters/resident_index.py` — the REST source

This source is mostly reliable. It has exactly one problem: **it sometimes
returns the same record on two different pages.**

### `_get(path)`

One HTTP GET. Returns `(status_code, parsed_json)`.

Three things can happen:
1. Normal response → return the code and the parsed body.
2. An HTTP error (404, 500) → `urllib` raises `HTTPError`, but that object
   still contains a readable body, so we read it and return the code
   anyway. The caller decides whether a 404 is a problem.
3. Can't connect at all, or the body isn't valid JSON → raise
   `SourceUnavailable`.

> **Q: Why treat unparseable JSON the same as being unreachable?**
> If a source sends me something I can't parse, I have no reason to trust
> its status code either. "It said 200" means nothing if the body is
> garbage. Both mean the same thing to my caller: this source did not give
> me a usable answer.

### `get_by_id(resident_id)`

Fetch one resident. Returns the record, or `None` if the source says 404.
A 404 is not an error — it's a fact, and the caller needs to tell "no such
person" apart from "couldn't ask."

### `list_all()` — the important one

```python
by_id = {}
page = 1
while True:
    ...fetch page...
    for record in body['results']:
        by_id[record['id']] = record   # <-- this line fixes the duplicates
    if not body.get('has_more'):
        break
    page += 1
```

> **Q: How do you handle the duplicate-across-pages problem?**
> I don't build a list, I build a dictionary keyed on the resident id. If
> the same record arrives on page 3 and again on page 4, the second one
> overwrites the first and the dictionary still has one entry. I never have
> to detect a duplicate, or know which pages it happened on — the data
> structure makes duplicates impossible. Against the data pack it returns
> exactly 620 residents every time.

Two guards in the same loop:

- **`MAX_PAGES`** — if the source never says `has_more: false`, stop at
  10,000 pages and raise `SourceUnavailable`. Each individual request has a
  timeout, but a loop of successful requests doesn't; without this, a
  misbehaving source hangs the API forever.
- **Shape checks** — if `results` isn't a list, or a record has no `id`,
  raise `SourceUnavailable` rather than letting a `KeyError` crash the API.
  A source sending the wrong shape is misbehaving, exactly like a 500.

### The cache

```python
if self._cache is not None and time.monotonic() - self._cached_at < self.cache_ttl:
    return self._cache
```

Two variables: the last successful result, and when it was fetched. If that
was less than 20 seconds ago, hand it back. Otherwise fetch again.

> **Q: Why cache a source that isn't slow?**
> Because a full walk is 25 HTTP requests, and looking up one resident
> needs the whole population — to check the name+date-of-birth is unique on
> my side before I trust a match. 25 requests per lookup would be careless.

### `list_all_or_last_known()` — the bad-day version

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

Returns a pair: the records, and how many seconds old they are. `None` for
the age means the answer is fresh.

> **Q: Explain this one.**
> Try to get fresh data. If that fails but I still have a copy from less
> than two minutes ago, hand back that copy and say how old it is. If the
> copy is older than that, or I never got one, fail like normal.

> **Q: Isn't serving old data exactly what the problem warns against?**
> It warns against *silently* pretending a missing source had nothing to
> say. This is the opposite of silent — every fallback answer carries
> `stale_seconds` and a note saying the values may have changed. And it's
> the other half of the same requirement: "partial data beats an error
> page." If the register died thirty seconds ago, a staff member is much
> better served by thirty-second-old benefits data labelled as such than by
> an error.

> **Q: Why two minutes?**
> It's a judgement about people, not about the system. Someone acting on
> two-minute-old benefits data is making a reasonable decision. Someone
> acting on ten-minute-old data during an outage nobody noticed isn't. Past
> the bound I'd rather say "I don't know" than quietly be wrong.

---

## `app/adapters/benefits_register.py` — the XML source

This source is **slow** (~1.5s) and **fails about one call in seven**. Both
are normal for it. This file is where that's dealt with.

### `_get_once(path)`

One attempt, no retrying. Same shape as the REST client's `_get`, but
returns raw text because this source speaks XML.

### `_get(path)` — retry and breaker

This is the most involved function in the codebase, and it does three
things in order:

**1. Is the breaker open?**

```python
if time.monotonic() < self._blocked_until:
    raise SourceUnavailable(...)
```

If we've decided this source is down, don't call it. Return immediately.

**2. Retry loop**

```python
for attempt in range(self.max_retries):
    code, text = self._get_once(path)
    if code == 200:
        self._consecutive_failures = 0   # success clears the count
        return text
    ...
    time.sleep(self.retry_base_delay * (2 ** attempt) + random.uniform(0, 0.1))
```

Try up to 3 times. Wait longer after each failure — 0.3s, then 0.6s, then
1.2s — plus a small random amount.

> **Q: Why does the wait double?**
> If the source is briefly overloaded, hammering it every 0.3s makes that
> worse. Backing off gives it room to recover. The random extra is so that
> if several requests fail at the same moment, they don't all retry at
> exactly the same instant and spike it again.

> **Q: Why retry a 500 at all?**
> Because this source returns 500s as normal behaviour — roughly 1 in 7
> calls, by design. One 500 is not an outage. If I reported "unavailable"
> after a single failure, I'd be degrading on almost every request when the
> source was working fine.

> **Q: What don't you retry?**
> Malformed XML. If the response arrives but can't be parsed, asking again
> just fetches the same broken thing — it's a fact about that response, not
> bad luck. It fails once and surfaces immediately.

**3. Trip the breaker**

```python
self._consecutive_failures += 1
if self._consecutive_failures >= self.breaker_failure_threshold:
    self._blocked_until = time.monotonic() + self.breaker_cooldown
```

> **Q: Explain your circuit breaker.**
> It's two variables: a count of failures in a row, and a timestamp before
> which I won't call the source. Every request that fails all its retries
> increments the count. When the count hits 3, I set the timestamp to 15
> seconds from now, and until then every call fails instantly without
> touching the network. Any success resets the count to zero.

> **Q: What about the "half-open" state — how do you test if it recovered?**
> I don't need a state machine for that. When the 15 seconds are up, the
> next request is the one that finds out. That request *is* the trial — it
> just doesn't need a name. What it does do is check `/health` first:
>
> ```python
> if self._consecutive_failures >= self.breaker_failure_threshold:
>     if not self.health():
>         self._blocked_until = time.monotonic() + self.breaker_cooldown
>         raise SourceUnavailable(...)
> ```
>
> `/health` is exempt from this source's slowness and failures, so it
> answers "is it back?" in one fast call. Asking it first means I don't
> spend the whole retry budget on a source that's still down. Measured:
> 2.05s instead of 7.12s, so 3.5x cheaper — and that saving repeats once
> per cooldown for as long as the outage lasts.

> **Q: Does it actually work?**
> Measured against a register I killed mid-run: the request before the
> breaker opens takes 4.17 seconds, because it sits through all its
> retries. The one after takes 0.000 seconds and never touches the network.

### `health()`

Deliberately skips both the retrying and the breaker.

> **Q: Why?**
> The mock service's own README says `/health` is exempt from the slowness
> and the failures. So it stays a truthful up/down signal even while
> `/records` is failing — which is exactly when you most want to ask.

### `list_all()` and `get_by_ref()`

`list_all()` fetches everything in one call (this source has no pagination,
so no duplicate problem) and caches it for 20 seconds. `get_by_ref()`
fetches one record. Both go through `_get`, which is why the breaker only
needs to exist in one place.

---

## `app/assembly.py` — the brain

No HTTP in this file. It asks the adapters for data and decides what the
answer looks like. Two things live here: **matching** and **degradation**.

### Matching: `_normalize_name` and the match key

The two systems share no id. The only thing linking them is that they
describe the same people.

```python
def _normalize_name(s):
    return re.sub(r'[^A-Z]', '', (s or '').upper())
```

Uppercase, then delete everything that isn't a letter. `"O'Brien"` and
`"OBrien"` both become `"OBRIEN"`.

The match key is a tuple: `(last name, first name, date of birth)`, all
normalised. The register writes names as `"DELGADO, Daniel"`, so
`_xml_name_parts` splits on the comma first.

### `find_benefits_match()` — four possible answers

| Answer | When | What's returned |
|---|---|---|
| `not_attempted` | The resident has no date of birth | Nothing to match on |
| `no_match` | Nothing shares that key | `null` benefits |
| `ambiguous` | More than one record shares that key | `null` — declines to guess |
| `matched` | Exactly one, on both sides | The record + `confidence: 0.99` |

> **Q: Why decline instead of picking the best guess?**
> The problem statement says being wrong quietly is much worse than
> declining to merge. If two people share a name and a date of birth, any
> choice I make is a coin flip that looks like a fact to whoever reads it.
> A staff member seeing "no match" goes and checks. A staff member seeing
> the wrong person's benefits doesn't.

> **Q: What's the `0.99` confidence based on?**
> It's a measured number, not a guess. `scripts/match_accuracy_check.py`
> runs my matching code against the raw data files, which contain a hidden
> `_pid` field that says which records are genuinely the same person. The
> live services strip that field, so my API never sees it. Result: of 340
> true pairs, it finds 306 and gets 0 wrong. 100% precision, 90% recall. I
> say 0.99 rather than 1.0 because no false positives in 306 cases is
> strong evidence of a good rule, not proof of a perfect one.

> **Q: Why does it miss 34?**
> Those 34 register records have a blank `Born` field — a real data quality
> gap in the legacy source. I could match them on name alone and recover
> some, but that's exactly where false positives come from.

### `_ambiguity_reason()` — checking both sides

The obvious ambiguity is two register records sharing a key. The subtle one
is two *residents* sharing a key — then a single register record can't be
attributed to either of them. Both are checked.

### `build_unified_resident()` — the two doors

```python
# Door 1: try it as a resident id
resident = rest_client.get_by_id(identifier)
if resident is not None:
    ...

# Door 2: try it as a register ref
benefit = benefits_client.get_by_ref(identifier)
if benefit is not None:
    ...

# Neither: not found
```

> **Q: Why accept two different kinds of identifier?**
> The problem is called "No Wrong Door." If a staff member is holding a
> benefits reference, telling them to go find a resident id first is
> exactly the hunting-through-systems the whole thing is meant to remove.
> Also, 200 people exist only in the register — if the only door were a
> resident id, they'd be invisible.

The response says which door was used, in `found_by`.

### Degradation — the important part

Every response has two blocks, `resident_source` and `benefits_source`,
each built by `_status()`:

```python
{'status': 'unavailable', 'error': 'benefits register unreachable: ...'}
```

> **Q: What does the caller get when a source is down?**
> HTTP 200, with whatever I could reach, and a status block saying exactly
> what happened to the other one. If the register is down, they get the
> resident's details plus `benefits_source: unavailable` and a
> plain-language reason. They never get an error page, and they never get a
> bare `null` they might read as "this person has no benefits."

> **Q: Why 200 and not 503?**
> Because I have real data to give them. A staff member who gets a 500 goes
> back to checking systems by hand. A staff member who gets the resident's
> record plus "benefits register is down" has been given something useful
> and told exactly what's missing.

**The two rows worth knowing:**

1. **`count` is `null`, not `0`,** when the index is unreachable on the
   bulk endpoint. `0` would claim there are no residents. The truth is I
   don't know how many there are.
2. **An identifier not found while a source is down** gets an extra
   `warning` saying it can't be confirmed absent — only "not found in the
   sources that answered."

---

## `app/api.py` — the HTTP layer

Plain `http.server`. `do_GET` wraps everything in a try/except so an
unexpected bug returns a clean 500 rather than a broken connection, then
`_route` matches the path:

```python
if u.path == '/health':          ...
if u.path.startswith('/unified/residents/'):  ...
if u.path == '/unified/residents':            ...
```

One detail worth knowing: the single-resident route takes **everything**
after the prefix, not just the next path segment, because a register ref
(`NO/2019/4697`) contains slashes and is a legitimate identifier.

Matching is on by default; `?match=off` turns it off.

> **Q: Why is matching on by default when the problem calls it a stretch goal?**
> The problem asks for "one call, one resident, everything known about
> them." A flag the caller has to know about doesn't deliver that. And
> turning it on is safe: the worst case is `no_match` or `ambiguous`, never
> a wrong merge — so there's nothing to protect the floor from.

---

## `app/cli.py` — the same thing without HTTP

Builds the same two clients, calls the same `build_unified_resident`, prints
the JSON. It exists so the solution can be demonstrated without running a
server — the problem statement says a command-line demonstration is fine.

---

## `tests/test_solution.py` — 31 tests

> **Q: What's the testing approach?**
> No mocking of the sources. The tests start real copies of both mock
> services on different ports — including a third one configured to fail
> 100% of the time — and run the real code against them. The whole point of
> this problem is behaviour when a source genuinely fails, and a mocked
> failure only proves my mock works.

Where the real services can't help — malformed XML, a source that never
stops paginating, a source gone dark for a controlled length of time — small
stub servers stand in.

The caching and breaker tests **count upstream requests** rather than
measuring time. That's the only way to prove the cache (or the breaker)
actually prevented a call, rather than just that nothing crashed.

---

## Likely hard questions

**"Walk me through what happens when I request a resident and the register
is down."**

`api.py` routes to `build_unified_resident`. Door 1 calls
`rest_client.get_by_id` — that works, so I have the resident. Then I ask
the register for all its records. `_get` tries 3 times with backoff, fails
every time, increments the failure count, and raises `SourceUnavailable`.
`_benefits_index_or_error` catches it and returns the error message. I set
`benefits_source` to `unavailable` with that message and return. The caller
gets HTTP 200, the resident's full details, and a clear statement that
benefits data couldn't be retrieved. If two more requests do the same, the
breaker opens and the fourth one fails instantly instead of waiting.

**"What happens on the second request, and the third?"**

Second: same as the first, but the resident data comes from cache. Third:
the failure count hits 3, so the breaker opens. Fourth through however many
arrive in the next 15 seconds: instant `unavailable`, no network call. After
15 seconds, the next one checks `/health` — if that's still failing, it
blocks for another 15 without touching `/records`.

But there's a better version of this answer, if the register was working
recently: the caller doesn't get `unavailable` at all. They get the
matched benefits record from the last successful fetch, with
`stale_seconds` saying how old it is. I demonstrated this by killing the
register mid-run — the response came back `"status": "matched"` with the
real benefit code and `"stale_seconds": 8.0`. They only get `unavailable`
once that copy passes two minutes old, or if we never got one.

**"Where would you change things if a third source appeared?"**

A new file in `app/adapters/`, and a few lines in `assembly.py` to fetch it
and add its status block. The two existing adapters don't change — they
don't know about each other, and `assembly.py` is the only file that knows
there's more than one source.

**"What would you do differently with more time?"**

Persist the cache to a file — right now a restart during an outage leaves
the last-known-data fallback with nothing to fall back on. Handle name
variations (hyphens, middle names) behind the same decline rule, so it
never matches on name alone. And read the two sources in parallel — I built
that and measured it saving 2.2 seconds, then removed it, because nothing
in the problem grades latency and threads were the hardest thing here to
defend.

**"Why is `stale_seconds` sometimes missing from the response?"**

Because it's only there when it means something. A fresh answer doesn't
carry it at all, so its presence always means "this came from a copy, and
here's how old." If I set it to `null` or `0` on every response, callers
would stop reading it.

**"Is anything in here you couldn't have written yourself?"**

The honest answer is in `AI-USAGE.md`: Claude was used throughout, and it's
disclosed. What I can defend is every decision — why a dictionary instead
of a list, why 200 instead of 503, why declining a match beats guessing,
and why several working features were deliberately removed.
