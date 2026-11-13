"""Mật khẩu và phiên đăng nhập dashboard; dữ liệu xác thực chỉ lưu phía server."""
import hashlib
import json
import math
import secrets
import time

AUTH_KEY = "dashboard_auth"
SESSION_SEC = 12 * 3600
HASH_ITERATIONS = 600_000
MAX_PASSWORD_LENGTH = 128


def _hash(password, salt, iterations=HASH_ITERATIONS):
    return hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt), iterations).hex()


def _new_record(password):
    salt = secrets.token_hex(16)
    return {"algorithm": "pbkdf2_sha256", "iterations": HASH_ITERATIONS, "salt": salt,
            "digest": _hash(password, salt), "version": secrets.token_urlsafe(24)}


def ensure_auth(store):
    record = store.get(AUTH_KEY)
    if record is not None:
        return record
    candidate = _new_record("123456")
    with store.connection() as db:
        db.execute("INSERT OR IGNORE INTO kv VALUES (?, ?)", (AUTH_KEY, json.dumps(candidate)))
    return store.get(AUTH_KEY)


def _matches(record, password):
    if not isinstance(password, str) or not 1 <= len(password) <= MAX_PASSWORD_LENGTH:
        return False
    return secrets.compare_digest(record["digest"], _hash(password, record["salt"], record["iterations"]))


def login(store, password, client_key="local", now=None):
    now = time.time() if now is None else now
    record = ensure_auth(store)
    key = "login_attempt:" + hashlib.sha256(str(client_key).encode()).hexdigest()
    attempts = store.get(key, {"count": 0, "blocked_until": 0, "last": 0})
    if now < attempts["blocked_until"]:
        raise ValueError(f"Vui lòng thử lại sau {math.ceil(attempts['blocked_until'] - now)} giây.")
    if not _matches(record, password):
        count = attempts["count"] + 1 if now - attempts["last"] < 300 else 1
        store.put(key, {"count": count, "blocked_until": now + 30 if count >= 5 else 0, "last": now})
        raise ValueError("Mật khẩu không đúng.")
    store.put(key, {"count": 0, "blocked_until": 0, "last": now})
    return {"version": record["version"], "expires": now + SESSION_SEC}


def session_valid(store, version, expires, now=None):
    now = time.time() if now is None else now
    if not version or not isinstance(expires, (int, float)) or now >= expires:
        return False
    record = store.get(AUTH_KEY)
    return bool(record and secrets.compare_digest(record["version"], version))


def change_password(store, current, new, confirmation):
    if not isinstance(new, str) or not 6 <= len(new) <= MAX_PASSWORD_LENGTH:
        raise ValueError("Mật khẩu mới cần từ 6 đến 128 ký tự.")
    if new != confirmation:
        raise ValueError("Mật khẩu xác nhận chưa khớp.")
    ensure_auth(store)
    with store.connection() as db:
        db.execute("BEGIN IMMEDIATE")
        record = json.loads(db.execute("SELECT value FROM kv WHERE key=?", (AUTH_KEY,)).fetchone()[0])
        if not _matches(record, current):
            raise ValueError("Mật khẩu hiện tại không đúng.")
        if _matches(record, new):
            raise ValueError("Mật khẩu mới cần khác mật khẩu hiện tại.")
        updated = _new_record(new)
        db.execute("UPDATE kv SET value=? WHERE key=?", (json.dumps(updated), AUTH_KEY))
        # Mật khẩu mới bắt đầu một lượt xác thực mới cho các địa chỉ đã thử sai.
        db.execute("DELETE FROM kv WHERE key LIKE 'login_attempt:%'")
    return {"version": updated["version"], "expires": time.time() + SESSION_SEC}
