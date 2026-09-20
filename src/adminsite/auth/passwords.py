import hashlib
import secrets

ALGORITHM = "pbkdf2_sha256"
ITERATIONS = 600_000
SALT_BYTES = 16


def hash_password(password: str, *, iterations: int = ITERATIONS) -> str:
    """Hash a password for storing.

    ```python
    from adminsite.auth import hash_password

    hash_password("letmein")
    ```

    Keep the result, not the password. The format is
    `pbkdf2_sha256$iterations$salt$hash`.
    """
    salt = secrets.token_hex(SALT_BYTES)
    digest = _digest(password, salt, iterations)
    return f"{ALGORITHM}${iterations}${salt}${digest}"


def verify_password(password: str, stored: str) -> bool:
    """Check a password against a stored hash, in constant time."""
    try:
        algorithm, rounds, salt, digest = stored.split("$")
        iterations = int(rounds)
    except ValueError:
        return False
    if algorithm != ALGORITHM:
        return False
    return secrets.compare_digest(digest, _digest(password, salt, iterations))


def looks_hashed(value: str) -> bool:
    """Whether a value is one of our hashes rather than a plain password."""
    return value.startswith(f"{ALGORITHM}$")


def _digest(password: str, salt: str, iterations: int) -> str:
    return hashlib.pbkdf2_hmac(
        "sha256", password.encode(), salt.encode(), iterations
    ).hex()
