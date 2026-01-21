import os

import httpx
import pytest


@pytest.mark.integration
def test_health_check(client: httpx.Client, root_url: str) -> None:
    if os.getenv("RUN_INTEGRATION") != "1":
        pytest.skip("Set RUN_INTEGRATION=1 to run integration tests.")

    resp = client.get(f"{root_url}/health")
    assert resp.status_code == 200
    payload = resp.json()
    assert payload.get("status") == "healthy"


@pytest.mark.integration
def test_login_logout(client: httpx.Client, base_url: str) -> None:
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
    assert payload["code"] == 0
    token = payload["data"]["token"]

    resp = client.post(
        f"{base_url}/auth/logout",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["code"] == 0


@pytest.mark.integration
def test_binding_status(
    client: httpx.Client, base_url: str, auth_headers: dict
) -> None:
    resp = client.get(f"{base_url}/binding/status", headers=auth_headers)
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["code"] == 0
    assert isinstance(payload["data"]["is_bound"], bool)


@pytest.mark.integration
def test_study_record_create(
    client: httpx.Client, base_url: str, auth_headers: dict
) -> None:
    resp = client.post(
        f"{base_url}/study/record",
        headers=auth_headers,
        json={"action": "homework", "duration": 5, "abstract": "unit test"},
    )
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["code"] == 0
    assert isinstance(payload["data"]["record_id"], int)


@pytest.mark.integration
def test_upload_token(
    client: httpx.Client, base_url: str, auth_headers: dict
) -> None:
    resp = client.post(
        f"{base_url}/upload/token",
        headers=auth_headers,
        json={"file_type": "image", "file_ext": "jpg"},
    )
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["code"] == 0
    data = payload["data"]
    assert data["upload_url"]
    assert data["file_key"]
    assert data["file_url"]


@pytest.mark.integration
def test_solving_submit(
    client: httpx.Client, base_url: str, auth_headers: dict
) -> None:
    if os.getenv("RUN_ZHIPU_TESTS") != "1":
        pytest.skip("Set RUN_ZHIPU_TESTS=1 to run Zhipu tests.")

    image_url = os.getenv("TEST_IMAGE_URL")
    if not image_url:
        pytest.skip("Set TEST_IMAGE_URL to run Zhipu solving tests.")

    resp = client.post(
        f"{base_url}/solving/submit",
        headers=auth_headers,
        json={"image_url": image_url},
    )
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["code"] == 0
    assert isinstance(payload["data"]["question_history_id"], int)


@pytest.mark.integration
def test_correction_submit(
    client: httpx.Client, base_url: str, auth_headers: dict
) -> None:
    if os.getenv("RUN_ZHIPU_TESTS") != "1":
        pytest.skip("Set RUN_ZHIPU_TESTS=1 to run Zhipu tests.")

    image_url = os.getenv("TEST_IMAGE_URL")
    if not image_url:
        pytest.skip("Set TEST_IMAGE_URL to run Zhipu correction tests.")

    resp = client.post(
        f"{base_url}/correction/submit",
        headers=auth_headers,
        json={"image_url": image_url},
    )
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["code"] == 0
    assert isinstance(payload["data"]["correction_id"], int)
    assert isinstance(payload["data"]["results"], list)


@pytest.mark.integration
def test_conversation_create_end(
    client: httpx.Client, base_url: str, auth_headers: dict
) -> None:
    if os.getenv("RUN_CONVERSATION_TESTS") != "1":
        pytest.skip("Set RUN_CONVERSATION_TESTS=1 to run conversation tests.")

    image_url = os.getenv("TEST_IMAGE_URL")
    if not image_url:
        pytest.skip("Set TEST_IMAGE_URL to run conversation tests.")

    solving = client.post(
        f"{base_url}/solving/submit",
        headers=auth_headers,
        json={"image_url": image_url},
    )
    assert solving.status_code == 200
    solving_data = solving.json()["data"]
    question_history_id = solving_data["question_history_id"]

    resp = client.post(
        f"{base_url}/conversation/create",
        headers=auth_headers,
        json={"type": "solving", "question_history_id": question_history_id},
    )
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["code"] == 0
    conversation_id = payload["data"]["conversation_id"]

    resp = client.post(
        f"{base_url}/conversation/end",
        headers=auth_headers,
        json={"conversation_id": conversation_id},
    )
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["code"] == 0
