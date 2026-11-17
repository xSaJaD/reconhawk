"""Offline isolation/regression cases for finding relation suggestions."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from app import intel
from app.db import Base, Finding, Program, Task


class FindingIntelligenceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="finding-intel-")
        self.engine = create_async_engine("sqlite+aiosqlite:///" + str(Path(self.tmp.name) / "test.db"))
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        self.mock = patch("app.intel.SessionLocal", self.sessions)
        self.mock.start()
        async with self.sessions() as session:
            a, b = Program(name="Program A"), Program(name="Program B")
            session.add_all([a, b])
            await session.commit()
            self.a, self.b = a.id, b.id

    async def asyncTearDown(self):
        self.mock.stop()
        await self.engine.dispose()
        self.tmp.cleanup()

    async def task(self, program_id, target="https://app.example.test"):
        async with self.sessions() as session:
            row = Task(program_id=program_id, target=target)
            session.add(row)
            await session.commit()
            return row.id

    async def finding(self, task_id, summary="Owned-account control confirms missing authorization at /api/orders", status="confirmed", title="Order object authorization missing"):
        async with self.sessions() as session:
            row = Finding(task_id=task_id, title=title, summary=summary, status=status)
            session.add(row)
            await session.commit()
            return row.id

    async def classify(self, task_id, summary="Owned-account control confirms missing authorization at /api/orders", title="Order object authorization missing"):
        return await intel.classify_finding(title, summary, exclude_task_id=task_id)

    async def test_identical_reports_in_different_programs_remain_new(self):
        prior = await self.task(self.b)
        await self.finding(prior)
        self.assertEqual(await self.classify(await self.task(self.a)), {"relation": "new", "related_to": None})

    async def test_same_program_different_hosts_remain_new(self):
        prior = await self.task(self.a, "https://other.example.test")
        await self.finding(prior)
        self.assertEqual((await self.classify(await self.task(self.a)))["relation"], "new")

    async def test_same_host_different_endpoint_remains_new(self):
        prior = await self.task(self.a)
        await self.finding(prior)
        result = await self.classify(await self.task(self.a), "Owned-account control confirms missing authorization at /api/invoices")
        self.assertEqual(result["relation"], "new")

    async def test_same_program_endpoint_suggests_existing_finding(self):
        prior = await self.task(self.a)
        fid = await self.finding(prior)
        self.assertEqual(await self.classify(await self.task(self.a)), {"relation": "same", "related_to": fid})

    async def test_markdown_quoted_endpoint_is_a_location(self):
        prior = await self.task(self.a)
        fid = await self.finding(prior, summary="Owned-account control at `GET /api/orders`")
        result = await self.classify(await self.task(self.a), "Owned-account control at `GET /api/orders`")
        self.assertEqual(result, {"relation": "same", "related_to": fid})

    async def test_fixed_same_location_suggests_bypass(self):
        prior = await self.task(self.a)
        fid = await self.finding(prior, status="fixed")
        self.assertEqual(await self.classify(await self.task(self.a)), {"relation": "bypass", "related_to": fid})

    async def test_same_title_without_location_is_insufficient(self):
        prior = await self.task(self.a)
        await self.finding(prior, summary="Authorization check failed")
        self.assertEqual((await self.classify(await self.task(self.a), "Authorization check failed"))["relation"], "new")

    async def test_non_root_task_target_can_supply_location(self):
        prior = await self.task(self.a, "https://app.example.test/api/orders")
        fid = await self.finding(prior, summary="Authorization check failed")
        current = await self.task(self.a, "https://app.example.test/api/orders")
        self.assertEqual(await self.classify(current, "Authorization check failed"), {"relation": "same", "related_to": fid})

    async def test_query_values_do_not_defeat_same_parameter_match(self):
        prior = await self.task(self.a)
        fid = await self.finding(prior, summary="Authorization check failed at https://app.example.test/api/orders?id=owned-a")
        result = await self.classify(await self.task(self.a), "Authorization check failed at https://app.example.test/api/orders?id=owned-b")
        self.assertEqual(result, {"relation": "same", "related_to": fid})

    async def test_distinct_query_parameters_are_distinct_locations(self):
        prior = await self.task(self.a)
        await self.finding(prior, summary="Authorization check failed at https://app.example.test/api/orders?id=owned-a")
        result = await self.classify(await self.task(self.a), "Authorization check failed at https://app.example.test/api/orders?tenant=owned-b")
        self.assertEqual(result["relation"], "new")

    async def test_no_program_is_host_scoped(self):
        prior = await self.task(None, "https://other.example.test")
        await self.finding(prior)
        self.assertEqual((await self.classify(await self.task(None)))["relation"], "new")

    async def test_no_program_same_host_location_can_match(self):
        prior = await self.task(None)
        fid = await self.finding(prior)
        self.assertEqual(await self.classify(await self.task(None)), {"relation": "same", "related_to": fid})

    async def test_manual_scan_does_not_match_program_owned_scan(self):
        prior = await self.task(self.a)
        await self.finding(prior)
        self.assertEqual((await self.classify(await self.task(None)))["relation"], "new")

    async def test_current_task_findings_are_not_comparison_history(self):
        current = await self.task(self.a)
        await self.finding(current)
        self.assertEqual((await self.classify(current))["relation"], "new")

    async def test_missing_current_context_is_not_a_global_search(self):
        prior = await self.task(self.a)
        await self.finding(prior)
        self.assertEqual((await self.classify(99999))["relation"], "new")
        self.assertEqual((await intel.classify_finding("Order object authorization missing", "/api/orders"))["relation"], "new")

    async def test_external_reference_link_does_not_supply_asset_location(self):
        prior = await self.task(self.a)
        await self.finding(prior, summary="Authorization check failed; reference https://docs.example.test/idor")
        result = await self.classify(await self.task(self.a), "Authorization check failed; reference https://docs.example.test/idor")
        self.assertEqual(result["relation"], "new")


if __name__ == "__main__":
    unittest.main()
