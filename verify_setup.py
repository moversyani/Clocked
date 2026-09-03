"""
Credential diagnostic.

The five DVSA values fail in different ways and a single "it didn't work" tells
you nothing about which one is wrong. This checks them in stages and stops at
the first failure, so the error points at one credential rather than five.

    python verify_setup.py
    python verify_setup.py --reg AB12CDE

Secrets are masked in all output, so this is safe to run with someone
watching and safe to paste the output of.
"""

from __future__ import annotations

import argparse
import sys

import requests

from clocked import config
from clocked.detect import analyse
from clocked.mot_client import MotApiError, MotClient, VehicleNotFound
from clocked.normalise import normalise

PASS = "PASS"
FAIL = "FAIL"


def line(status: str, message: str) -> None:
    print(f"  [{status}] {message}")


def stage_one_presence() -> bool:
    print("\n1. Credentials present")

    absent = config.missing()

    if absent:
        for name in absent:
            line(FAIL, f"{name} is missing or blank")
        print("\n  Copy .env.example to .env and fill in all five values.")
        return False

    secrets = ("DVSA_CLIENT_ID", "DVSA_CLIENT_SECRET", "DVSA_API_KEY")

    for name in config.REQUIRED:
        value = config.get(name)
        # URLs are not secrets, and showing them in full is the whole point —
        # a swapped token and scope URL is only visible if you can read them.
        shown = config.mask(value) if name in secrets else value
        line(PASS, f"{name} = {shown}")

    return True


def stage_two_urls() -> bool:
    """Catch the most common paste error before spending a network call on it."""
    print("\n2. URLs look well formed")

    ok = True

    for name in ("DVSA_TOKEN_URL", "DVSA_SCOPE_URL"):
        value = config.get(name)

        if not value.startswith("https://"):
            line(FAIL, f"{name} does not start with https://")
            ok = False
        else:
            line(PASS, f"{name} starts with https://")

    token_url = config.get("DVSA_TOKEN_URL")

    if "token" not in token_url.lower():
        line(FAIL, "DVSA_TOKEN_URL does not contain 'token' — check you have not swapped it with the scope URL")
        ok = False
    else:
        line(PASS, "DVSA_TOKEN_URL looks like a token endpoint")

    return ok


def stage_three_token() -> str | None:
    """Proves the client ID, client secret and scope URL are all correct."""
    print("\n3. Access token")

    client = MotClient()

    try:
        token = client._fetch_token()
    except requests.RequestException as error:
        line(FAIL, f"Could not reach the token endpoint: {error}")
        return None
    except MotApiError as error:
        line(FAIL, str(error))
        print("\n  A failure here means the client ID, client secret or scope URL")
        print("  is wrong. The API key is not used at this stage.")
        return None

    line(PASS, f"Token received: {config.mask(token, 8)}")
    line(PASS, f"Valid for roughly {int(client._token_expires_at - __import__('time').time())} seconds")

    return token


def stage_four_lookup(registration: str) -> bool:
    """Proves the API key is correct — the token alone is not enough."""
    print(f"\n4. Live vehicle lookup ({registration.upper()})")

    try:
        payload = MotClient().get_vehicle(registration)
    except VehicleNotFound:
        line(FAIL, "DVSA has no MOT history for that registration")
        print("\n  Try a vehicle over three years old. Newer cars have no MOT record yet.")
        return False
    except MotApiError as error:
        line(FAIL, str(error))
        print("\n  If the token stage passed but this failed, the API key is the")
        print("  likely problem — it is sent separately from the token.")
        return False

    tests = payload.get("motTests") or []
    line(PASS, f"{payload.get('make', '?')} {payload.get('model', '?')}, {len(tests)} MOT test(s)")

    readings, skipped = normalise(payload)
    report = analyse(readings, skipped)

    line(PASS, f"{len(readings)} usable reading(s), {len(skipped)} skipped")
    line(PASS, f"Verdict: {report.verdict.value.replace('_', ' ').upper()}")

    return True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check the DVSA setup end to end.")
    parser.add_argument("--reg", help="A real registration to test the full pipeline against")
    args = parser.parse_args(argv)

    print("Clocked — setup check")

    if not stage_one_presence():
        return 1

    if not stage_two_urls():
        return 1

    if stage_three_token() is None:
        return 1

    if args.reg and not stage_four_lookup(args.reg):
        return 1

    print("\nAll checks passed.")

    if not args.reg:
        print("Run again with --reg to test a real lookup:")
        print("  python verify_setup.py --reg AB12CDE")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
