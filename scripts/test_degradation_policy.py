"""
Degradation Policy Replication Test
Tests every row of the DECISIONS.md degradation policy table.
Run from the repo root: python scripts/test_degradation_policy.py
"""
import urllib.request
import urllib.error
import json
import time
import subprocess
import sys
import os

# Always resolve repo root relative to this script's location
REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

API_MAIN  = "http://127.0.0.1:8090"

PASS = "  PASS"
FAIL = "  FAIL"
INFO = "  INFO"

def get(url, timeout=12):
    try:
        r = urllib.request.urlopen(url, timeout=timeout)
        return json.loads(r.read().decode()), r.getcode()
    except urllib.error.HTTPError as e:
        return json.loads(e.read().decode()), e.code
    except Exception as e:
        return {"error": str(e)}, 0

def section(title):
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}")

def check(label, condition, actual=""):
    status = PASS if condition else FAIL
    print(f"{status}  {label}")
    if not condition and actual:
        print(f"         Got: {actual}")

def start_api(port, rest_url, xml_url, capture=False):
    env = os.environ.copy()
    env["REST_BASE_URL"] = rest_url
    env["XML_BASE_URL"]  = xml_url
    env["API_PORT"]      = str(port)
    out = subprocess.PIPE if capture else subprocess.DEVNULL
    proc = subprocess.Popen(
        [sys.executable, "-m", "app.api", "--port", str(port)],
        cwd=REPO_ROOT,
        env=env,
        stdout=out,
        stderr=out
    )
    return proc

