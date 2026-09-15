import pytest

from renewaltracker import create_app
from renewaltracker.config import TestConfig
from renewaltracker.models import db


@pytest.fixture()
def app():
    app = create_app(TestConfig)
    with app.app_context():
        db.drop_all()
        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()


@pytest.fixture()
def client(app):
    return app.test_client()


@pytest.fixture()
def auth_client(client):
    """A test client that is already logged in."""
    res = client.post("/api/auth/register", json={"username": "alice", "password": "correct-horse", "email": "alice@example.com"})
    assert res.status_code == 201, res.get_json()
    return client
