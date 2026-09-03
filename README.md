# Clocked

A UK vehicle mileage integrity checker. Enter a registration number, and Clocked pulls the vehicle's full MOT history from the DVSA government API and flags evidence of odometer tampering.

Mileage clocking — winding an odometer back to make a car look less used than it is — is a live fraud in the UK second-hand market. Every MOT test records a mileage reading, and those readings are public. A car that has been clocked leaves the evidence in its own government record. Clocked reads it.

**Status:** in development. Detection engine and API client complete and tested. Web interface and live API integration in progress.

---

## What it detects

| Check | What it means |
| --- | --- |
| `ODOMETER_ROLLBACK` | A reading lower than an earlier peak. An odometer cannot go backwards. |
| `IMPLAUSIBLE_JUMP` | Mileage added at a rate above 60,000 miles a year — possible, but often means an earlier reading was understated. |
| `STATIC_MILEAGE` | A year or more on the record with almost no mileage added. Could be genuine storage, could be a disconnected odometer. |
| `MIXED_UNITS` | The history mixes miles and kilometres. Flagged so the user knows a conversion was applied. |
| `MINOR_DISCREPANCY` | A drop under 100 miles. Reported, but not treated as fraud — it is almost always a typing error at the test station. |
| `INCOMPLETE_HISTORY` | Tests that could not be used. Gaps reduce how much the verdict can be relied on. |

Findings roll up into one of four verdicts: `CLEAR`, `REVIEW`, `EVIDENCE_OF_TAMPERING`, or `INSUFFICIENT_DATA`.

## Design decisions worth explaining

**Rollbacks are measured against the running maximum, not the previous reading.** If a car is wound back once and then driven normally for three years, every consecutive pair afterwards shows a sensible increase. Pairwise comparison finds nothing. Comparing each reading against the highest reading ever recorded catches it, and keeps catching it for the rest of the vehicle's life.

**No external mileage averages are used.** A van doing 40,000 miles a year is not suspicious. Every check is a claim about the vehicle's own internal consistency, which is the only thing the data can actually support.

**Small drops are not called fraud.** Below 100 miles, a discrepancy is reported as information rather than an accusation. Test stations mistype.

**Bad data is skipped, never guessed.** Missing readings, unrecognised units and unparseable dates are excluded from analysis and surfaced separately. Inventing a value to fill a gap would produce confident, wrong answers.

**Unit conversion happens before comparison.** A history mixing miles and kilometres produces false rollbacks if compared raw. One fixture in this repo exists purely to prove that case is handled.

## Architecture

```
clocked/
  normalise.py    Raw DVSA JSON  ->  clean, sorted, miles-only readings
  detect.py       Readings       ->  findings and a verdict
  mot_client.py   OAuth 2.0, token caching, rate-limit backoff
  cli.py          Runs against a fixture or a live registration
fixtures/         Sample histories: clean, tampered, and messy
tests/            13 tests, no network or credentials required
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
XY68ZZT
Verdict: EVIDENCE OF TAMPERING

  11 Mar 2018      41,200 mi
  09 Mar 2019      72,840 mi
  14 Mar 2020     104,510 mi
  02 Apr 2021      58,300 mi
  08 Apr 2022      71,950 mi
  15 Apr 2023      84,600 mi

  [X] ODOMETER_ROLLBACK: Mileage fell by 46,210 miles — from 104,510 on
      14 Mar 2020 to 58,300 on 02 Apr 2021. An odometer cannot decrease.
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
- [ ] Verify the client against live credentials
- [ ] Django web interface
- [ ] Mileage timeline chart with anomalies highlighted
- [ ] Local response cache to stay inside rate limits

## Data source and licence

MOT history data is provided by the Driver and Vehicle Standards Agency under the Open Government Licence v3.0.

Clocked is released under the MIT Licence. See `LICENSE`.

## Disclaimer

Clocked reports what the public MOT record shows. A finding is evidence worth investigating further, not proof of fraud, and a clear result does not mean a vehicle is untampered — only that the recorded history is internally consistent.

Mileage altered between MOT tests, on vehicles under three years old, or before import will not appear in this data at all. Treat the output as one input to a decision, not the decision itself.
