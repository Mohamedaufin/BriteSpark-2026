# Problem 3 — No Wrong Door

## Unified Resident API

This is my submission for **Brite Spark 2026 – Problem 3: No Wrong Door**.

I built a resilient, zero-dependency integration API that assembles a unified view of a resident from two disconnected legacy systems: a paginated REST resident index and a slow, unreliable XML benefits register.

The solution absorbs the instability of the source systems, deduplicates paginated records, and bridges the two databases using a deterministic cross-source identity matching engine.

---

## 📋 Solution Overview

### What My Solution Does

1. ✅ Connects to both the REST Resident Index and the XML Benefits Register — sequentially, not concurrently (see *Architecture* below).
2. ✅ Deduplicates "sliding" records across REST pagination boundaries.
3. ✅ Defends against the XML Register's permanent 40% failure rate using a retry + circuit-breaking adapter.
4. ✅ Eliminates most of the XML Register's ~1.5s latency using a TTL cache.
5. ✅ Fails gracefully by serving a `stale_seconds`-labelled fallback if the upstream crashes after cache expiry.
6. ✅ Merges records across systems using deterministic Name + DOB matching.
7. ✅ Explicitly refuses to merge ambiguous records — checked from **both** sides — to prevent silent data corruption.
8. ✅ Returns a comprehensive, single-payload JSON profile with granular source status.
9. ✅ Allows caseworkers to query using **either** a REST ID or an XML Reference (True "No Wrong Door").
10. ✅ Runs entirely on the Python Standard Library with zero external dependencies.
11. ✅ Fully idempotent — the same request made any number of times returns the same shape and never duplicates or mutates data.

---

## 🖥️ Zero-Dependency Architecture

This solution is designed to be instantly deployable on any grading machine without package management overhead or proxy issues.

### Architecture Features

- **No Virtual Environments:** No `pip install` required — no `requirements.txt` or `pyproject.toml` exists in this repo.
- **Pure Standard Library:** Uses `http.server`, `urllib.request`, and `xml.etree`.
- **Sequential Fetching:** The resident index is queried, then the benefits register, one after the other within a single request — **not** concurrent. This is a real limitation, not a feature; it's listed under *Known Limitations* below rather than dressed up. A cache-cold matched lookup pays both latencies added together.
- **Modular Adapters:** Source-specific fetching logic is strictly isolated from the assembly layer, which is what let the Day 2 failure-rate change (below) be absorbed without touching `assembly.py` or `api.py`.

---

## 🔎 Cross-Source Identity Resolution

The API is designed not only to fetch data, but to link disconnected systems without guessing.

For each resident, the API evaluates:

- Normalized First and Last Name
- Date of Birth
- Uniqueness on **both** sides — a shared key on the register side declines the merge, and so does a shared key on the resident side, since a single register row can't be safely attached to either of two matching residents.

### Matching Outcomes

| Status | Description |
|--------|-------------|
| **MATCHED** | An exact, unambiguous match was found. A confidence score of `0.99` is attached, along with the basis for it. |
| **NO_MATCH** | The identifier exists in one system, but no record in the other shares its name and DOB. |
| **NOT_FOUND** | The identifier doesn't exist in a source at all — distinct from `NO_MATCH`, which means a match was attempted and failed to link, not that nothing was there to begin with. |
| **AMBIGUOUS** | Multiple people share the same Name and DOB, on either side. The system safely declines to merge them. |

Because the API supports bi-directional lookups, the ~200 people who exist *only* in the legacy XML register are still perfectly accessible.

---

## 🛡️ Key Resilience Features

