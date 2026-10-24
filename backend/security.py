import hashlib
import hmac
import secrets


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    derived = hashlib.scrypt(password.encode(), salt=salt, n=16384, r=8, p=5, dklen=64)
    return "scrypt$16384$8$5$" + salt.hex() + "$" + derived.hex()


def verify_password(password: str, encoded: str) -> bool:
    try:
        algo, n, r, p, salt, expected = encoded.split("$")
        if algo != "scrypt" or (n, r, p) != ("16384", "8", "5"):
            return False
        if len(salt) != 32 or len(expected) != 128:
            return False
        actual = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=int(n), r=int(r), p=int(p), dklen=64)
        return hmac.compare_digest(actual, bytes.fromhex(expected))
    except (ValueError, TypeError):
        return False


def digest(secret: str, value: str) -> str:
    return hmac.new(secret.encode(), value.encode(), hashlib.sha256).hexdigest()


def csrf_token(secret: str, token: str) -> str:
    return digest(secret, "csrf:" + token)
