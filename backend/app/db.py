from __future__ import annotations

import datetime as dt
from typing import Optional

from sqlalchemy import String, Text, DateTime, Integer, Float, Boolean, ForeignKey, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from .config import settings

engine = create_async_engine(settings.database_url, echo=False)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


def now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


class Base(DeclarativeBase):
    pass


class Setting(Base):
    """Key/value runtime configuration, editable from the UI."""
    __tablename__ = "settings"
    key: Mapped[str] = mapped_column(String(80), primary_key=True)
    value: Mapped[str] = mapped_column(Text, default="")


class Program(Base):
    __tablename__ = "programs"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    platform: Mapped[str] = mapped_column(String(50), default="manual")  # manual | hackerone
    handle: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    policy: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    scope_in: Mapped[str] = mapped_column(Text, default="[]")
    scope_out: Mapped[str] = mapped_column(Text, default="[]")
    program_type: Mapped[str] = mapped_column(String(20), default="unknown")  # bounty|vdp|unknown; legacy bbp is read as bounty
    icon_url: Mapped[str] = mapped_column(String(500), default="")
    # last known scope hash, to detect scope changes for alerting
    scope_hash: Mapped[str] = mapped_column(String(80), default="")
    scope_sync_status: Mapped[str] = mapped_column(String(30), default="never")
    scope_sync_error: Mapped[str] = mapped_column(Text, default="")
    scope_sync_warnings: Mapped[str] = mapped_column(Text, default="[]")
    scope_assets_json: Mapped[str] = mapped_column(Text, default="[]")
    scope_sync_started_at: Mapped[Optional[dt.datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    scope_synced_at: Mapped[Optional[dt.datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    scope_asset_count: Mapped[int] = mapped_column(Integer, default=0)
    scope_web_asset_count: Mapped[int] = mapped_column(Integer, default=0)
    scope_sync_attempts: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=now)


class Task(Base):
    __tablename__ = "tasks"
    id: Mapped[int] = mapped_column(primary_key=True)
    program_id: Mapped[Optional[int]] = mapped_column(ForeignKey("programs.id"), nullable=True)
    target: Mapped[str] = mapped_column(String(500))
    objective: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(30), default="queued")  # queued|running|done|error|stopped
    provider: Mapped[str] = mapped_column(String(40), default="")
    model: Mapped[str] = mapped_column(String(100), default="")
    tokens_in: Mapped[int] = mapped_column(Integer, default=0)
    tokens_out: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    steps: Mapped[int] = mapped_column(Integer, default=0)
    checkpoint: Mapped[str] = mapped_column(Text, default='')
    effort: Mapped[str] = mapped_column(String(20), default='')
    stop_reason: Mapped[str] = mapped_column(String(120), default='')
    routine_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    unknown_price: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=now)
    finished_at: Mapped[Optional[dt.datetime]] = mapped_column(DateTime(timezone=True), nullable=True)


class Event(Base):
    __tablename__ = "events"
    id: Mapped[int] = mapped_column(primary_key=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("tasks.id"), index=True)
    kind: Mapped[str] = mapped_column(String(40), index=True)
    title: Mapped[str] = mapped_column(String(300), default="")
    body: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=now)


class Endpoint(Base):
    __tablename__ = "endpoints"
    id: Mapped[int] = mapped_column(primary_key=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("tasks.id"), index=True)
    method: Mapped[str] = mapped_column(String(10), default="GET")
    url: Mapped[str] = mapped_column(String(1000))
    source: Mapped[str] = mapped_column(String(100), default="")
    notes: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=now)


class EndpointSpec(Base):
    __tablename__ = 'endpoint_specs'
    id: Mapped[int] = mapped_column(primary_key=True)
    task_id: Mapped[int] = mapped_column(ForeignKey('tasks.id'), index=True)
    url: Mapped[str] = mapped_column(Text)
    method: Mapped[str] = mapped_column(String(20), default='GET')
    source: Mapped[str] = mapped_column(Text, default='')
    shape: Mapped[str] = mapped_column(Text, default='{}')


