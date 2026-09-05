# Clocked

A UK vehicle mileage integrity checker. Enter a registration number, and Clocked pulls the vehicle's full MOT history from the DVSA government API and flags evidence of odometer tampering.

Mileage clocking, winding an odometer back to make a car look less used than it is, is a live fraud in the UK second-hand market. Every MOT test records a mileage reading, and those readings are public. A car that has been clocked leaves the evidence in its own government record. Clocked reads it.

**Status:** in development. Detection engine and API client complete and tested. Web interface and live API integration in progress.

---

## What it detects

| Check | What it means |
| --- | --- |
| `ODOMETER_ROLLBACK` | A run of readings below an earlier peak, reported as one event. Carries the peak, the lowest reading, how many tests it spans, and how long it lasted. An odometer cannot go backwards. |
| `MILEAGE_SHORTFALL` | The vehicle's own pre-rollback mileage rate, projected forward, predicts more than the latest reading shows. A rollback's effect can outlast the event itself. |
| `IMPLAUSIBLE_JUMP` | Mileage added at a rate above 60,000 miles a year. Possible, but often means an earlier reading was understated. |
| `IMPLAUSIBLE_SLOWDOWN` | Mileage added at a rate below 500 miles a year, over a period shorter than a year. Fills the gap `STATIC_MILEAGE` leaves for shorter windows, most useful for the period since the last MOT. |
| `STATIC_MILEAGE` | A year or more on the record with almost no mileage added. Could be genuine storage, could be a disconnected odometer. |
| `WEAR_MILEAGE_MISMATCH` | Wear-related advisories and failures, accumulated across the MOT record, against a claimed mileage low enough that the wear looks disproportionate. Weak evidence on its own: wear varies enormously with age, salt exposure, driving style and maintenance. Never more than a warning. |
| `TESTING_GAP` | An interval between two tests much longer than a year. The record has nothing to say about that period, and mileage altered during a gap leaves no trace. Reported as unexplained, not suspicious, unless it overlaps the COVID-19 MOT exemption, in which case it is noted as likely explained by that. |
| `MIXED_UNITS` | The history mixes miles and kilometres. Flagged so the user knows a conversion was applied. |
| `MINOR_DISCREPANCY` | A drop under 100 miles. Reported, but not treated as fraud, because it is almost always a typing error at the test station. |
| `INCOMPLETE_HISTORY` | Tests that could not be used. Gaps reduce how much the verdict can be relied on. |

Every check above also runs against an optional current dashboard reading, if one is supplied. Any finding it contributes to says plainly that it rests on a figure the user entered rather than one DVSA verified.

Findings roll up into one of four verdicts: `CLEAR`, `REVIEW`, `EVIDENCE_OF_TAMPERING`, or `INSUFFICIENT_DATA`.

## Design decisions worth explaining

**Rollbacks are measured against the running maximum, not the previous reading.** If a car is wound back once and then driven normally for three years, every consecutive pair afterwards shows a sensible increase. Pairwise comparison finds nothing. Comparing each reading against the highest reading ever recorded catches it, and keeps catching it for the rest of the vehicle's life.

**A rollback is one event, not one finding per reading.** Consecutive readings sitting below the same peak are grouped into a single `ODOMETER_ROLLBACK`, because a single act of tampering would otherwise produce several near identical criticals, one for every test until the mileage recovers. A drop persisting across several tests is stronger evidence than a single dip, because a typo does not persist, so the finding records how many tests were affected and how long the depression lasted.

**A rollback's effect can outlast the rollback.** Even after the odometer recovers and climbs normally again, it can take years to catch up to where the vehicle's own pre-rollback trend said it should be, if it ever does. `MILEAGE_SHORTFALL` projects the baseline rate from before the first rollback forward and compares it against the latest reading, so the evidence does not expire once the mileage starts climbing again.

**A failed test and its retest are one inspection.** Where a failed reading is followed within a short window by another reading close in mileage, the earlier one is collapsed into the later, so the same real world test is not counted twice.

**A long gap between tests is reported as unexplained, not suspicious.** Mileage altered while a vehicle went untested leaves no trace, but a legitimate SORN period, a classic or low-use vehicle kept off the road, and an ordinary late test are all indistinguishable from each other in this data. The one cause this check can identify is the COVID-19 MOT exemption, so a gap overlapping that window is called out as likely explained by it rather than left as a generic warning.

**A current dashboard reading closes the one gap the MOT record cannot see.** Mileage altered between the last MOT and today leaves no trace anywhere in DVSA's data, so an optional current reading, supplied with the date it was taken, is fed through the same rollback, jump and slowdown checks as any MOT reading. It is flagged as user-reported rather than MOT-verified everywhere it appears, every finding it contributes to says so in its own wording, and it is never allowed to redefine the baseline that `MILEAGE_SHORTFALL` projects from, since an unverified figure should not be able to both create and erase the evidence against it.

