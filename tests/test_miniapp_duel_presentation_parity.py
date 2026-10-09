"""Mini App reads the same committed ordinary-duel presentation as Telegram."""

import json
from types import SimpleNamespace

import httpx
import pytest

import database
from handlers import duel_service
from handlers.persistent_duel_publisher import publish_persistent_duel_outbox
from miniapp_api import _plain_duel_text, create_miniapp_api
from tests.test_miniapp_api import CHAT_A, session_for
from tests.test_miniapp_auth import TEST_BOT_TOKEN
from tests.test_persistent_duel_finalization import NOW, TraceRng, register, terminal_session
from text_resources import get_text


def no_rng(*_args, **_kwargs):
    raise AssertionError("A persisted result read used RNG")


@pytest.mark.asyncio
@pytest.mark.parametrize("winner_before,loser_before,winner_after,loser_after", [
    (90, 20, 100, 15),
    (95, 3, 100, 0),
    (100, 0, 100, 0),
    (120, 20, 100, 15),
])
async def test_finished_points_use_live_balance_and_actual_change(
    temp_database, monkeypatch, winner_before, loser_before, winner_after, loser_after,
):
    register(CHAT_A)
    session = terminal_session(CHAT_A, winner_points=winner_before,
                               loser_points=loser_before)
    trace = TraceRng([0.99, 0.99, 0.99] if loser_before else [0.99, 0.99])
    monkeypatch.setattr(duel_service, "random", trace)
    finalized = duel_service.finalize_persistent_duel(CHAT_A, session["id"], now_ms=NOW + 1)
    assert finalized.result["winner_points_awarded"] == max(0, winner_after - winner_before)
    assert finalized.result["loser_points_awarded"] == loser_after - loser_before
    assert finalized.result["winner_points_delta"] == winner_after - winner_before
    assert finalized.result["loser_points_delta"] == loser_after - loser_before
    assert f"{winner_after - winner_before:+d} очков" in finalized.result["final_text"]
    assert f"{loser_after - loser_before:+d} очков" in finalized.result["final_text"]

    monkeypatch.setattr(duel_service, "random", SimpleNamespace(random=no_rng, choice=no_rng))
    api = create_miniapp_api(bot_token=TEST_BOT_TOKEN, allowed_origin="")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api),
                                 base_url="http://test") as client:
        headers = await session_for(client, CHAT_A, 1)
        points = (await client.get("/api/v1/duel/active", headers=headers)).json()[
            "recent_finished"
        ]["points"]
    assert points == {
        "winner_before": winner_before, "winner_after": winner_after,
        "winner_delta": winner_after - winner_before,
        "winner_delta_awarded": max(0, winner_after - winner_before),
        "loser_before": loser_before, "loser_after": loser_after,
        "loser_delta": loser_after - loser_before,
        "loser_delta_awarded": loser_after - loser_before,
    }


@pytest.mark.asyncio
async def test_telegram_final_and_miniapp_share_selected_presentation_after_restart(
    temp_database, fake_context, monkeypatch,
):
    register(CHAT_A)
    database.add_duel_inventory_item(CHAT_A, 2, "ceremonial_bolt")
    session = terminal_session(CHAT_A, loser_points=0)
    trace = TraceRng([0.0, 0.0, 0.0])
    monkeypatch.setattr(duel_service, "random", trace)
    finalized = duel_service.finalize_persistent_duel(CHAT_A, session["id"], now_ms=NOW + 1)
    result = finalized.result
    assert result["dwarf_fact"] and result["round_flavor"]
    assert result["berserk"] and result["post_message"]
    # This fixture represents an older checkpoint without presentation_html.
    assert "presentation_html" not in result["terminal_resolution"]

    with database.get_db() as conn:
        publication_id, payload = conn.execute(
            "SELECT id, payload_json FROM duel_outbox WHERE chat_id = ? AND duel_id = ? "
            "AND kind = 'final_result'", (CHAT_A, session["id"]),
        ).fetchone()
    assert json.loads(payload)["text"] == result["final_text"]
    sent = await publish_persistent_duel_outbox(
        CHAT_A, publication_id, fake_context.bot,
        claim_time_ms=NOW + 2, published_at_ms=NOW + 2,
    )
    assert sent.reason == "delivered"
    assert fake_context.bot.send_message.call_args.kwargs["text"] == result["final_text"]

    before_reads = list(trace.trace)
    monkeypatch.setattr(duel_service, "random", SimpleNamespace(random=no_rng, choice=no_rng))
    api = create_miniapp_api(bot_token=TEST_BOT_TOKEN, allowed_origin="")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api),
                                 base_url="http://test") as client:
        headers = await session_for(client, CHAT_A, 1)
        first = (await client.get("/api/v1/duel/active", headers=headers)).json()["recent_finished"]
        assert first == (await client.get("/api/v1/duel/active", headers=headers)).json()[
            "recent_finished"
        ]
    reopened = create_miniapp_api(bot_token=TEST_BOT_TOKEN, allowed_origin="")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=reopened),
                                 base_url="http://test") as client:
        headers = await session_for(client, CHAT_A, 2)
        assert (await client.get("/api/v1/duel/active", headers=headers)).json()[
            "recent_finished"
        ] == first
    assert trace.trace == before_reads
    assert first["rounds"][-1]["presentation_text"] == _plain_duel_text(result["custom_text"])
    assert first["round_flavor"] == _plain_duel_text(result["round_flavor"])
    assert first["dwarf_fact"] == result["dwarf_fact"]
    assert first["berserk"]["text"] == _plain_duel_text(result["berserk"]["text"])
    assert first["post_message"] == result["post_message"]
    assert first["note_prefix"] == get_text("duel.finish.post_message.prefix")
    assert first["duration"] == {"rounds": 2, "text": "2 раунда"}


