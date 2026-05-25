"""Authentication and request-signing for the Tradernet / Freedom24 API.

Two working schemes are supported:

* **API key (V2 / primary).** Every request is signed with HMAC-SHA256 over a
  sorted, URL-encoded query string built from ``{cmd, params, nonce, apiKey}``
  using the *secret* (private) key. The hex digest is sent in the
  ``X-NtApi-Sig`` header and the same encoded string is the request body. This
  is the recommended path and what the live Freedom24 API expects.

* **Login / password.** Credentials are posted to obtain a session id (``sid``)
  which is then included in subsequent request params. Handled in
  :mod:`client`; this module only provides the shared primitives.

An optional **RSA-SHA256** helper is included for the EDS ("open security
session") flow that some accounts require before authorizing sensitive writes
(orders, withdrawals). It signs a nonce with an RSA private key.

If signed requests are rejected with a signature error, the exact serialization
in :func:`convert_to_query_string` is the first thing to check against your
account's API documentation.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import time
from typing import Any


def generate_nonce() -> int:
    """Return a strictly increasing integer nonce (10000ths of a second)."""
    return int(time.time() * 10000)


def _render_scalar(value: Any) -> str:
    """Render a leaf value the same way for both the signature and the body.

    Booleans become ``true``/``false``; ``None`` becomes an empty string. The
    server only requires that the signed canonical and the parsed body agree, so
    the exact rendering matters less than using it consistently in both places.
    """
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return ""
    return str(value)


def convert_to_query_string(data: dict) -> str:
    """Build the canonical string that is HMAC-signed (the ``X-NtApi-Sig`` input).

    Matches the official Tradernet SDK exactly: keys sorted alphabetically,
    nested dicts serialized *recursively in place* (``params=date_from=...&
    date_to=...``), and **no URL-encoding and no bracket notation**. This is the
    string the server reproduces to verify the signature — it is deliberately
    different from the request body (see :func:`url_form_encoded`).
    """
    parts: list[str] = []
    for key in sorted(data):
        value = data[key]
        if isinstance(value, dict):
            parts.append(f"{key}={convert_to_query_string(value)}")
        else:
            parts.append(f"{key}={_render_scalar(value)}")
    return "&".join(parts)


def url_form_encoded(data: dict, root_name: str = "") -> str:
    """Build the request body: sorted, bracket-notation, **not** URL-encoded.

    ``{"params": {"date_from": "x"}}`` becomes ``params[date_from]=x``. This is
    what gets POSTed; the signature is computed separately over
    :func:`convert_to_query_string`. Mirrors the official SDK's ``url_form_encoded``.
    """
    parts: list[str] = []
    for key in sorted(data):
        value = data[key]
        if isinstance(value, dict):
            parts.append(url_form_encoded(value, key))
        else:
            rendered = _render_scalar(value)
            parts.append(f"{root_name}[{key}]={rendered}" if root_name else f"{key}={rendered}")
    return "&".join(parts)


def sign_hmac_sha256(secret_key: str, query_string: str) -> str:
    """Return the HMAC-SHA256 hex digest of *query_string* keyed by *secret_key*."""
    return hmac.new(
        secret_key.encode("utf-8"),
        query_string.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def build_signed_request(
    pub_key: str, secret_key: str, cmd: str, params: dict | None
) -> tuple[str, dict[str, str]]:
    """Build a V2 API-key signed request.

    Returns ``(body, headers)`` where ``body`` is the URL-encoded payload to POST
    and ``headers`` carries the ``X-NtApi-Sig`` signature.
    """
    payload: dict[str, Any] = {
        "cmd": cmd,
        "nonce": generate_nonce(),
        "apiKey": pub_key,
    }
    # Only include `params` when non-empty: empty-params commands (e.g.
    # getPositionJson) must omit it entirely, or the trailing `params=` / `&`
    # makes the signature and body disagree with the server's reconstruction.
    if params:
        payload["params"] = params

    signature = sign_hmac_sha256(secret_key, convert_to_query_string(payload))
    body = url_form_encoded(payload)
    headers = {
        "X-NtApi-Sig": signature,
        "Content-Type": "application/x-www-form-urlencoded",
    }
    return body, headers


def rsa_sign_nonce(private_key_pem: str | bytes, nonce: str | int) -> str:
    """Sign *nonce* with an RSA private key (RSA-SHA256, PKCS#1 v1.5).

    Returns a base64-encoded signature. Used only for the optional EDS
    "open security session" flow. ``private_key_pem`` may be the PEM contents or
    bytes; the caller is responsible for reading a file path if one is given.
    """
    # Imported lazily so the rest of the server works even if `cryptography`
    # is not needed for the active auth mode.
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding

    key_bytes = (
        private_key_pem.encode("utf-8")
        if isinstance(private_key_pem, str)
        else private_key_pem
    )
    private_key = serialization.load_pem_private_key(key_bytes, password=None)
    signature = private_key.sign(  # type: ignore[union-attr]
        str(nonce).encode("utf-8"),
        padding.PKCS1v15(),
        hashes.SHA256(),
    )
    return base64.b64encode(signature).decode("ascii")