**Wear signals are weak evidence, deliberately treated as such.** `WEAR_MILEAGE_MISMATCH` compares wear-related advisories and failures accumulated across a vehicle's MOT history against its claimed mileage. Wear varies enormously with age, salt exposure, driving style and maintenance, so this never rises above a warning and the wording says so directly. The keyword categories that drive it live in a plain dictionary rather than a chain of conditionals, since defect wording is not standardised and the list needs to grow.

**No external mileage averages are used.** A van doing 40,000 miles a year is not suspicious. Every check is a claim about the vehicle's own internal consistency, which is the only thing the data can actually support.

**Small drops are not called fraud.** Below 100 miles, a discrepancy is reported as information rather than an accusation. Test stations mistype.

**Bad data is skipped, never guessed.** Missing readings, unrecognised units and unparseable dates are excluded from analysis and surfaced separately. Inventing a value to fill a gap would produce confident, wrong answers.

**Unit conversion happens before comparison.** A history mixing miles and kilometres produces false rollbacks if compared raw. One fixture in this repo exists purely to prove that case is handled.

## Architecture

```
clocked/
  normalise.py    Raw DVSA JSON  ->  clean, sorted, miles-only readings
  wear.py         Advisory and failure text -> a categorised wear profile
  detect.py       Readings       ->  findings and a verdict
  mot_client.py   OAuth 2.0, token caching, rate-limit backoff
  cli.py          Runs against a fixture or a live registration, optionally
                  with a current dashboard reading
fixtures/         Sample histories: clean, tampered, messy, gapped, and worn
tests/            60 tests, no network or credentials required
```

Normalisation and detection are deliberately separate from the API client. The engine takes plain data structures, so it is fully testable offline and would work unchanged against a different data source.

## Running it

```bash
pip install -r requirements.txt
python -m unittest discover -s tests
python -m clocked.cli --fixture fixtures/rollback_history.json
```

Example output:

```
XY68ZZT (fixtures/rollback_history.json)
========================================
Verdict: EVIDENCE OF TAMPERING

Mileage history
  11 Mar 2018      41,200 mi
  09 Mar 2019      72,840 mi
  14 Mar 2020     104,510 mi
  02 Apr 2021      58,300 mi
  08 Apr 2022      71,950 mi
  15 Apr 2023      84,600 mi

Findings
  [X] ODOMETER_ROLLBACK: Mileage fell from a peak of 104,510 on 14 Mar 2020 to a low of
      58,300 on 02 Apr 2021. The reading stayed below that peak across 3 test(s), a
      depression lasting 1127 days. An odometer cannot decrease.
  [!] MILEAGE_SHORTFALL: Based on this vehicle's mileage rate before the earlier
      rollback, the reading on 15 Apr 2023 is 117,118 miles short of what that rate
      would predict. The gap has not been made up since the odometer was wound back.
```

Add today's dashboard reading to close the gap since the last MOT too:

```bash
python -m clocked.cli --fixture fixtures/clean_history.json --current-mileage 70500 --current-date 2023-10-01
```

## Live API access

The DVSA MOT History API is free under the Open Government Licence and open to individuals. Registration returns five credentials: a client ID, client secret, API key, token URL and scope URL. Copy `.env.example` to `.env` and fill them in.

Authentication is OAuth 2.0 client credentials. The client exchanges the ID and secret for an access token, caches it until shortly before it expires, and sends it alongside the API key on each request. A 401 triggers one token refresh and retry; a 429 backs off using the `Retry-After` header.

```bash
python -m clocked.cli --reg AB12CDE
```

## Roadmap

- [x] Normalisation layer with unit conversion and bad-data handling
- [x] Detection engine with four verdict states
- [x] Test suite running offline against fixtures
- [x] DVSA client with token caching and backoff
- [x] Verify the client against live credentials
- [ ] Django web interface
- [ ] Mileage timeline chart with anomalies highlighted
- [ ] Local response cache to stay inside rate limits

## Data source and licence

MOT history data is provided by the Driver and Vehicle Standards Agency under the Open Government Licence v3.0.


## Disclaimer

Clocked reports what the public MOT record shows. A finding is evidence worth investigating further, not proof of fraud, and a clear result does not mean a vehicle is untampered, only that the recorded history is internally consistent.

Mileage altered between MOT tests, on vehicles under three years old, or before import will not appear in this data at all. Treat the output as one input to a decision, not the decision itself.
