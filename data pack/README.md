# Problem 3 — No Wrong Door
## Data pack

### Contents

| File | What it is |
|:--|:--|
| `services/rest_service.py` | The Resident Index. A paginated JSON service. |
| `services/xml_service.py` | The Benefits Register. A legacy XML service. |
| `services/run_both.sh` | Starts both. |
| `services/_rest_data.json`, `services/_xml_data.json` | The data the services serve. You may read these, but your solution must go through the services. |

Python 3 standard library only. Nothing to install.

### Running them

```bash
./services/run_both.sh
```

Or separately:

```bash
python3 services/rest_service.py --port 8081
python3 services/xml_service.py  --port 8082
```

### Source 1 — Resident Index (REST, port 8081)

```
GET /residents?page=1&page_size=25     paginated list
GET /residents/<id>                    single record
GET /health
```

Returns JSON. Records look like:

```json
{ "id": "R-10234", "first_name": "Maria", "last_name": "Delgado",
  "date_of_birth": "1971-04-02", "address_line": "118 Cedar Ave",
  "city": "Northgate", "phone": "555-402-9911",
  "program_status": "Active", "last_contact": "2025-11-30" }
```

The index is ordered by `last_contact`, which other processes update while you are paging.

### Source 2 — Benefits Register (XML, port 8082)

```
GET /records                           all records
GET /records/<ref>                     single record
GET /health                            (fast, does not fail)
```

Returns XML. Records look like:

```xml
<Record>
  <Ref>NO/2019/4234</Ref>
  <Name>DELGADO, Maria</Name>
  <Born>1971-04-02</Born>
  <Addr>118 Cedar Avenue</Addr>
  <Town>Northgate</Town>
  <BenefitCode>HSP-B</BenefitCode>
  <ReviewDue>2026-05-14</ReviewDue>
</Record>
```

**This service is slow and it fails.** Expect roughly 1.5 seconds per call and a `500` on a fraction of requests. Neither is a fault, neither is going to be fixed, and neither is a reason to wait for someone to look at it. Build for it.

`/health` is deliberately exempt from both behaviours, so you have one reliable way to tell "the service is up" from "the service failed this call".

> **If XML parsing fails strangely.** `xml.etree.ElementTree` can import successfully and then raise *"No module named expat"* on every parse, if your Python was built without a working `pyexpat`. That is a broken interpreter, not a broken service. Try a different Python before you lose an hour to it — and mention it at a check-in.

### The two sources do not share a key

`R-10234` and `NO/2019/4234` are unrelated identifiers assigned by different systems at different times. Nothing in either record tells you they describe the same person.

Note also that the two sources hold overlapping but different populations. Some people appear in both, some in only one. There is no field telling you which.

**Identity matching across the two sources is a stretch goal, not part of the floor.** It is genuinely hard and it is easy to lose a day in. Read the floor in your problem document before deciding to attempt it.

### Reminder

A change to the requirements lands on day two. You will not be told what it is.
