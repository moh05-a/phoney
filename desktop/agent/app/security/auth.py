"""Pairing codes, device tokens and source-address checks."""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
import secrets
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database.models import Device, PairingCode, utcnow

TOKEN_PREFIX = "pbx_"
# No 0/O, 1/I/L: the code is read off a monitor and typed on a phone.
CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
CODE_LENGTH = 8


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def normalize_code(raw: str) -> str:
    return "".join(ch for ch in raw.upper() if ch.isalnum())


def format_code(code: str) -> str:
    return f"{code[:4]}-{code[4:]}"


def create_pairing_code(db: Session, ttl_s: int) -> str:
    code = "".join(secrets.choice(CODE_ALPHABET) for _ in range(CODE_LENGTH))
    db.add(PairingCode(code=code, expires_at=utcnow() + timedelta(seconds=ttl_s)))
    db.commit()
    return code


def redeem_pairing_code(db: Session, raw_code: str) -> bool:
    """Single use: a code is consumed the moment it is accepted."""
    row = db.get(PairingCode, normalize_code(raw_code))
    if row is None or row.used or row.expires_at.replace(tzinfo=utcnow().tzinfo) < utcnow():
        return False
    row.used = True
    db.commit()
    return True


def create_device(db: Session, name: str, model: str) -> tuple[Device, str]:
    """Returns the device and its token. The token is not stored and cannot be shown again."""
    token = TOKEN_PREFIX + secrets.token_urlsafe(32)
    device = Device(id="d_" + secrets.token_hex(8), name=name, model=model, token_hash=hash_token(token))
    db.add(device)
    db.commit()
    return device, token


def authenticate(db: Session, token: str | None) -> Device | None:
    if not token or not token.startswith(TOKEN_PREFIX) or len(token) > 100:
        return None
    digest = hash_token(token)
    device = db.scalar(select(Device).where(Device.token_hash == digest))
    if device is None or device.revoked or not hmac.compare_digest(device.token_hash, digest):
        return None
    return device


def bearer_token(header_value: str | None) -> str | None:
    if not header_value:
        return None
    scheme, _, value = header_value.partition(" ")
    return value.strip() if scheme.lower() == "bearer" and value else None


def is_private_address(host: str | None) -> bool:
    """True for loopback, RFC 1918, link-local and unique-local addresses."""
    if not host:
        return False
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    return ip.is_loopback or ip.is_private or ip.is_link_local
