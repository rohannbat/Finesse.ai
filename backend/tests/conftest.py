import os
import tempfile

# Must be set before app.config / app.database are imported.
_db_path = os.path.join(tempfile.mkdtemp(prefix="healthsync-test-"), "test.db")
os.environ.setdefault("DATABASE_URL", f"sqlite:///{_db_path}")
os.environ.setdefault("JWT_SECRET", "test-secret-that-is-long-enough-32b")
if "TOKEN_ENCRYPTION_KEY" not in os.environ:
    from cryptography.fernet import Fernet

    os.environ["TOKEN_ENCRYPTION_KEY"] = Fernet.generate_key().decode()

import pytest
from fastapi.testclient import TestClient

from main import app


@pytest.fixture(scope="session")
def client():
    with TestClient(app) as c:
        yield c
