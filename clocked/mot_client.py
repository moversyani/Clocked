"""
Client for the DVSA MOT History API.

Two separate things have to be true before DVSA will answer a request:

  1. You hold a valid access token, obtained by exchanging your client ID and
     client secret at the token endpoint. Tokens expire, so they are cached
     and refreshed rather than requested on every call.
  2. You send your API key on every request. The token proves who you are;
     the key identifies which registered application is asking.

Written against the documented flow. Untested against the live service until
the DVSA credentials arrive — see README for what to verify first.
"""

from __future__ import annotations

import time

import requests

from . import config

VEHICLE_URL = "https://history.mot.api.gov.uk/v1/trade/vehicles/registration/{registration}"

# Refresh slightly before true expiry so a token never dies mid-request.
EXPIRY_MARGIN_SECONDS = 60


class MotApiError(RuntimeError):
    """Raised when DVSA responds with something we cannot use."""


class VehicleNotFound(MotApiError):
    """No MOT history exists for this registration."""


class MotClient:
    def __init__(
        self,
        client_id: str | None = None,
        client_secret: str | None = None,
        api_key: str | None = None,
        token_url: str | None = None,
        scope_url: str | None = None,
        session: requests.Session | None = None,
    ):
        self.client_id = client_id or config.get("DVSA_CLIENT_ID")
        self.client_secret = client_secret or config.get("DVSA_CLIENT_SECRET")
        self.api_key = api_key or config.get("DVSA_API_KEY")
        self.token_url = token_url or config.get("DVSA_TOKEN_URL")
        self.scope_url = scope_url or config.get("DVSA_SCOPE_URL")

        self.session = session or requests.Session()
        self._token: str | None = None
        self._token_expires_at: float = 0.0

    def _token_is_valid(self) -> bool:
        return bool(self._token) and time.time() < self._token_expires_at

    def _fetch_token(self) -> str:
        response = self.session.post(
            self.token_url,
            data={
                "grant_type": "client_credentials",
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "scope": self.scope_url,
            },
            timeout=15,
        )

        if response.status_code != 200:
            raise MotApiError(
                f"Token request failed ({response.status_code}). "
                f"Check the client ID, secret and scope URL."
            )

        payload = response.json()
        self._token = payload["access_token"]
        self._token_expires_at = (
            time.time() + int(payload.get("expires_in", 3600)) - EXPIRY_MARGIN_SECONDS
        )

        return self._token

    def _access_token(self) -> str:
        if self._token_is_valid():
            return self._token
        return self._fetch_token()

    def get_vehicle(self, registration: str, max_retries: int = 3) -> dict:
        """
        Fetch one vehicle's full MOT history.

        Retries on rate limiting and on a single 401, which usually means the
        cached token expired earlier than advertised.
        """
        registration = registration.replace(" ", "").upper()
        url = VEHICLE_URL.format(registration=registration)

        for attempt in range(max_retries):
            response = self.session.get(
                url,
                headers={
                    "Authorization": f"Bearer {self._access_token()}",
                    "X-API-Key": self.api_key,
                    "Accept": "application/json",
                },
                timeout=20,
            )

            if response.status_code == 200:
                return response.json()

            if response.status_code == 404:
                raise VehicleNotFound(f"No MOT history found for {registration}.")

            if response.status_code == 401:
                self._token = None
                continue

            if response.status_code == 429:
                wait = int(response.headers.get("Retry-After", 2**attempt))
                time.sleep(wait)
                continue

            raise MotApiError(
                f"DVSA returned {response.status_code} for {registration}."
            )

        raise MotApiError(f"Gave up after {max_retries} attempts for {registration}.")
