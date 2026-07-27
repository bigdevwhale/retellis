"""``/v1/journal`` — the user-authored diary, separate from the chat event chain.

Exercises CRUD + ILIKE search + facet filters (persona / tag / mood / date
range) + pagination, the PATCH absent-vs-explicit-null distinction for the
nullable ``title`` / ``mood``, and cross-user isolation. Runs against the
in-memory store default. No key material, no LLM calls touch this surface.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta


def _headers(user: str) -> dict[str, str]:
    return {"X-User-Id": user}


async def _create(
    client, *, user: str, persona_id: str = "lou", body: str = "Сегодня было тихо.", **extra
) -> dict:
    payload: dict = {"persona_id": persona_id, "body": body}
    payload.update(extra)
    r = await client.post("/v1/journal", json=payload, headers=_headers(user))
    assert r.status_code == 200, await r.text()
    return r.json()


async def test_create_then_list_newest_first(client) -> None:
    a = await _create(client, user="u1", body="first entry")
    # Stagger created_at: the in-memory store stamps ``now`` per call, so a
    # second create strictly postdates the first.
    b = await _create(client, user="u1", body="second entry")
    rows = (await client.get("/v1/journal", headers=_headers("u1"))).json()
    assert [r["id"] for r in rows] == [b["id"], a["id"]]
    # Wire shape: the contract fields round-trip verbatim.
    assert rows[0]["body"] == "second entry"
    assert rows[0]["persona_id"] == "lou"
    assert rows[0]["tags"] == []
    assert rows[0]["salience"] == 0.0


async def test_tags_round_trip_on_create_and_patch(client) -> None:
    """``tags`` must come back verbatim from POST, survive PATCH with the
    same value, and be returned by the bare GET. This is the regression
    guard for "tags save but don't show" — both directions matter."""
    created = await _create(
        client, user="u1", body="with tags", tags=["work", "focus"],
    )
    assert created["tags"] == ["work", "focus"]

    # GET returns the same tags.
    rows = (await client.get("/v1/journal", headers=_headers("u1"))).json()
    assert rows[0]["tags"] == ["work", "focus"]

    # PATCH replaces the full tag list (model_fields_set semantics).
    patched = await client.patch(
        f"/v1/journal/{created['id']}",
        json={"body": "with tags", "tags": ["home"]},
        headers=_headers("u1"),
    )
    assert patched.status_code == 200, await patched.text()
    assert patched.json()["tags"] == ["home"]

    # PATCH without ``tags`` keeps the existing list (absent ≠ null).
    patched2 = await client.patch(
        f"/v1/journal/{created['id']}",
        json={"body": "untouched tags"},
        headers=_headers("u1"),
    )
    assert patched2.status_code == 200
    assert patched2.json()["tags"] == ["home"]


async def test_create_with_unicode_tags(client) -> None:
    created = await _create(
        client, user="u1", body="семья", tags=["семья", "работа"],
    )
    assert created["tags"] == ["семья", "работа"]
    rows = (await client.get("/v1/journal", headers=_headers("u1"))).json()
    assert rows[0]["tags"] == ["семья", "работа"]


async def test_search_ilike_title_and_body_case_insensitive_ru(client) -> None:
    await _create(client, user="u1", title="Дорога домой", body="шёл под дождём")
    await _create(client, user="u1", body="Ничего особенного")
    # RU substring, wrong case, matches the title.
    r = await client.get("/v1/journal?q=дорога", headers=_headers("u1"))
    assert {row["title"] for row in r.json()} == {"Дорога домой"}
    # EN-style case folding on a latin token in the body.
    await _create(client, user="u1", body="finished the SILENT report")
    r = await client.get("/v1/journal?q=silent", headers=_headers("u1"))
    assert len(r.json()) == 1
    assert "SILENT" in r.json()[0]["body"]


async def test_filter_by_persona(client) -> None:
    await _create(client, user="u1", persona_id="lou", body="lou entry")
    await _create(client, user="u1", persona_id="sage", body="sage entry")
    rows = (await client.get("/v1/journal?persona_id=sage", headers=_headers("u1"))).json()
    assert len(rows) == 1
    assert rows[0]["persona_id"] == "sage"


async def test_filter_by_tag(client) -> None:
    await _create(client, user="u1", body="one", tags=["work", "focus"])
    await _create(client, user="u1", body="two", tags=["home"])
    rows = (await client.get("/v1/journal?tag=work", headers=_headers("u1"))).json()
    assert [r["body"] for r in rows] == ["one"]


async def test_filter_by_mood(client) -> None:
    await _create(client, user="u1", body="a", mood="tired")
    await _create(client, user="u1", body="b", mood="calm")
    rows = (await client.get("/v1/journal?mood=tired", headers=_headers("u1"))).json()
    assert [r["body"] for r in rows] == ["a"]


async def test_filter_by_date_range(client) -> None:
    # Pin created_at via the store directly so the date filter is deterministic
    # (the router stamps ``now`` on POST and the in-memory clock can't be
    # rewound from the API).
    from ai_companion_api.memory.store import InMemoryStore

    store = InMemoryStore()
    old = await store.add_journal_entry(
        user_id="u1",
        persona_id="lou",
        title=None,
        body="long ago",
        mood=None,
        tags=[],
        salience=0.0,
        source_convo_id=None,
        source_event_id=None,
    )
    # Mutate the stored created_at to a fixed past moment.
    old_created = datetime(2024, 1, 15, tzinfo=UTC)
    store._journal[0] = old.model_copy(update={"created_at": old_created})

    # Hand the seeded store to the app and query through the router.
    import ai_companion_api.main as main_mod

    app = main_mod.create_app()
    app.state.store = store
    from httpx import ASGITransport, AsyncClient

    async with main_mod.lifespan(app):
        # lifespan overrides app.state.store — re-attach after it runs.
        app.state.store = store
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            cutoff = old_created + timedelta(days=1)
            iso = cutoff.isoformat().replace("+00:00", "Z")
            # ``from`` after the old entry → only the new POSTed one remains.
            r = await ac.get(f"/v1/journal?from={iso}", headers=_headers("u1"))
            rows = r.json()
            assert all(row["body"] != "long ago" for row in rows)
            # ``to`` before the cutoff → only the old entry.
            r2 = await ac.get(f"/v1/journal?to={iso}", headers=_headers("u1"))
            rows2 = r2.json()
            assert any(row["body"] == "long ago" for row in rows2)


async def test_pagination_limit_offset(client) -> None:
    for i in range(5):
        await _create(client, user="u1", body=f"e{i}")
    page1 = (await client.get("/v1/journal?limit=2&offset=0", headers=_headers("u1"))).json()
    page2 = (await client.get("/v1/journal?limit=2&offset=2", headers=_headers("u1"))).json()
    assert [r["body"] for r in page1] == ["e4", "e3"]
    assert [r["body"] for r in page2] == ["e2", "e1"]
    # limit cap is enforced (le=200); over-cap is a 422, not silently clamped.
    r = await client.get("/v1/journal?limit=999", headers=_headers("u1"))
    assert r.status_code == 422


async def test_patch_partial_only_supplied_fields_change(client) -> None:
    e = await _create(
        client, user="u1", title="T", body="B", mood="calm", tags=["x", "y"], salience=0.66
    )
    res = await client.patch(f"/v1/journal/{e['id']}", json={"body": "B2"}, headers=_headers("u1"))
    assert res.status_code == 200, await res.text()
    body = res.json()
    assert body["body"] == "B2"
    assert body["title"] == "T"  # unchanged
    assert body["mood"] == "calm"  # unchanged
    assert body["tags"] == ["x", "y"]  # unchanged
    # salience is NOT patchable via this endpoint — it stays as authored.
    assert body["salience"] == 0.66


async def test_patch_explicit_null_clears_title_and_mood(client) -> None:
    e = await _create(client, user="u1", title="T", body="B", mood="calm")
    res = await client.patch(
        f"/v1/journal/{e['id']}", json={"title": None, "mood": None}, headers=_headers("u1")
    )
    assert res.status_code == 200
    body = res.json()
    assert body["title"] is None
    assert body["mood"] is None
    assert body["body"] == "B"  # untouched


async def test_patch_refuses_empty_body(client) -> None:
    e = await _create(client, user="u1", body="B")
    res = await client.patch(f"/v1/journal/{e['id']}", json={"body": ""}, headers=_headers("u1"))
    assert res.status_code == 422


async def test_patch_missing_returns_404(client) -> None:
    res = await client.patch(
        "/v1/journal/does-not-exist", json={"body": "x"}, headers=_headers("u1")
    )
    assert res.status_code == 404


async def test_delete_returns_204_then_404(client) -> None:
    e = await _create(client, user="u1", body="bye")
    r = await client.delete(f"/v1/journal/{e['id']}", headers=_headers("u1"))
    assert r.status_code == 204
    # Gone from the listing.
    rows = (await client.get("/v1/journal", headers=_headers("u1"))).json()
    assert all(row["id"] != e["id"] for row in rows)
    # Second delete → 404 (idempotent failure, not 204).
    r2 = await client.delete(f"/v1/journal/{e['id']}", headers=_headers("u1"))
    assert r2.status_code == 404


async def test_cross_user_isolation(client) -> None:
    e = await _create(client, user="u1", body="mine")
    # u2 cannot see u1's entries.
    rows = (await client.get("/v1/journal", headers=_headers("u2"))).json()
    assert rows == []
    # u2 cannot patch u1's entry.
    r = await client.patch(
        f"/v1/journal/{e['id']}", json={"body": "hijack"}, headers=_headers("u2")
    )
    assert r.status_code == 404
    # u2 cannot delete u1's entry.
    r2 = await client.delete(f"/v1/journal/{e['id']}", headers=_headers("u2"))
    assert r2.status_code == 404
    # u1 still has it, untouched.
    rows2 = (await client.get("/v1/journal", headers=_headers("u1"))).json()
    assert rows2[0]["body"] == "mine"


async def test_create_from_chat_carries_source_links(client) -> None:
    e = await _create(
        client,
        user="u1",
        body="saved from chat",
        source_convo_id="c-1",
        source_event_id="ev-1",
    )
    assert e["source_convo_id"] == "c-1"
    assert e["source_event_id"] == "ev-1"


# --- /v1/journal/tags (sidebar tag cloud) ---


async def test_list_journal_tags_dedupes_and_sorts(client) -> None:
    await _create(client, user="u1", body="a", tags=["work"])
    await _create(client, user="u1", body="b", tags=["family", "work"])
    await _create(client, user="u1", body="c", tags=["idea"])
    r = await client.get("/v1/journal/tags", headers=_headers("u1"))
    assert r.status_code == 200, await r.text()
    # Wire shape: { tags: [..] } — wrapped, not a bare list.
    payload = r.json()
    assert payload == {"tags": ["family", "idea", "work"]}


async def test_list_journal_tags_filters_by_mood(client) -> None:
    await _create(client, user="u1", body="a", mood="calm", tags=["work"])
    await _create(client, user="u1", body="b", mood="tired", tags=["family"])
    r = await client.get("/v1/journal/tags?mood=calm", headers=_headers("u1"))
    assert r.json() == {"tags": ["work"]}


async def test_list_journal_tags_scoped_to_user(client) -> None:
    await _create(client, user="u1", body="a", tags=["work"])
    await _create(client, user="u2", body="b", tags=["other"])
    # u1 must not see u2's tags.
    r = await client.get("/v1/journal/tags", headers=_headers("u1"))
    assert r.json() == {"tags": ["work"]}
    r = await client.get("/v1/journal/tags", headers=_headers("u2"))
    assert r.json() == {"tags": ["other"]}


async def test_list_journal_tags_empty_when_no_entries(client) -> None:
    r = await client.get("/v1/journal/tags", headers=_headers("u1"))
    assert r.status_code == 200
    assert r.json() == {"tags": []}


async def test_list_journal_tags_ignores_active_tag_filter(client) -> None:
    """Picking a tag filter must NOT shrink the cloud — the cloud is the
    source of truth for filter chips. The server does not accept a ``tag``
    query param at all on this endpoint, so we verify the existing
    ``list_journal_entries?tag=`` filter does not bleed into the cloud."""
    await _create(client, user="u1", body="a", tags=["work", "focus"])
    await _create(client, user="u1", body="b", tags=["home"])
    # Filter the entries to a single tag — the cloud still lists every tag.
    await client.get("/v1/journal?tag=work", headers=_headers("u1"))
    r = await client.get("/v1/journal/tags", headers=_headers("u1"))
    assert r.json() == {"tags": ["focus", "home", "work"]}


# --- store: list_journal_tags (InMemoryStore unit, no router) ---


async def test_inmemory_list_journal_tags_filters_by_persona_and_family() -> None:
    from ai_companion_api.memory.store import InMemoryStore

    store = InMemoryStore()
    await store.add_journal_entry(
        user_id="u1", persona_id="lou", body="a", title=None, mood=None,
        tags=["work"], salience=0.0, source_convo_id=None,
        source_event_id=None, family_id="f1",
    )
    await store.add_journal_entry(
        user_id="u1", persona_id="sage", body="b", title=None, mood=None,
        tags=["family"], salience=0.0, source_convo_id=None,
        source_event_id=None, family_id="f1",
    )
    await store.add_journal_entry(
        user_id="u1", persona_id="lou", body="c", title=None, mood=None,
        tags=["idea"], salience=0.0, source_convo_id=None,
        source_event_id=None, family_id=None,
    )

    # No filters: all three tags.
    assert await store.list_journal_tags(user_id="u1") == ["family", "idea", "work"]
    # persona scope: just lou.
    assert await store.list_journal_tags(user_id="u1", persona_id="lou") == ["idea", "work"]
    # family scope: just f1.
    assert await store.list_journal_tags(user_id="u1", family_id="f1") == [
        "family",
        "work",
    ]
    # Both: just lou-in-f1.
    assert await store.list_journal_tags(
        user_id="u1", persona_id="lou", family_id="f1",
    ) == ["work"]


async def test_inmemory_list_journal_tags_date_range() -> None:
    from ai_companion_api.memory.store import InMemoryStore

    store = InMemoryStore()
    await store.add_journal_entry(
        user_id="u1", persona_id="lou", body="a", title=None, mood=None,
        tags=["old"], salience=0.0, source_convo_id=None, source_event_id=None,
    )
    b = await store.add_journal_entry(
        user_id="u1", persona_id="lou", body="b", title=None, mood=None,
        tags=["new"], salience=0.0, source_convo_id=None, source_event_id=None,
    )
    cutoff = b.created_at  # exclude ``a``
    assert await store.list_journal_tags(user_id="u1", from_dt=cutoff) == ["new"]
    # Empty user_id is a defensive guard, not the auth boundary — must return [].
    assert await store.list_journal_tags(user_id="") == []
