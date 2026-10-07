"""Bootstrap telemetry identifies failures without recording credentials."""

import hashlib
import hmac
import logging
import time
from urllib.parse import urlencode

import httpx
import pytest

import miniapp_api
from miniapp_api import create_miniapp_api
from miniapp_sessions import LAUNCH_TTL_SECONDS, create_launch_token
from tests.test_miniapp_auth import TEST_BOT_TOKEN, signed_init_data


CHAT_ID = -9524
USER_ID = 4523274


def _signed_without_user(now):
    fields = {"auth_date": str(now), "query_id": "test"}
    check_string = "\n".join(f"{key}={fields[key]}" for key in sorted(fields))
    secret = hmac.new(b"WebAppData", TEST_BOT_TOKEN.encode(), hashlib.sha256).digest()
    fields["hash"] = hmac.new(secret, check_string.encode(), hashlib.sha256).hexdigest()
    return urlencode(fields)


def _events(caplog):
    return [record.getMessage() for record in caplog.records
            if record.getMessage().startswith("miniapp_bootstrap ")]


@pytest.mark.asyncio
async def test_session_diagnostics_classify_auth_and_launch_failures(temp_database, caplog):
    now = int(time.time())
    valid = signed_init_data(user={"id": USER_ID}, auth_date=now)
    wrong_user = signed_init_data(user={"id": USER_ID + 1}, auth_date=now)
    expired_auth = signed_init_data(user={"id": USER_ID}, auth_date=now - 301)
    missing_user = _signed_without_user(now)
    launch = create_launch_token(CHAT_ID, USER_ID, now=now)
    expired_launch = create_launch_token(CHAT_ID, USER_ID,
                                         now=now - LAUNCH_TTL_SECONDS)
    cases = [
        ({"init_data": "", "launch_token": launch}, 401, "missing_init_data", False),
        ({"init_data": valid, "launch_token": ""}, 401, "missing_launch_token", False),
        ({"init_data": valid + "&bad=%ZZ", "launch_token": launch},
         401, "init_data_invalid", False),
        ({"init_data": expired_auth, "launch_token": launch},
         401, "init_data_expired", False),
        ({"init_data": missing_user, "launch_token": launch},
         401, "init_data_user_missing", False),
        ({"init_data": signed_init_data(user={"id": str(USER_ID)}, auth_date=now),
          "launch_token": launch}, 401, "init_data_user_invalid", False),
        ({"init_data": valid, "launch_token": "not-a-token"},
         401, "launch_token_invalid", True),
        ({"init_data": valid, "launch_token": "X" * 43},
         401, "launch_token_not_found", True),
        ({"init_data": valid, "launch_token": expired_launch},
         401, "launch_token_expired", True),
        ({"init_data": wrong_user, "launch_token": launch},
         401, "launch_token_wrong_user", True),
        ({"init_data": valid, "launch_token": launch},
         200, "session_created", True),
        ({"init_data": valid, "launch_token": launch},
         401, "launch_token_consumed", True),
    ]
    api = create_miniapp_api(bot_token=TEST_BOT_TOKEN, allowed_origin="")
    with caplog.at_level(logging.INFO):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api),
                                     base_url="http://test") as client:
            for payload, status, reason, has_verified_user in cases:
                caplog.clear()
                response = await client.post("/api/v1/session", json=payload)
                assert response.status_code == status
                events = _events(caplog)
                assert "reason=request_received status=pending" in events[0]
                assert f"reason={reason} status={status}" in events[-1]
                expected_user_id = USER_ID + 1 if reason == "launch_token_wrong_user" else USER_ID
                assert (f"user_id={expected_user_id}" in events[-1]) == has_verified_user
                assert (f"chat_id={CHAT_ID}" in events[-1]) == (status == 200)
                joined = "\n".join(events)
                for secret in (valid, wrong_user, expired_auth, missing_user,
                               launch, expired_launch, TEST_BOT_TOKEN,
                               payload["launch_token"]):
                    if secret:
                        assert secret not in joined


@pytest.mark.asyncio
async def test_session_diagnostics_cover_validation_and_internal_error(
    temp_database, caplog, monkeypatch,
):
    now = int(time.time())
    init_data = signed_init_data(user={"id": USER_ID}, auth_date=now)
    launch = create_launch_token(CHAT_ID, USER_ID, now=now)
    api = create_miniapp_api(bot_token=TEST_BOT_TOKEN, allowed_origin="")

    def fail_exchange(*_args, **_kwargs):
        raise RuntimeError(f"secret {launch}")

    with caplog.at_level(logging.INFO):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api),
                                     base_url="http://test") as client:
            missing = await client.post("/api/v1/session", json={"launch_token": launch})
            assert missing.status_code == 422
            assert "reason=missing_init_data status=422" in _events(caplog)[-1]
            caplog.clear()
            missing = await client.post("/api/v1/session", json={"init_data": init_data})
            assert missing.status_code == 422
            assert "reason=missing_launch_token status=422" in _events(caplog)[-1]
            caplog.clear()
            malformed = await client.post("/api/v1/session", json={
                "init_data": init_data, "launch_token": launch, "extra": True,
            })
            assert malformed.status_code == 422
            assert "reason=invalid_session_request status=422" in _events(caplog)[-1]
            caplog.clear()
            monkeypatch.setattr(miniapp_api, "exchange_launch_token", fail_exchange)
            failed = await client.post("/api/v1/session", json={
                "init_data": init_data, "launch_token": launch,
            })
            assert failed.status_code == 500
            assert "reason=unexpected_error status=500" in _events(caplog)[-1]
            assert "user_id=4523274" in _events(caplog)[-1]
            assert "exception_class=RuntimeError" in _events(caplog)[-1]
            assert launch not in "\n".join(_events(caplog))
            assert init_data not in "\n".join(_events(caplog))
