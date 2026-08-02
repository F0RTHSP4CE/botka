from __future__ import annotations

from sqlalchemy import inspect, text

from botka.db.session import init_models


async def test_init_models_removes_legacy_anonymous_admin_snapshots(engine) -> None:
    async with engine.begin() as connection:
        await connection.execute(
            text(
                "CREATE TABLE anonymous_admin_snapshots "
                "(id INTEGER PRIMARY KEY, permissions TEXT)"
            )
        )

    await init_models(engine)

    async with engine.connect() as connection:
        table_names = await connection.run_sync(
            lambda sync_connection: inspect(sync_connection).get_table_names()
        )
    assert "anonymous_admin_snapshots" not in table_names