class Finding(Base):
    __tablename__ = "findings"
    id: Mapped[int] = mapped_column(primary_key=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("tasks.id"), index=True)
    title: Mapped[str] = mapped_column(String(300))
    severity: Mapped[str] = mapped_column(String(20), default="info")
    status: Mapped[str] = mapped_column(String(20), default="new")  # new|confirmed|duplicate|fixed|reported
    summary: Mapped[str] = mapped_column(Text, default="")
    report_md: Mapped[str] = mapped_column(Text, default="")
    # diff intelligence: relation to a prior finding
    relation: Mapped[str] = mapped_column(String(20), default="")  # same|new|regressed|bypass
    related_to: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    evidence_ids: Mapped[str] = mapped_column(Text, default='[]')
    verification_revision: Mapped[int] = mapped_column(Integer, default=0)
    evidence_score: Mapped[int] = mapped_column(Integer, default=0)  # 0-100 evidence quality
    # verification + reporting
    confidence: Mapped[int] = mapped_column(Integer, default=0)  # 0-100
    verified: Mapped[bool] = mapped_column(Boolean, default=False)
    verdict: Mapped[str] = mapped_column(Text, default="")  # verifier reasoning
    weakness: Mapped[str] = mapped_column(String(120), default="")  # e.g. IDOR/BOLA
    h1_report_id: Mapped[str] = mapped_column(String(40), default="")
    h1_report_url: Mapped[str] = mapped_column(String(300), default="")
    submitted_at: Mapped[Optional[dt.datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=now)


class Lead(Base):
    """A promising-but-unconfirmed signal the agent wants to revisit."""
    __tablename__ = "leads"
    id: Mapped[int] = mapped_column(primary_key=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("tasks.id"), index=True)
    title: Mapped[str] = mapped_column(String(300))
    url: Mapped[str] = mapped_column(String(1000), default="")
    severity_guess: Mapped[str] = mapped_column(String(20), default="info")
    rationale: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(20), default="open")  # open|chasing|closed|promoted
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=now)


class Addendum(Base):
    """Follow-up attached to a finding: triager reply, retest, or bypass write-up."""
    __tablename__ = "addenda"
    id: Mapped[int] = mapped_column(primary_key=True)
    finding_id: Mapped[int] = mapped_column(ForeignKey("findings.id"), index=True)
    task_id: Mapped[Optional[int]] = mapped_column(ForeignKey("tasks.id"), nullable=True)
    kind: Mapped[str] = mapped_column(String(20), default="comment")  # comment|retest|bypass|poc
    body_md: Mapped[str] = mapped_column(Text, default="")
    posted: Mapped[bool] = mapped_column(Boolean, default=False)  # marked posted to the report
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=now)


class SubAgent(Base):
    """A specialist sub-task the lead agent delegated."""
    __tablename__ = "subagents"
    id: Mapped[int] = mapped_column(primary_key=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("tasks.id"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    role: Mapped[str] = mapped_column(String(120), default="")
    status: Mapped[str] = mapped_column(String(20), default="spawned")  # spawned|working|done
    summary: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=now)


class RepeaterRequest(Base):
    __tablename__ = "repeater_requests"
    id: Mapped[int] = mapped_column(primary_key=True)
    task_id: Mapped[Optional[int]] = mapped_column(ForeignKey("tasks.id"), nullable=True, index=True)
    method: Mapped[str] = mapped_column(String(10), default="GET")
    url: Mapped[str] = mapped_column(String(1000))
    request_headers: Mapped[str] = mapped_column(Text, default="{}")
    request_body: Mapped[str] = mapped_column(Text, default="")
    status_code: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    response_headers: Mapped[str] = mapped_column(Text, default="{}")
    response_body: Mapped[str] = mapped_column(Text, default="")
    duration_ms: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=now)


class Routine(Base):
    """A scheduled recurring scan."""
    __tablename__ = "routines"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    target: Mapped[str] = mapped_column(String(500))
    objective: Mapped[str] = mapped_column(Text, default="")
    program_id: Mapped[Optional[int]] = mapped_column(ForeignKey("programs.id"), nullable=True)
    interval_minutes: Mapped[int] = mapped_column(Integer, default=1440)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    last_run: Mapped[Optional[dt.datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=now)


class Notification(Base):
    __tablename__ = "notifications"
    id: Mapped[int] = mapped_column(primary_key=True)
    task_id: Mapped[Optional[int]] = mapped_column(ForeignKey("tasks.id"), nullable=True)
    level: Mapped[str] = mapped_column(String(20), default="info")  # info|success|warn|critical
    title: Mapped[str] = mapped_column(String(300))
    body: Mapped[str] = mapped_column(Text, default="")
    read: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=now)


