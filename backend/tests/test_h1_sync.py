"""Offline sync regressions; temporary SQLite, no real credentials or integrations."""
import asyncio
import json
import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

# Other legacy test modules import the app after this module during discovery. Ensure that
# the initial application engine also points to a disposable database, never operator data.
_app_store = tempfile.TemporaryDirectory(prefix="reconhawk-h1-app-")
os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///" + _app_store.name + "/app.db"
os.environ["ARTIFACTS_DIR"] = _app_store.name + "/artifacts"
os.environ["ANTHROPIC_API_KEY"] = ""

from app import h1_sync
from app.db import Base, Program, now
from app.integrations.hackerone import HackerOneError


def scope(host="new.example", eligible=True, asset_type="URL"):
    return {"type": "structured-scope", "id": host, "attributes": {
        "asset_identifier": host, "asset_type": asset_type, "eligible_for_submission": eligible}}


class HackerOneSyncTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="reconhawk-h1-sync-")
        self.engine = create_async_engine("sqlite+aiosqlite:///" + self.tmp.name + "/sync.db")
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        self.session_patch = patch.object(h1_sync, "SessionLocal", self.sessions)
        self.session_patch.start()
        self.notifications = patch.object(h1_sync.notify, "push", new_callable=AsyncMock)
        self.notify = self.notifications.start()
        self.sleep = AsyncMock()
        async with self.sessions() as session:
            row = Program(name="Existing", platform="hackerone", handle="alpha", policy="Keep this policy",
                          program_type="bbp", scope_in='["old.example"]', scope_out='["deny.old.example"]',
                          scope_hash="old-hash", scope_synced_at=now(), scope_asset_count=2, scope_web_asset_count=2)
            row.scope_assets_json = '[{"asset_identifier":"old.example","asset_type":"URL"}]'
            session.add(row)
            await session.commit()
            self.pid, self.previous_synced_at = row.id, h1_sync.timestamp(row.scope_synced_at)

    async def asyncTearDown(self):
        await h1_sync.stop()
        self.notifications.stop()
        self.session_patch.stop()
        await self.engine.dispose()
        self.tmp.cleanup()

    async def row(self, handle="alpha"):
        async with self.sessions() as session:
            return (await session.execute(select(Program).where(
                Program.platform == "hackerone", Program.handle == handle))).scalars().one()

    async def run_sync(self, fake, handles=None, cfg=None, concurrency=1):
        # Ordered-side-effect tests keep the serial contract; concurrency has its own tests.
        return await h1_sync.sync_scopes(handles or ["alpha"], client=fake,
                                         cfg=cfg if cfg is not None else {}, sleep=self.sleep,
                                         concurrency=concurrency)

    async def test_import_classifies_and_preserves_omitted_policy(self):
        result = await h1_sync.import_programs([
            {"attributes": {"handle": "alpha", "offers_bounties": True}},
            {"attributes": {"handle": "beta", "name": "Disclosure", "offers_bounties": False}},
            {"attributes": {"handle": "unknown", "name": "Missing eligibility"}}])
        self.assertEqual((result["added"], result["updated"], result["total"]), (2, 1, 3))
        row = await self.row()
        self.assertEqual((row.program_type, row.name, row.policy, row.scope_in),
                         ("bounty", "Existing", "Keep this policy", '["old.example"]'))
        self.assertEqual((await self.row("beta")).program_type, "vdp")
        self.assertEqual((await self.row("unknown")).program_type, "unknown")

    async def test_import_never_overwrites_manual_program_with_same_handle(self):
        async with self.sessions() as session:
            manual = Program(name="Manual record", handle="beta", platform="manual", policy="Manual policy")
            session.add(manual)
            await session.commit()
            mid = manual.id
        await h1_sync.import_programs([{"attributes": {"handle": "beta", "name": "Imported", "offers_bounties": True}}])
        async with self.sessions() as session:
            manual = await session.get(Program, mid)
            self.assertEqual((manual.name, manual.policy, manual.platform), ("Manual record", "Manual policy", "manual"))
        self.assertEqual((await self.row("beta")).name, "Imported")

    async def test_duplicate_handles_are_imported_once(self):
        result = await h1_sync.import_programs([{"attributes": {"handle": "beta", "offers_bounties": True}}] * 2)
        self.assertEqual((result["added"], result["total"]), (1, 1))

    async def test_success_replaces_complete_scope_and_records_metadata(self):
        result = await self.run_sync(SimpleNamespace(get_scopes=AsyncMock(return_value=[scope(), scope("deny.example", False)])))
        row = await self.row()
        self.assertEqual(json.loads(row.scope_in), ["new.example"])
        self.assertEqual(json.loads(row.scope_out), ["deny.example"])
        self.assertEqual((row.scope_sync_status, row.scope_asset_count, row.scope_web_asset_count), ("synced", 2, 2))
        self.assertEqual(row.scope_sync_attempts, 1)
        self.assertEqual(json.loads(row.scope_assets_json)[0]["asset_identifier"], "new.example")
        self.assertNotEqual(row.scope_hash, "old-hash")
        self.assertEqual((result["status"], result["succeeded"], result["failed"]), ("complete", 1, 0))

    async def test_failure_preserves_last_known_scope_and_success_timestamp(self):
        result = await self.run_sync(SimpleNamespace(get_scopes=AsyncMock(side_effect=HackerOneError("Forbidden", status_code=403))))
        row = await self.row()
        self.assertEqual((row.scope_in, row.scope_out, row.scope_hash), ('["old.example"]', '["deny.old.example"]', "old-hash"))
        self.assertEqual(h1_sync.timestamp(row.scope_synced_at), self.previous_synced_at)
        self.assertEqual((row.scope_asset_count, row.scope_web_asset_count), (2, 2))
        self.assertEqual(json.loads(row.scope_assets_json)[0]["asset_identifier"], "old.example")
        self.assertEqual(row.scope_sync_status, "error")
        self.assertIn("Forbidden", row.scope_sync_error)
        self.assertEqual((result["status"], result["failed"]), ("partial", 1))
        self.sleep.assert_not_awaited()
        self.assertEqual(self.notify.call_args.args[0], "warn")

    async def test_partial_failure_does_not_drop_remaining_programs(self):
        await h1_sync.import_programs([{"attributes": {"handle": "beta", "offers_bounties": False}}])
        fake = SimpleNamespace(get_scopes=AsyncMock(side_effect=[HackerOneError("Missing", status_code=404), [scope("beta.example")]]))
        result = await self.run_sync(fake, ["alpha", "beta"])
        self.assertEqual((result["status"], result["completed"], result["succeeded"], result["failed"]), ("partial", 2, 1, 1))
        self.assertEqual((await self.row("beta")).scope_sync_status, "synced")

    async def test_rate_limit_retries_after_server_delay(self):
        fake = SimpleNamespace(get_scopes=AsyncMock(side_effect=[HackerOneError("Wait", status_code=429, retry_after=7), [scope()]]))
        self.assertEqual((await self.run_sync(fake))["status"], "complete")
        self.sleep.assert_awaited_once_with(7.0)
        self.assertEqual((await self.row()).scope_sync_attempts, 2)

    async def test_long_rate_limit_is_visible_and_keeps_scope(self):
        fake = SimpleNamespace(get_scopes=AsyncMock(side_effect=HackerOneError("Retry later", status_code=429, retry_after=120)))
        result = await self.run_sync(fake)
        row = await self.row()
        self.assertEqual((row.scope_sync_status, row.scope_in), ("rate_limited", '["old.example"]'))
        self.assertEqual(result["failed"], 1)
        self.assertEqual(fake.get_scopes.await_count, 1)
        self.sleep.assert_not_awaited()
        self.assertGreater(result["retry_after_seconds"], 0)
        with self.assertRaises(h1_sync.SyncBusy):
            await h1_sync.queue_scopes(["alpha"])

    async def test_transient_failure_has_bounded_retries(self):
        fake = SimpleNamespace(get_scopes=AsyncMock(side_effect=HackerOneError("Unavailable", status_code=503)))
        self.assertEqual((await self.run_sync(fake))["status"], "partial")
        self.assertEqual(fake.get_scopes.await_count, 3)
        self.assertEqual([call.args[0] for call in self.sleep.await_args_list], [2.0, 4.0])

    async def test_rate_limit_defers_remaining_programs_visibly(self):
        await h1_sync.import_programs([{"attributes": {"handle": "beta", "offers_bounties": False}}])
        fake = SimpleNamespace(get_scopes=AsyncMock(side_effect=HackerOneError("Cooldown", status_code=429, retry_after=120)))
        result = await self.run_sync(fake, ["alpha", "beta"])
        self.assertEqual((result["status"], result["completed"], result["failed"]), ("partial", 2, 2))
        self.assertEqual(fake.get_scopes.await_count, 1)
        row = await self.row("beta")
        self.assertEqual(row.scope_sync_status, "rate_limited")
        self.assertIn("Deferred", row.scope_sync_error)
        self.assertEqual(row.scope_sync_attempts, 0)

    async def test_transport_timeout_retries(self):
        fake = SimpleNamespace(get_scopes=AsyncMock(side_effect=[httpx.ReadTimeout("timeout"), [scope()]]))
        self.assertEqual((await self.run_sync(fake))["status"], "complete")
        self.assertEqual(fake.get_scopes.await_count, 2)

    async def test_successful_empty_scope_revokes_old_targets(self):
        await self.run_sync(SimpleNamespace(get_scopes=AsyncMock(return_value=[])))
        row = await self.row()
        self.assertEqual((row.scope_in, row.scope_out, row.scope_sync_status), ("[]", "[]", "empty"))

    async def test_unsupported_assets_are_visible_without_expanding_scope(self):
        await self.run_sync(SimpleNamespace(get_scopes=AsyncMock(return_value=[scope("com.example.mobile", asset_type="APPLE_STORE_APP_ID")])))
        row = await self.row()
        self.assertEqual((row.scope_sync_status, row.scope_asset_count, row.scope_web_asset_count), ("empty", 1, 0))
        self.assertTrue(json.loads(row.scope_sync_warnings))
        asset = json.loads(row.scope_assets_json)[0]
        self.assertEqual(asset["asset_identifier"], "com.example.mobile")
        self.assertEqual(asset["normalization_status"], "unsupported")

    async def test_errors_do_not_disclose_configured_credentials(self):
        cfg = {"hackerone_token": "secret-token-123", "hackerone_username": "private-user"}
        fake = SimpleNamespace(get_scopes=AsyncMock(side_effect=HackerOneError("private-user secret-token-123 denied", status_code=403)))
        result = await self.run_sync(fake, cfg=cfg)
        self.assertNotIn("secret-token-123", json.dumps(result))
        self.assertNotIn("private-user", (await self.row()).scope_sync_error)

    async def test_queue_refuses_concurrent_job(self):
        started, release = asyncio.Event(), asyncio.Event()
        async def job(handles):
            started.set()
            await release.wait()
        with patch.object(h1_sync, "sync_scopes", side_effect=job):
            self.assertEqual((await h1_sync.queue_scopes(["alpha"]))["status"], "queued")
            await started.wait()
            with self.assertRaises(h1_sync.SyncBusy):
                await h1_sync.queue_scopes(["alpha"])
            release.set()
            await h1_sync.stop()

    async def test_restart_marks_interrupted_sync_without_erasing_scope(self):
        async with self.sessions() as session:
            row = await session.get(Program, self.pid)
            row.scope_sync_status = "syncing"
            await session.commit()
        await h1_sync.recover_interrupted()
        row = await self.row()
        self.assertEqual((row.scope_sync_status, row.scope_in), ("interrupted", '["old.example"]'))

    async def test_cancellation_marks_inflight_work_interrupted(self):
        started = asyncio.Event()
        async def blocked(handle):
            started.set()
            await asyncio.Event().wait()
        job = asyncio.create_task(self.run_sync(SimpleNamespace(get_scopes=blocked)))
        await started.wait()
        job.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await job
        row = await self.row()
        self.assertEqual((row.scope_sync_status, row.scope_in), ("interrupted", '["old.example"]'))

    # ---- speed: concurrent workers, startup refresh of stale scopes only ----

    async def _many(self, count):
        await h1_sync.import_programs([{"attributes": {"handle": f"p{i}"}} for i in range(count)])
        return [f"p{i}" for i in range(count)]

    async def test_concurrent_workers_fetch_programs_in_parallel(self):
        handles = await self._many(12)
        active = peak = 0

        async def get_scopes(handle):
            nonlocal active, peak
            active += 1
            peak = max(peak, active)
            await asyncio.sleep(0.01)
            active -= 1
            return [scope(handle + ".example")]

        result = await self.run_sync(SimpleNamespace(get_scopes=get_scopes), handles, concurrency=6)
        self.assertEqual((result["status"], result["succeeded"]), ("complete", 12))
        self.assertGreater(peak, 1)
        self.assertLessEqual(peak, 6)
        self.assertEqual((await self.row("p11")).scope_in, '["p11.example"]')

    async def test_rate_limit_stops_new_requests_across_workers(self):
        handles = await self._many(20)
        fake = SimpleNamespace(get_scopes=AsyncMock(
            side_effect=HackerOneError("Cooldown", status_code=429, retry_after=120)))
        result = await self.run_sync(fake, handles, concurrency=4)
        self.assertEqual((result["completed"], result["failed"]), (20, 20))
        # Only requests already in flight when the first 429 arrived may run.
        self.assertLessEqual(fake.get_scopes.await_count, 4)

    async def test_stale_handles_skip_recent_and_prioritize_never_synced(self):
        await h1_sync.import_programs([{"attributes": {"handle": "fresh"}}, {"attributes": {"handle": "never"}}])
        async with self.sessions() as session:
            row = (await session.execute(select(Program).where(Program.handle == "fresh"))).scalars().one()
            row.scope_sync_status, row.scope_synced_at = "synced", now()
            old = (await session.execute(select(Program).where(Program.handle == "alpha"))).scalars().one()
            old.scope_sync_status = "synced"
            old.scope_synced_at = now() - __import__("datetime").timedelta(hours=48)
            await session.commit()
        order = await h1_sync.stale_handles(["alpha", "fresh", "never"], 12)
        self.assertEqual(order, ["never", "alpha"])
        self.assertEqual(await h1_sync.stale_handles(["fresh"], 0), ["fresh"])

    async def test_startup_sync_imports_and_queues_only_stale(self):
        async with self.sessions() as session:
            row = await session.get(Program, self.pid)
            row.scope_sync_status = "synced"
            await session.commit()
        fake = SimpleNamespace(
            list_programs=AsyncMock(return_value=[{"attributes": {"handle": "alpha"}},
                                                  {"attributes": {"handle": "gamma"}}]),
            get_scopes=AsyncMock(return_value=[scope("gamma.example")]))
        result = await h1_sync.startup_sync(client=fake, cfg={"h1_auto_sync": "true"})
        self.assertEqual((result["status"], result["stale"]), ("queued", 1))
        await h1_sync._task
        fake.get_scopes.assert_awaited_once_with("gamma")
        self.assertEqual((await self.row("gamma")).scope_sync_status, "synced")

    async def test_startup_sync_respects_disable_and_missing_credentials(self):
        self.assertEqual((await h1_sync.startup_sync(cfg={"h1_auto_sync": "false"}))["status"], "disabled")
        self.assertEqual((await h1_sync.startup_sync(cfg={"h1_auto_sync": "true"}))["status"], "not_configured")

    async def test_startup_sync_failure_is_reported_not_raised(self):
        fake = SimpleNamespace(list_programs=AsyncMock(side_effect=HackerOneError("Down", status_code=503)))
        result = await h1_sync.startup_sync(client=fake, cfg={"h1_auto_sync": "true"})
        self.assertEqual(result["status"], "error")

    def test_request_interval_follows_configured_rate(self):
        self.assertAlmostEqual(h1_sync.request_interval({"h1_requests_per_second": "8"}), 0.125)
        self.assertAlmostEqual(h1_sync.request_interval({"h1_requests_per_second": "999"}), 0.1)
        self.assertAlmostEqual(h1_sync.request_interval({}), 0.125)


if __name__ == "__main__":
    unittest.main()
