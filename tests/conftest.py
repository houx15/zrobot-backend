import os
from typing import Generator

import httpx
import pytest


@pytest.fixture(scope="session")
def base_url() -> str:
    return os.getenv("BASE_URL", "http://localhost:8000/api/v1/student")


@pytest.fixture(scope="session")
def root_url(base_url: str) -> str:
    marker = "/api/v1/student"
    if marker in base_url:
        return base_url.split(marker)[0]
    return base_url.rsplit("/", 1)[0]


@pytest.fixture(scope="session")
def client() -> Generator[httpx.Client, None, None]:
    with httpx.Client(timeout=60.0) as session:
        yield session


@pytest.fixture(scope="function")
def auth_token(client: httpx.Client, base_url: str) -> str:
    if os.getenv("RUN_INTEGRATION") != "1":
        pytest.skip("Set RUN_INTEGRATION=1 to run integration tests.")

    phone = os.getenv("TEST_STUDENT_PHONE", "13800138000")
    password = os.getenv("TEST_STUDENT_PASSWORD", "123456")
    device_id = os.getenv("TEST_DEVICE_ID", "test_device")

    resp = client.post(
        f"{base_url}/auth/login",
        json={"phone": phone, "password": password, "device_id": device_id},
    )
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["code"] == 0, payload
    return payload["data"]["token"]


@pytest.fixture(scope="function")
def auth_headers(auth_token: str) -> dict:
    return {"Authorization": f"Bearer {auth_token}"}
