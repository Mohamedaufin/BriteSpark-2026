# AI Usage

I used Claude Code and ChatGPT during this project.

## What I did

- Chose the problem after reading the requirements and determining that a zero-dependency architecture was the strongest approach for the "clean clone" constraint.
- Made the final design decisions after reviewing AI suggestions, specifically rejecting third-party frameworks (like FastAPI) and choosing deterministic exact-matching (Name + DOB) over risky fuzzy matching.
- Defined the explicit degradation policy mapping failures to 200 OK responses with partial data.
- Personally tested the core system against the live services by killing ports to verify the circuit breaker and cache fallback mechanics.
- Reviewed every file and asked for explanations until I understood how the retry, caching and circuit breaker logic worked, including why the breaker is two variables rather than a formal state machine.

## Where ChatGPT helped

- Used ChatGPT to understand the problem statement, clarify the strict requirements of the hackathon rubric, and review the initial architectural approach.
- Suggested circuit breaking and TTL caching as approaches for handling the Day 2 permanent 40% failure rate. I decided how both were actually built.

## Where Claude Code helped

- Helped translate my requirements and design decisions into the pure Python Standard Library implementation, including the `http.server` routes, the two source adapters, and the assembly layer that owns matching and degradation.
- Implemented the identity resolution logic based on the exact-match behaviour I specified.
- Ran the automated tests and the degradation replication script (`test_degradation_policy.py`) to produce the live latency figures quoted in `DECISIONS.md`, which I reviewed and confirmed.
- Audited the finished repository against the problem statement and found real mismatches between what the documents claimed and what the code did — a response field named wrongly, a missing floor-item section, and measured figures that did not reproduce. All were corrected against fresh measurements.
- Helped draft the README and this file, which I reviewed and edited.

## Ownership

I am responsible for every line in the repository and can explain how it works, including how it handles the Day 2 failure scenarios.