- **Hard Guardrails:** The API is structurally incapable of turning an upstream timeout into a 5xx crash. Every failure maps to a named status field in a normal `200 OK` response.
- **Retry with Backoff:** The XML adapter retries up to 3 times with a 0.3s base delay before declaring a source unavailable.
- **TTL Cache (20 seconds):** The XML register's `list_all` call is cached for 20 seconds. Measured live over three cold/warm cycles: a cold call costs **2.6–6.5s** (it pays the register's 0.7–2.4s delay plus a retry cycle whenever it draws one of the 40% failures); the call right after costs **0.031–0.049s**. That ~30–50ms floor is the always-live, uncached resident-index lookup that still runs on every request. The staleness explicitly accepted in exchange is up to 20 seconds.
- **Stale-on-Error Fallback (up to 120 seconds):** If the cache expires and the fresh fetch fails, the adapter serves the last known good snapshot — provided it is no older than 120 seconds — with the exact `stale_seconds` age attached so the caller is never misled. Past 120 seconds, it reports `unavailable` normally.
- **Circuit Breaker:** After 3 consecutive fully-failed *requests* (not attempts), the breaker blocks the source for 15 seconds. Measured live against the register killed outright: the first three requests took **7.6s, 7.1s, 7.2s** (full retry budget each); the next was **0.034s** — rejected on a timestamp, touching the network zero times. The first request *after* the cooldown expires probes the cheap `/health` endpoint instead of retrying blindly: **2.075s**, about **3.5x cheaper** than the ~7.2s cycle it replaces. Full mechanism, plus the awkward case where a source's `/health` lies, is in [`DECISIONS.md`](DECISIONS.md).
- **Pagination Safety:** A `MAX_PAGES = 10,000` hard limit prevents infinite loops if the REST server permanently drops its `has_more` flag. A walk that fails partway through returns the residents already collected, with a `partial_detail` string on `resident_source` explaining what's missing — rather than discarding pages that were fetched successfully.

---

## 📁 Project Structure

```text
app/
├── adapters/
│   ├── resident_index.py      # REST client: pagination + de-dup + cache + partial-walk fallback
│   └── benefits_register.py   # XML client: retry + circuit breaker + cache + stale-fallback
├── assembly.py                # Identity matching & aggregation logic
├── api.py                     # HTTP server & routing
├── cli.py                     # Command-line interface demo
├── config.py                  # Env-var defaults & resilience tuning
└── errors.py                  # Standardized exceptions

tests/
└── test_solution.py           # Integration test suite (32 tests)

scripts/
├── match_accuracy_check.py       # Ground-truth accuracy validation
└── test_degradation_policy.py    # Runnable replication of the degradation table above

data pack/                     # Provided mock services
start.sh / start.bat           # Launch all three processes and run a health check
```

---

## 💻 Technology

### Backend
- Python 3.9+
- Python Standard Library only (`urllib`, `http.server`)

---

## 🚀 Running the Solution

> **Windows users: use Command Prompt (`cmd`), not PowerShell.** Open it by pressing `Win + R`, typing `cmd`, and pressing Enter. All Windows commands in this README are written for `cmd`.

### Quick Start (Recommended)

A launcher script is included that starts all 3 services automatically and prints a health check. Both scripts below were actually run end-to-end while writing this section — the output quoted after Step 6 is real, not illustrative.

**Windows** — double-click `start.bat`, or from a terminal:
```bat
start.bat
```

**macOS / Linux** — from a terminal:
```bash
chmod +x start.sh
bash start.sh
```

The script checks for Python, verifies all files are present, opens each service in its own window/background process, waits for boot, runs a health check, and prints the test URLs.

---

### Manual Setup (Step-by-Step)

If the launcher does not work on your system, follow the steps below.

### Prerequisites

- Python 3.9 or later
- Git
- No `pip install`, no virtual environment, no external packages

---

### Step 0: Install Python (Skip if already installed)

**Check if Python is already installed:**

```bash
python --version          # Windows / macOS / Linux
python3 --version         # macOS / Linux alternative
```

If this prints `Python 3.9.x` or higher, skip this step entirely.

If you see `command not found` or a version below 3.9, install it:

---

**Windows:**