class Idea(Base):
    """A hypothesis the agent queues and later pulls from (prioritized worklist)."""
    __tablename__ = "ideas"
    id: Mapped[int] = mapped_column(primary_key=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("tasks.id"), index=True)
    text: Mapped[str] = mapped_column(Text, default="")
    priority: Mapped[int] = mapped_column(Integer, default=3)  # 1 (hottest) – 5
    status: Mapped[str] = mapped_column(String(20), default="queued")  # queued|chasing|done|dropped
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=now)


class GraphNode(Base):
    __tablename__ = "graph_nodes"
    id: Mapped[int] = mapped_column(primary_key=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("tasks.id"), index=True)
    kind: Mapped[str] = mapped_column(String(40), default="node")  # host|endpoint|param|identity|token|tech|finding|object
    key: Mapped[str] = mapped_column(String(400), index=True)  # natural key for dedup
    label: Mapped[str] = mapped_column(String(400), default="")
    attrs: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=now)


class GraphEdge(Base):
    __tablename__ = "graph_edges"
    id: Mapped[int] = mapped_column(primary_key=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("tasks.id"), index=True)
    src_id: Mapped[int] = mapped_column(ForeignKey("graph_nodes.id"))
    dst_id: Mapped[int] = mapped_column(ForeignKey("graph_nodes.id"))
    rel: Mapped[str] = mapped_column(String(40), default="relates_to")  # hosts|calls|auth_by|references|exposes|idor_to
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=now)


class Asset(Base):
    __tablename__ = "assets"
    id: Mapped[int] = mapped_column(primary_key=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("tasks.id"), index=True)
    program_id: Mapped[Optional[int]] = mapped_column(ForeignKey("programs.id"), nullable=True)
    host: Mapped[str] = mapped_column(String(300), index=True)
    kind: Mapped[str] = mapped_column(String(20), default="host")  # domain|subdomain|ip|service|host
    tech: Mapped[str] = mapped_column(Text, default="[]")  # JSON list
    status: Mapped[str] = mapped_column(String(20), default="unknown")  # live|dead|unknown
    source: Mapped[str] = mapped_column(String(80), default="")
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=now)


class TokenUsage(Base):
    __tablename__ = "token_usage"
    id: Mapped[int] = mapped_column(primary_key=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("tasks.id"), index=True)
    provider: Mapped[str] = mapped_column(String(40), default="")
    model: Mapped[str] = mapped_column(String(100), default="")
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=now)


class Identity(Base):
    """A test identity (operator-supplied, or provisioned via a bounded, guarded signup) used to
    prove access-control bugs by replaying requests as a different principal."""
    __tablename__ = "identities"
    id: Mapped[int] = mapped_column(primary_key=True)
    program_id: Mapped[Optional[int]] = mapped_column(ForeignKey("programs.id"), nullable=True, index=True)
    label: Mapped[str] = mapped_column(String(80))  # e.g. userA / userB
    auth_type: Mapped[str] = mapped_column(String(20), default="cookie")  # cookie|bearer|header
    header_name: Mapped[str] = mapped_column(String(80), default="")  # for auth_type=header
    material: Mapped[str] = mapped_column(Text, default="")  # the secret (cookie/token value) — masked in API
    username: Mapped[str] = mapped_column(String(120), default="")
    note: Mapped[str] = mapped_column(String(300), default="")
    source: Mapped[str] = mapped_column(String(20), default="operator")  # operator|provisioned
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=now)