@pytest.mark.asyncio
@pytest.mark.parametrize("rounds,expected", [
    (1, "1 раунд"), (2, "2 раунда"), (5, "5 раундов"),
])
async def test_duration_uses_canonical_server_pluralization(
    temp_database, monkeypatch, rounds, expected,
):
    register(CHAT_A)
    session = terminal_session(CHAT_A)
    with database.get_db() as conn:
        conn.execute(
            "UPDATE duel_sessions SET round_no = ?, "
            "result_json = json_set(result_json, '$.round_no', ?) WHERE id = ?",
            (rounds, rounds, session["id"]),
        )
    monkeypatch.setattr(duel_service, "random", TraceRng([0.99, 0.99, 0.99]))
    duel_service.finalize_persistent_duel(CHAT_A, session["id"])
    api = create_miniapp_api(bot_token=TEST_BOT_TOKEN, allowed_origin="")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api),
                                 base_url="http://test") as client:
        headers = await session_for(client, CHAT_A, 1)
        finished = (await client.get("/api/v1/duel/active", headers=headers)).json()[
            "recent_finished"
        ]
    assert finished["duration"] == {"rounds": rounds, "text": expected}


@pytest.mark.asyncio
async def test_old_finished_result_without_new_optional_fields_is_readable_without_reroll(
    temp_database, monkeypatch,
):
    register(CHAT_A)
    session = terminal_session(CHAT_A)
    monkeypatch.setattr(duel_service, "random", TraceRng([0.99, 0.99, 0.99]))
    result = duel_service.finalize_persistent_duel(CHAT_A, session["id"]).result
    for key in ("winner_points_before", "loser_points_before", "winner_points_delta",
                "loser_points_delta", "winner_points_awarded", "loser_points_awarded", "round_flavor",
                "dwarf_fact", "post_message", "berserk"):
        result.pop(key, None)
    result["terminal_resolution"].pop("presentation_html", None)
    with database.get_db() as conn:
        conn.execute("UPDATE duel_sessions SET result_json = ? WHERE id = ?",
                     (json.dumps(result), session["id"]))
    monkeypatch.setattr(duel_service, "random", SimpleNamespace(random=no_rng, choice=no_rng))
    api = create_miniapp_api(bot_token=TEST_BOT_TOKEN, allowed_origin="")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api),
                                 base_url="http://test") as client:
        headers = await session_for(client, CHAT_A, 1)
        response = await client.get("/api/v1/duel/active", headers=headers)
    assert response.status_code == 200
    finished = response.json()["recent_finished"]
    assert finished["points"]["winner_delta_awarded"] is None
    assert finished["points"]["loser_delta_awarded"] is None
    assert finished["round_flavor"] is None
    assert finished["dwarf_fact"] is None
    assert finished["post_message"] is None
    assert finished["rounds"][-1]["presentation_text"]


@pytest.mark.asyncio
async def test_historical_over_cap_result_uses_snapshot_without_rewriting_history(
    temp_database, monkeypatch,
):
    register(CHAT_A)
    session = terminal_session(CHAT_A, winner_points=120)
    monkeypatch.setattr(duel_service, "random", TraceRng([0.99, 0.99, 0.99]))
    result = duel_service.finalize_persistent_duel(CHAT_A, session["id"]).result
    for key in ("winner_points_before", "loser_points_before",
                "winner_points_delta", "loser_points_delta"):
        result.pop(key)
    result["winner_points_awarded"] = 10  # Older records stored nominal reward.
    historical_json = json.dumps(result)
    with database.get_db() as conn:
        conn.execute("UPDATE duel_sessions SET result_json = ? WHERE id = ?",
                     (historical_json, session["id"]))
    monkeypatch.setattr(duel_service, "random", SimpleNamespace(random=no_rng, choice=no_rng))
    api = create_miniapp_api(bot_token=TEST_BOT_TOKEN, allowed_origin="")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api),
                                 base_url="http://test") as client:
        headers = await session_for(client, CHAT_A, 1)
        points = (await client.get("/api/v1/duel/active", headers=headers)).json()[
            "recent_finished"]["points"]
    assert points["winner_before"] == 120
    assert points["winner_after"] == 100
    assert points["winner_delta"] == -20
    assert points["winner_delta_awarded"] == 10
    with database.get_db() as conn:
        assert conn.execute("SELECT result_json FROM duel_sessions WHERE id = ?",
                            (session["id"],)).fetchone()[0] == historical_json