1. Go to [https://www.python.org/downloads/](https://www.python.org/downloads/)
2. Click **"Download Python 3.x.x"** (the big yellow button).
3. Run the installer.
4. **Critical:** On the first screen of the installer, tick the checkbox that says **"Add Python to PATH"** before clicking Install.
5. Once installed, close and reopen your terminal, then run `python --version` to confirm.

> **Rollback:** If `python --version` still fails after installation, try `py --version` instead. The Python Windows Launcher (`py`) is installed alongside Python and works as a direct substitute — use `py` everywhere in place of `python` below.

---

**macOS:**

```bash
brew install python3
```

If you don't have Homebrew: [https://brew.sh](https://brew.sh)

Alternatively, download directly from [https://www.python.org/downloads/](https://www.python.org/downloads/) and run the `.pkg` installer.

---

**Linux (Ubuntu / Debian):**

```bash
sudo apt update
sudo apt install python3
```

**Linux (Fedora / RHEL):**

```bash
sudo dnf install python3
```

---

> **Note:** On macOS and Linux the command may be `python3` instead of `python`. Use whichever one prints a version above 3.9.

---

### Step 1: Clone the Repository

```bash
git clone https://github.com/Mohamedaufin/BriteSpark.git
cd BriteSpark
```

All commands from this point run from inside the `BriteSpark` folder.

---

### Step 2: Verify the Folder Structure

```bash
# Windows
python -c "import os, sys; files=['app/api.py','app/assembly.py','data pack/services/rest_service.py','data pack/services/xml_service.py']; missing=[f for f in files if not os.path.exists(f)]; [print('MISSING:', f) for f in missing]; sys.exit(1) if missing else print('All files OK.')"

# macOS / Linux
python3 -c "import os, sys; files=['app/api.py','app/assembly.py','data pack/services/rest_service.py','data pack/services/xml_service.py']; missing=[f for f in files if not os.path.exists(f)]; [print('MISSING:', f) for f in missing]; sys.exit(1) if missing else print('All files OK.')"
```

Expected output — all lines should say `OK`:
```
All files OK.
```

> **If it says `MISSING`:** The clone did not complete correctly. Delete the folder and re-clone.

---

### Step 3: Start the REST Mock Service

Open **Terminal 1** and run from the repo root:

```bash
# Windows
python "data pack/services/rest_service.py" --port 8081

# macOS / Linux
python3 "data pack/services/rest_service.py" --port 8081
```

**Expected output:**
```
Resident Index (REST) on http://127.0.0.1:8081
  620 records across 27 pages of 25
```

Leave this terminal running. Do not close it.

> **If this fails with "Address already in use":** Port 8081 is taken. Use `--port 8085` instead, then pass `REST_BASE_URL=http://127.0.0.1:8085` when starting the API in Step 5.

---

### Step 4: Start the XML Mock Service

Open **Terminal 2** and run from the repo root:

```bash
# Windows
python "data pack/services/xml_service.py" --port 8082 --failure-rate 0.40

# macOS / Linux
python3 "data pack/services/xml_service.py" --port 8082 --failure-rate 0.40
```

**Expected output:**
```
Benefits Register (XML) on http://127.0.0.1:8082
  540 records | failure rate 40% | delay 0.7-2.4s
```

Leave this terminal running. Do not close it.

> **The `--failure-rate 0.40` flag reflects the Day 2 state** (see DECISIONS.md). Without it, the service defaults to 15%.

> **If this fails with "Address already in use":** Use `--port 8086` instead.

---

### Step 5: Start the Unified API

Open **Terminal 3** and run from the repo root:

```bash
# Windows
python -m app.api --port 8090

# macOS / Linux
python3 -m app.api --port 8090
```

**Expected output:**
```
Unified Resident View on http://127.0.0.1:8090
  resident index    -> http://127.0.0.1:8081
  benefits register -> http://127.0.0.1:8082
```

Leave this terminal running. All three terminals must stay open simultaneously.

> **If you used different ports in Steps 3 or 4**, pass them as environment variables:
>
> **Windows (Command Prompt / cmd):**
> ```cmd
> set REST_BASE_URL=http://127.0.0.1:8085 && set XML_BASE_URL=http://127.0.0.1:8086 && python -m app.api --port 8090
> ```
> **macOS / Linux:**
> ```bash
> REST_BASE_URL=http://127.0.0.1:8085 XML_BASE_URL=http://127.0.0.1:8086 python3 -m app.api --port 8090
> ```

---

### Step 6: Verify Everything is Running

```bash
curl http://127.0.0.1:8090/health
```

**Real output** (this is what the endpoint actually returns — not a nested `sources` object):
```json
{
  "status": "ok",
  "service": "unified-resident-view",
  "resident_index": "up",
  "benefits_register": "up"
}
```

Both `resident_index` and `benefits_register` must say `"up"`. If one says `"down"`, the corresponding mock service did not start correctly — go back and fix that step first.

---

## 🖥️ Using the API

| Endpoint | What it does |
|---|---|
| `GET /health` | This service's health, plus up/down for each upstream source |
| `GET /unified/residents/<identifier>` | Unified view of one resident, by **either** identifier |
| `GET /unified/residents/<identifier>?match=off` | Same, without attempting the cross-source join |
| `GET /unified/residents` | Unified view of every resident (de-duplicated) |
| `GET /unified/residents?match=off` | Same, without matching |

### Test Queries

To see the "No Wrong Door" identity matching in action, look up the same person using either door:

```bash
# Door 1: Look up by REST ID
curl http://127.0.0.1:8090/unified/residents/R-10697

# Door 2: Look up by XML Reference
curl http://127.0.0.1:8090/unified/residents/NO/2019/4697

# A person who exists ONLY in the register (200 of these in the data pack)
curl http://127.0.0.1:8090/unified/residents/AS/2024/4702
```

---

## 🧪 Testing Graceful Degradation (Manual Verification)

The API is designed to handle each source completely independently. Evaluators can manually test this:

1. **Simulate REST Failure:**
   - Go to Terminal 1 and press `Ctrl+C` to kill the REST service.
   - Send a request to `http://127.0.0.1:8090/unified/residents`.
   - The API still returns `200 OK`. `resident_source.status` becomes `unavailable`; `benefits_source` is unaffected.

2. **Simulate XML Failure & Circuit Breaker:**
   - Restart Terminal 1, then kill Terminal 2 (XML service) with `Ctrl+C`.
   - Send requests to the API.
   - REST data is returned throughout. The first three calls take the full ~7s retry cycle each (falling back to a stale cached copy if one is still valid, with `stale_seconds` attached). After that the breaker trips and calls return in **~0.03s**, rejected on a timestamp without touching the network. Wait 15 seconds for the cooldown to lapse and the next call takes **~2s** — that's the `/health` probe checking whether the source came back, instead of paying the full retry cycle to find out.

---

## 🎯 Problem 3 Alignment & Day 2 Challenge

This solution addresses **Brite Spark 2026 – Problem 3: No Wrong Door** by combining automated identity resolution with true bi-directional querying.

### Degradation Policy

For every way a source can fail, the caller always gets a `200 OK` with a named status — never a bare 5xx, never silent missing data.

| Failure Mode | `resident_source` | `benefits_source` | HTTP Status |
|---|---|---|---|
| Both sources healthy | `ok` | `ok` / `matched` | 200 |
| XML returns 500 (within retry budget) | `ok` | its real status (retry succeeded) | 200 |
| XML fails all 3 retries, no usable cache | `ok` | `unavailable` + reason | 200 |
| XML is down, cache is fresh (< 20s) | `ok` | its real status (cached) | 200 |
| XML is down, cache is stale (20–120s) | `ok` | its real status + `stale_seconds` | 200 |
| XML is down, cache is expired (> 120s) | `ok` | `unavailable` | 200 |
| Circuit breaker blocking calls | `ok` | `unavailable`, or a stale answer if one's still valid | 200 |
| REST is down — queried by **register ref** | `unavailable` + reason | `ok` (real data still served) | 200 |
| REST is down — queried by **resident id** | `unavailable` + reason | `not_found` + a `warning` that absence can't be confirmed | 200 |
| REST is down — **bulk listing** | `unavailable` + reason | `not_attempted` | 200 |
| Both sources down | `unavailable` | `unavailable` | 200 |
| Identifier not found in either source | `not_found` | `not_found` | 200 |
| Match found but ambiguous (either side) | `ok` | `ambiguous` + reason | 200 |
| Malformed JSON / XML from upstream | `unavailable` + parse error | `unavailable` + parse error | 200 |

### Day 2 Surprise Challenge – Permanent 40% Failure Rate

The XML source was permanently degraded to a 40% failure rate on Day 2 (up from 15%). Because the adapter is completely isolated from the assembly logic, **the solution needed no changes at all** — not one value. `XML_MAX_RETRIES` has been `3` since it was first added; the cache TTL, breaker threshold and cooldown are all untouched. The existing defaults were verified live against the register at 40% (the circuit breaker numbers above were measured against exactly this configuration) and simply held.

What *did* change was the **test suite** — its register fixture now runs at 40%, and two fixtures that make repeated register calls had their retry budget raised so the suite itself isn't flaky at that failure rate. That asymmetry (solution untouched, tests retuned) is the honest version of what happened, and `DECISIONS.md` covers it in full along with what I'd have done differently knowing this was coming.

| Scenario | System Response |
|----------|----------------|
| **Both Sources Succeed** | Returns unified profile; `ok` and `matched` statuses. |
| **XML Source Fails** | Returns REST data; `benefits_source` explicitly reports `unavailable` (or a labelled stale answer). |
| **REST Source Fails** | Returns XML data; `resident_source` explicitly reports `unavailable`. |
| **Cache Expired + Source Down** | Returns last known data with exact `stale_seconds` warning, if still within 120s; otherwise `unavailable`. |
| **Circuit Breaker Blocking** | Rejected on a timestamp in ~0.03s without touching the network; after the cooldown lapses, a ~2s `/health` probe replaces the ~7s retry cycle. REST data returned normally throughout. |

---

## 🚫 Out of Scope

To keep the prototype focused strictly on the core hackathon integration logic, the following were intentionally excluded:
- **Web UI:** As per the prompt ("Interface quality is not assessed"), this is purely a backend API.
- **Database Storage:** The system operates as a real-time gateway and holds data purely in transient memory.
- **Authentication:** Left open for frictionless evaluator access.

## ⚠️ Known Limitations

Stated here rather than left for someone else to find:
- **Source fetches are sequential, not concurrent** — a cache-cold matched lookup pays both sources' latency added together.
- **`get_by_id` / `get_by_ref` aren't cached** — only the bulk `list_all` paths are.
- Full reasoning for both, and what I'd fix first, is in `DECISIONS.md`.

---

## 📚 The Rest of the Documentation

- **[`DECISIONS.md`](DECISIONS.md)** — the required design record: the full degradation policy row by row, the identity-matching reasoning and measured accuracy, retry-safety and idempotency, the Day 2 response, what was cut, and what I'd fix first.
- **[`WALKTHROUGH.md`](WALKTHROUGH.md)** — a plain-language tour of every file in the order the code actually runs, written so any part of it can be talked through out loud.
- **[`AI-USAGE.md`](AI-USAGE.md)** — the required AI usage disclosure: what was used, for what, what was verified by hand, and a documentation regression that was caught and corrected.

---

## 📝 License

This project is submitted as part of the Brite Spark 2026 hackathon competition.