class Memory(Base):
    """Durable facts the agent learns, recalled across scans. Scope ties a memory to a
    program/target so recon knowledge compounds over time."""
    __tablename__ = "memories"
    id: Mapped[int] = mapped_column(primary_key=True)
    scope: Mapped[str] = mapped_column(String(80), default="global", index=True)  # global | host:<host> | program:<id>
    category: Mapped[str] = mapped_column(String(60), default="note")  # tech|auth|endpoint|behavior|note
    content: Mapped[str] = mapped_column(Text, default="")
    task_id: Mapped[Optional[int]] = mapped_column(ForeignKey("tasks.id"), nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=now)


class Skill(Base):
    """A reusable pentest playbook the agent loads for guidance."""
    __tablename__ = "skills"
    name: Mapped[str] = mapped_column(String(120), primary_key=True)
    category: Mapped[str] = mapped_column(String(60), default="web")
    summary: Mapped[str] = mapped_column(String(300), default="")
    playbook: Mapped[str] = mapped_column(Text, default="")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    # custom-skills + depth
    payloads_json: Mapped[str] = mapped_column(Text, default="")
    bypass_table_md: Mapped[str] = mapped_column(Text, default="")
    references_md: Mapped[str] = mapped_column(Text, default="")
    tags: Mapped[str] = mapped_column(String(300), default="")
    source: Mapped[str] = mapped_column(String(80), default="builtin")  # builtin|custom
    filename: Mapped[str] = mapped_column(String(200), default="")
    is_custom: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=now)


class Intel(Base):
    """Extracted intelligence: params, secrets, identifiers, tech found during recon."""
    __tablename__ = "intel"
    id: Mapped[int] = mapped_column(primary_key=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("tasks.id"), index=True)
    kind: Mapped[str] = mapped_column(String(40), default="note")  # param|secret|token|id|email|tech|header
    value: Mapped[str] = mapped_column(Text, default="")
    source: Mapped[str] = mapped_column(String(300), default="")
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=now)


class ChatMessage(Base):
    """Chat threads: triager replies, retests, and a free General mode."""
    __tablename__ = "chat_messages"
    id: Mapped[int] = mapped_column(primary_key=True)
    thread: Mapped[str] = mapped_column(String(120), index=True)  # e.g. "general" or "finding:12"
    mode: Mapped[str] = mapped_column(String(20), default="general")  # general|triage|retest
    role: Mapped[str] = mapped_column(String(20), default="user")  # user|assistant
    content: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=now)


# Generic migration: for every model, add any column the live SQLite table is missing.
# This covers upgrades from any older ReconHawk database without a hardcoded per-column
# list (which silently falls out of date as the schema grows).
_SQLITE_TYPE_MAP = {
    "VARCHAR": "VARCHAR", "STRING": "VARCHAR", "TEXT": "TEXT",
    "INTEGER": "INTEGER", "BOOLEAN": "BOOLEAN", "DATETIME": "DATETIME", "FLOAT": "FLOAT",
}


def _column_ddl(col) -> str:
    """Render a SQLAlchemy Column's SQLite type + a safe default, for ALTER TABLE ADD COLUMN."""
    type_name = type(col.type).__name__.upper()
    sqlite_type = _SQLITE_TYPE_MAP.get(type_name, "TEXT")
    if type_name == "DATETIME":
        return sqlite_type  # nullable, no default — timestamps shouldn't get a fake value
    if type_name == "BOOLEAN":
        default = "0"
    elif type_name in ("INTEGER", "FLOAT"):
        default = "0"
    else:
        default = "''"
    if col.default is not None and col.default.is_scalar:
        value = col.default.arg
        default = str(int(value)) if isinstance(value,bool) else (str(value) if isinstance(value,(int,float)) else "'" + str(value).replace("'","''") + "'")
    return f"{sqlite_type} DEFAULT {default}"


def _migrate_sync(conn) -> None:
    for table_name, table in Base.metadata.tables.items():
        rows = conn.exec_driver_sql(f"PRAGMA table_info({table_name})").fetchall()
        if not rows:
            continue  # table doesn't exist yet — create_all already handled it
        existing = {row[1] for row in rows}
        for col in table.columns:
            if col.name not in existing:
                conn.exec_driver_sql(f"ALTER TABLE {table_name} ADD COLUMN {col.name} {_column_ddl(col)}")


async def init_db() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.run_sync(_migrate_sync)
