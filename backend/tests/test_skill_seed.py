"""Built-in skill seeding: refresh, prune retired built-ins, preserve custom + choices."""
import os, tempfile, unittest
from unittest.mock import patch

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

_store = tempfile.TemporaryDirectory(prefix="reconhawk-skillseed-")
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///" + _store.name + "/app.db")
os.environ.setdefault("ARTIFACTS_DIR", _store.name + "/artifacts")
os.environ.setdefault("ANTHROPIC_API_KEY", "")

from app import skills_seed, knowledge
from app.db import Base, Skill


class SkillSeedTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="reconhawk-skillseed-db-")
        self.engine = create_async_engine("sqlite+aiosqlite:///" + self.tmp.name + "/s.db")
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)
        async with self.engine.begin() as c:
            await c.run_sync(Base.metadata.create_all)
        self.p1 = patch.object(skills_seed, "SessionLocal", self.sessions)
        self.p2 = patch.object(knowledge, "SessionLocal", self.sessions)
        self.p1.start(); self.p2.start()

    async def asyncTearDown(self):
        self.p1.stop(); self.p2.stop()
        await self.engine.dispose(); self.tmp.cleanup()

    async def all_skills(self):
        async with self.sessions() as s:
            return (await s.execute(select(Skill))).scalars().all()

    async def test_seed_installs_full_strong_set_with_references(self):
        await skills_seed.seed_skills()
        rows = await self.all_skills()
        self.assertEqual(len(rows), len(skills_seed.DEFAULT_SKILLS))
        self.assertGreaterEqual(len(rows), 25)
        self.assertTrue(all(r.references_md and "References:" in r.references_md for r in rows))
        self.assertTrue(all(r.source == "builtin" and not r.is_custom for r in rows))
        # Covers the headline vulnerability classes.
        tags = " ".join(r.tags for r in rows)
        for needed in ("idor", "sqli", "xss", "ssrf", "ssti", "rce", "xxe", "jwt", "csrf", "cors"):
            self.assertIn(needed, tags)

    async def test_seed_is_idempotent_and_refreshes_content(self):
        await skills_seed.seed_skills()
        async with self.sessions() as s:
            row = (await s.execute(select(Skill).where(Skill.name.like("SQL injection%")))).scalars().one()
            row.playbook = "stale"; await s.commit()
        await skills_seed.seed_skills()  # second run must not duplicate and must refresh
        rows = await self.all_skills()
        self.assertEqual(len(rows), len(skills_seed.DEFAULT_SKILLS))
        async with self.sessions() as s:
            row = (await s.execute(select(Skill).where(Skill.name.like("SQL injection%")))).scalars().one()
            self.assertNotEqual(row.playbook, "stale")

    async def test_seed_preserves_disabled_choice(self):
        await skills_seed.seed_skills()
        async with self.sessions() as s:
            row = (await s.execute(select(Skill).where(Skill.name.like("Cross-site scripting%")))).scalars().one()
            row.enabled = False; await s.commit()
        await skills_seed.seed_skills()
        async with self.sessions() as s:
            row = (await s.execute(select(Skill).where(Skill.name.like("Cross-site scripting%")))).scalars().one()
            self.assertFalse(row.enabled)

    async def test_retired_builtin_is_removed_custom_is_kept(self):
        async with self.sessions() as s:
            s.add(Skill(name="Old weak skill", category="web", playbook="legacy",
                        source="builtin", is_custom=False, enabled=True))
            s.add(Skill(name="RHQ v1 - operator pack", category="web", playbook="mine",
                        source="custom", is_custom=True, enabled=True))
            await s.commit()
        await skills_seed.seed_skills()
        names = {r.name for r in await self.all_skills()}
        self.assertNotIn("Old weak skill", names)           # retired built-in pruned
        self.assertIn("RHQ v1 - operator pack", names)      # custom skill preserved

    async def test_skills_context_includes_references_without_blank_lines(self):
        await skills_seed.seed_skills()
        ctx = await knowledge.skills_context()
        self.assertIn("References:", ctx)
        self.assertNotIn("\n\n\n", ctx)


if __name__ == "__main__":
    unittest.main()


class SkillDeletionPersistsTests(SkillSeedTests):
    async def test_deleted_builtin_is_not_resurrected_on_reseed(self):
        await skills_seed.seed_skills()
        # Operator deletes a built-in (mirrors the DELETE route path).
        async with self.sessions() as s:
            from app.db import Skill
            from sqlalchemy import select as _sel
            row = (await s.execute(_sel(Skill).where(Skill.name.like("JWT%")))).scalars().one()
            name = row.name
            await s.delete(row)
            await skills_seed.mark_builtin_deleted(s, name)
            await s.commit()
        await skills_seed.seed_skills()  # restart re-seed must NOT bring it back
        names = {r.name for r in await self.all_skills()}
        self.assertNotIn(name, names)
