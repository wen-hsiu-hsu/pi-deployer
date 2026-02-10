"""Signature and token verification for pi-deployer."""

import hashlib
import hmac


def verify_signature(payload_body, signature_header, secret):
    """Verify GitHub webhook HMAC-SHA256 signature.

    Args:
        payload_body: Raw request body bytes.
        signature_header: Value of X-Hub-Signature-256 header (sha256=...).
        secret: The webhook secret string.

    Returns:
        True if signature is valid, False otherwise.
    """
    if not signature_header or not secret:
        return False

    if not signature_header.startswith("sha256="):
        return False

    expected = hmac.new(
        secret.encode("utf-8"),
        payload_body,
        hashlib.sha256,
    ).hexdigest()

    received = signature_header[7:]  # strip "sha256="
    return hmac.compare_digest(expected, received)


def verify_bearer_token(auth_header, token):
    """Verify Bearer token from Authorization header.

    Args:
        auth_header: Value of Authorization header (Bearer ...).
        token: Expected token string.

    Returns:
        True if token matches, False otherwise.
    """
    if not auth_header or not token:
        return False

    if not auth_header.startswith("Bearer "):
        return False

    received = auth_header[7:]  # strip "Bearer "
    return hmac.compare_digest(received, token)