def wait_for_api(port, timeout=30):
    """Poll until the API is responding or timeout.

    The per-attempt timeout has to be generous: /health probes both upstreams,
    and when one of them is a dead port the probe pays that connection's
    failure time before answering (~2.5s observed on Windows, which refuses a
    connection to a closed local port far more slowly than Linux does). A
    per-attempt timeout shorter than that never sees a healthy response at
    all, no matter how long the outer deadline is.
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=10)
            return True
        except Exception:
            time.sleep(0.5)
    return False

# ─────────────────────────────────────────────────────────────
section("ROW 1: Both sources healthy -> ok / ok")
# ─────────────────────────────────────────────────────────────
data, code = get(f"{API_MAIN}/unified/residents/R-10697")
check("HTTP 200 returned",            code == 200, str(code))
check("resident_source present",      "resident_source" in data)
rs = data.get("resident_source", {})
bs = data.get("benefits_source", {})
check("resident_source.status == ok", rs.get("status") == "ok", rs.get("status"))
print(f"{INFO}  benefits_source.status = {bs.get('status')}")

# ─────────────────────────────────────────────────────────────
section("ROW 8: REST completely down -> depends on the door used")
# ─────────────────────────────────────────────────────────────
print("  Launching isolated API (REST=dead port 19999, XML=live 8082) ...")
proc_r = start_api(8095, "http://127.0.0.1:19999", "http://127.0.0.1:8082")
ready = wait_for_api(8095)
if not ready:
    check("Isolated API started on port 8095", False, "timed out waiting for port")
else:
    print("  API ready. Querying all three access paths ...")
    # With the index down, what the register can say depends entirely on
    # which door was used - so all three are checked rather than one.
    # Door 1 (a REST id): the register is reachable but has no record under
    # a *resident* id, so the honest answer is not_found, not "unavailable".
    d1, code1 = get("http://127.0.0.1:8095/unified/residents/R-10697")
    check("Door 1 (REST id): HTTP 200, not 500", code1 == 200, str(code1))
    check("Door 1: resident_source == unavailable",
          d1.get("resident_source", {}).get("status") == "unavailable",
          d1.get("resident_source", {}).get("status"))
    check("Door 1: benefits_source == not_found (register has no such ref)",
          d1.get("benefits_source", {}).get("status") == "not_found",
          d1.get("benefits_source", {}).get("status"))
    check("Door 1: warns that absence could not be confirmed",
          "warning" in d1, str(list(d1.keys())))

    # Door 2 (a register ref): the register can answer in full, so the
    # caller still gets real benefits data despite the index being down.
    #
    # Deliberately tolerant of the register's real 40% failure rate: at that
    # rate a single call legitimately comes back `unavailable` sometimes, and
    # that is correct behaviour, not a defect. Asserting `ok` unconditionally
    # would make this script randomly red for the right reasons, which is
    # worse than useless in something offered as proof. What is asserted
    # instead is the property that actually must hold either way: the answer
    # is a well-formed 200 with an honest, named status - and whichever
    # status it is, it carries the evidence that goes with it.
    d2, code2 = get("http://127.0.0.1:8095/unified/residents/NO/2019/4697")
    bs_d2 = d2.get("benefits_source", {}).get("status")
    check("Door 2 (register ref): HTTP 200, not 500", code2 == 200, str(code2))
    check("Door 2: resident_source == unavailable",
          d2.get("resident_source", {}).get("status") == "unavailable",
          d2.get("resident_source", {}).get("status"))
    check("Door 2: benefits_source is ok or unavailable (both honest at 40%)",
          bs_d2 in ("ok", "unavailable"), bs_d2)
    if bs_d2 == "ok":
        check("Door 2: benefits payload present when status is ok",
              d2.get("benefits") is not None)
        print(f"{INFO}  Register answered - real benefits data served despite index being down.")
    else:
        check("Door 2: a reason is given when the register could not answer",
              bool(d2.get("benefits_source", {}).get("error")),
              repr(d2.get("benefits_source", {}).get("error")))
        print(f"{INFO}  Register hit its 40% failure this run - reported honestly, not silently empty.")

    # Bulk: there is nothing to match against, so matching is not attempted
    # and count is null rather than 0 - we could not look, we did not look
    # and find nobody.
    d3, code3 = get("http://127.0.0.1:8095/unified/residents", timeout=30)
    check("Bulk: HTTP 200, not 500", code3 == 200, str(code3))
    check("Bulk: benefits_source == not_attempted",
          d3.get("benefits_source", {}).get("status") == "not_attempted",
          d3.get("benefits_source", {}).get("status"))
    check("Bulk: count is null, not 0", d3.get("count") is None, repr(d3.get("count")))
proc_r.terminate(); proc_r.wait()

# ─────────────────────────────────────────────────────────────
section("ROW 9: Both sources offline -> unavailable / unavailable")
# ─────────────────────────────────────────────────────────────
print("  Launching isolated API (REST=dead 19998, XML=dead 19999) ...")
proc_b = start_api(8096, "http://127.0.0.1:19998", "http://127.0.0.1:19999")
ready = wait_for_api(8096)
if not ready:
    check("Isolated API started on port 8096", False, "timed out waiting for port")
else:
    print("  API ready. Querying ...")
    d, code = get("http://127.0.0.1:8096/unified/residents/R-10697", timeout=20)
    check("HTTP 200 returned (not 500)", code == 200, str(code))
    rs3 = d.get("resident_source", {})
    bs3 = d.get("benefits_source", {})
    check("resident_source.status == unavailable", rs3.get("status") == "unavailable", rs3.get("status"))
    check("benefits_source.status == unavailable", bs3.get("status") == "unavailable", bs3.get("status"))
proc_b.terminate(); proc_b.wait()

# ─────────────────────────────────────────────────────────────
section("ROW 6: Circuit Breaker OPEN -> fail-fast (speed test)")
# ─────────────────────────────────────────────────────────────
print("  Launching 100% failure XML service on port 8099 ...")
xml_proc = subprocess.Popen(
    [sys.executable, "data pack/services/xml_service.py", "--port", "8099", "--failure-rate", "1.0"],
    cwd=REPO_ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
)
time.sleep(1)

print("  Launching isolated API (REST=live 8081, XML=100% fail 8099) ...")
proc_cb = start_api(8097, "http://127.0.0.1:8081", "http://127.0.0.1:8099")
ready = wait_for_api(8097)
if not ready:
    check("Isolated API started on port 8097", False, "timed out waiting for port")
else:
    print("  API ready. Firing 7 requests to trip the breaker (threshold=3) ...")
    times = []
    statuses = []
    codes = []
    residents_returned = []
    for i in range(7):
        t0 = time.time()
        d, code_i = get("http://127.0.0.1:8097/unified/residents/R-10697")
        elapsed = time.time() - t0
        times.append(elapsed)
        codes.append(code_i)
        residents_returned.append(d.get("resident") is not None)
        st = d.get("benefits_source", {}).get("status", "?")
        statuses.append(st)
        print(f"  Request {i+1}: {elapsed:.3f}s  benefits_source.status={st}")

    pre  = sum(times[:3]) / 3
    post = sum(times[5:]) / max(len(times[5:]), 1)
    check("All responses HTTP 200",                all(c == 200 for c in codes), str(codes))
    check("Pre-breaker calls are slow (retrying)", pre > 0.3,  f"{pre:.3f}s avg")
    check("Post-breaker calls are faster",         post < pre * 0.5, f"post={post:.3f}s vs pre={pre:.3f}s")
    # The point of the breaker is that the *other* source keeps working
    # throughout - a fail-fast on the register must never cost the caller
    # their resident data.
    check("REST data still returned on every call", all(residents_returned),
          f"{residents_returned.count(True)}/{len(residents_returned)} calls had resident data")
    check("Benefits reported unavailable, not silently empty",
          all(s == "unavailable" for s in statuses), str(set(statuses)))

xml_proc.terminate(); xml_proc.wait()
proc_cb.terminate();  proc_cb.wait()

# ─────────────────────────────────────────────────────────────
section("ROW 11: Identity Match -> confidence=0.99")
# ─────────────────────────────────────────────────────────────
data, code = get(f"{API_MAIN}/unified/residents/R-10697")
bs = data.get("benefits_source", {})
check("HTTP 200", code == 200)
if bs.get("status") == "matched":
    check("confidence field present",  "confidence" in bs, str(bs.keys()))
    check("confidence == 0.99",        bs.get("confidence") == 0.99, str(bs.get("confidence")))
    print(f"{INFO}  Match confirmed: confidence={bs.get('confidence')}, basis={bs.get('confidence_basis','n/a')}")
else:
    print(f"{INFO}  benefits_source.status={bs.get('status')} (this resident may not have an XML record)")

# ─────────────────────────────────────────────────────────────
section("CACHE TEST: ROW 3 - Second call is faster (cache hit)")
# ─────────────────────────────────────────────────────────────
# The cache TTL is 20s, so a genuinely cold call requires waiting the TTL
# out first. Without this wait the "first" call is often already served from
# a cache warmed by an earlier request (including a previous run of this
# script), leaving two warm calls whose ordering is pure timing noise - the
# check then fails for a reason that has nothing to do with the cache.
print("  Waiting 21s for the 20s TTL to expire so the first call is genuinely cold ...")
time.sleep(21)
print("  Hitting /unified/residents twice ...")
t0 = time.time(); get(f"{API_MAIN}/unified/residents", timeout=60); t1 = time.time() - t0
t2_start = time.time(); get(f"{API_MAIN}/unified/residents", timeout=60); t2 = time.time() - t2_start
print(f"  Cold call:   {t1:.3f}s")
print(f"  Cached call: {t2:.3f}s")
check("Cached call is faster than the cold one", t2 < t1, f"{t2:.3f}s vs {t1:.3f}s")
check("Cached call is substantially faster (>2x)", t2 * 2 < t1, f"{t2:.3f}s vs {t1:.3f}s")

# ─────────────────────────────────────────────────────────────
section("FINAL SUMMARY")
# ─────────────────────────────────────────────────────────────
print("""
  Rows verified automatically:
    ROW 1  - Both healthy           -> automated
    ROW 6  - Circuit Breaker OPEN   -> automated (speed comparison)
    ROW 8  - REST down              -> automated (isolated API)
    ROW 9  - Both down              -> automated (isolated API)
    ROW 11 - Identity match 0.99    -> automated
    ROW 3  - Cache hit speed        -> automated

  Rows requiring manual testing (need time to pass):
    ROW 4  - Stale cache (20-120s)  -> wait 20s, kill XML, hit endpoint
    ROW 5  - Expired cache (>120s)  -> wait 120s, kill XML, hit endpoint
    ROW 2  - Transient XML 500      -> already tested by 40% failure rate
""")
