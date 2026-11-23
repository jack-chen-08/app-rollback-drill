"""Sample member-space API used by the rollback drill.

Two image tags are built from this single source:
  v1 - healthy release (``/health`` returns 200)
  v2 - intentionally defective release (``/health`` returns 500)

Everything environment driven; no address, account or secret is hardcoded.
All seeded data is fictional.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from typing import Iterator

import redis
from fastapi import Depends, FastAPI, Header, HTTPException, Query
from pydantic import BaseModel
from pgvector.sqlalchemy import Vector
from sqlalchemy import DateTime, Float, Integer, String, Text, create_engine, func, select, text
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

DATABASE_URL = os.environ["DATABASE_URL"]
REDIS_URL = os.environ["REDIS_URL"]
REDIS_PREFIX = os.environ.get("REDIS_PREFIX", "member-space:")
APP_SECRET = os.environ["APP_SECRET"].encode()
APP_VERSION = os.environ.get("APP_VERSION", "unknown")
HEALTH_FORCE_STATUS = int(os.environ.get("HEALTH_FORCE_STATUS", "200"))
EMBED_DIM = 32
TOKEN_TTL_SECONDS = 8 * 3600

engine = create_engine(DATABASE_URL, pool_pre_ping=True, pool_size=5, max_overflow=5)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)
cache = redis.Redis.from_url(REDIS_URL, decode_responses=True, socket_connect_timeout=2)


class Base(DeclarativeBase):
    pass


class Member(Base):
    __tablename__ = "members"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    org_id: Mapped[str] = mapped_column(String(36), index=True)
    site_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    name: Mapped[str] = mapped_column(String(64))
    phone: Mapped[str] = mapped_column(String(20))
    level: Mapped[str] = mapped_column(String(16))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    created_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Activity(Base):
    __tablename__ = "activities"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    org_id: Mapped[str] = mapped_column(String(36), index=True)
    site_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    title: Mapped[str] = mapped_column(String(128))
    starts_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    capacity: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    created_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class KbDoc(Base):
    __tablename__ = "kb_docs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    org_id: Mapped[str] = mapped_column(String(36), index=True)
    site_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    title: Mapped[str] = mapped_column(String(128))
    content: Mapped[str] = mapped_column(Text)
    score: Mapped[float] = mapped_column(Float, default=0.0)
    embedding: Mapped[list[float]] = mapped_column(Vector(EMBED_DIM))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    created_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class User(Base):
    __tablename__ = "users"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    org_id: Mapped[str] = mapped_column(String(36), index=True)
    username: Mapped[str] = mapped_column(String(64), unique=True)
    password_hash: Mapped[str] = mapped_column(String(128))
    salt: Mapped[str] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    created_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


def embed(text_value: str) -> list[float]:
    """Deterministic placeholder embedding (no external model, no data egress)."""
    vec = [0.0] * EMBED_DIM
    for token in text_value.lower().split() or [text_value.lower()]:
        digest = hashlib.sha256(token.encode()).digest()
        for i in range(EMBED_DIM):
            vec[i] += digest[i] / 255.0 - 0.5
    norm = sum(v * v for v in vec) ** 0.5 or 1.0
    return [v / norm for v in vec]


def hash_password(password: str, salt: str) -> str:
    return hashlib.sha256(f"{salt}:{password}".encode()).hexdigest()


def make_token(user_id: str) -> str:
    expires = int(time.time()) + TOKEN_TTL_SECONDS
    payload = f"{user_id}:{expires}"
    sig = hmac.new(APP_SECRET, payload.encode(), hashlib.sha256).hexdigest()[:32]
    return f"{payload}:{sig}"


def seed(session: Session) -> None:
    org_id = os.environ.get("SEED_ORG_ID", "00000000-0000-0000-0000-000000000001")
    username = os.environ.get("SEED_USERNAME", "")
    password = os.environ.get("SEED_PASSWORD", "")

    if username and not session.scalar(select(User).where(User.username == username)):
        salt = uuid.uuid4().hex[:12]
        session.add(
            User(
                id=str(uuid.uuid4()),
                org_id=org_id,
                username=username,
                salt=salt,
                password_hash=hash_password(password, salt),
            )
        )

    if not session.scalar(select(func.count()).select_from(Member).where(Member.org_id == org_id)):
        for idx in range(1, 6):
            session.add(
                Member(
                    id=str(uuid.uuid4()),
                    org_id=org_id,
                    name=f"测试会员{idx}",
                    phone=f"138000000{idx:02d}",
                    level="gold" if idx % 2 == 0 else "silver",
                )
            )

    if not session.scalar(select(func.count()).select_from(Activity).where(Activity.org_id == org_id)):
        session.add(
            Activity(
                id=os.environ.get("SEED_ACTIVITY_ID", "11111111-1111-1111-1111-111111111111"),
                org_id=org_id,
                title="产业资源对接沙龙（虚构）",
                starts_at=datetime.now(timezone.utc) + timedelta(days=7),
                capacity=60,
            )
        )

    if not session.scalar(select(func.count()).select_from(KbDoc).where(KbDoc.org_id == org_id)):
        docs = [
            ("会员权益说明", "会员可预约场地、参加产业资源对接活动并申请商机推荐。"),
            ("资源对接流程", "提交需求后由运营在三个工作日内匹配资源方并安排线上会谈。"),
            ("知识库检索规范", "检索使用向量相似度，返回标题、摘要与相似度分数。"),
        ]
        for title, content in docs:
            session.add(
                KbDoc(
                    id=str(uuid.uuid4()),
                    org_id=org_id,
                    title=title,
                    content=content,
                    embedding=embed(f"{title} {content}"),
                )
            )
    session.commit()


@asynccontextmanager
async def lifespan(_: FastAPI):
    for attempt in range(30):
        try:
            with engine.begin() as conn:
                conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
            Base.metadata.create_all(engine)
            with SessionLocal() as session:
                seed(session)
            break
        except Exception as exc:  # noqa: BLE001 - startup retry loop
            if attempt == 29:
                raise
            print(f"database not ready ({exc.__class__.__name__}), retrying...", flush=True)
            time.sleep(2)
    yield


app = FastAPI(title="member-space sample api", version=APP_VERSION, lifespan=lifespan)


def get_session() -> Iterator[Session]:
    with SessionLocal() as session:
        yield session


def current_user(
    authorization: str | None = Header(default=None),
    session: Session = Depends(get_session),
) -> User:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="missing bearer token")
    token = authorization.split(" ", 1)[1]
    try:
        user_id, expires, sig = token.split(":")
    except ValueError:
        raise HTTPException(status_code=401, detail="malformed token") from None
    expected = hmac.new(APP_SECRET, f"{user_id}:{expires}".encode(), hashlib.sha256).hexdigest()[:32]
    if not hmac.compare_digest(sig, expected) or int(expires) < int(time.time()):
        raise HTTPException(status_code=401, detail="invalid or expired token")
    user = session.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=401, detail="unknown user")
    return user


@app.get("/health")
def health():
    if HEALTH_FORCE_STATUS != 200:
        raise HTTPException(status_code=HEALTH_FORCE_STATUS, detail="health check failed (drill defect)")
    return {"status": "ok"}


@app.get("/api/version")
def version():
    return {"version": APP_VERSION, "health_force_status": HEALTH_FORCE_STATUS}


class LoginRequest(BaseModel):
    username: str
    password: str


@app.post("/api/login")
def login(payload: LoginRequest, session: Session = Depends(get_session)):
    user = session.scalar(select(User).where(User.username == payload.username))
    if user is None or hash_password(payload.password, user.salt) != user.password_hash:
        raise HTTPException(status_code=401, detail="bad credentials")
    return {"token": make_token(user.id), "org_id": user.org_id}


@app.get("/api/members")
def members(
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
    limit: int = Query(default=10, ge=1, le=100),
):
    cache_key = f"{REDIS_PREFIX}members:{user.org_id}"
    try:
        cached = cache.get(cache_key)
        if cached:
            return {"source": "cache", "items": json.loads(cached)}
    except redis.RedisError:
        pass
    rows = session.scalars(
        select(Member)
        .where(Member.org_id == user.org_id, Member.deleted_at.is_(None))
        .order_by(Member.name)
        .limit(limit)
    ).all()
    items = [
        {"id": r.id, "name": r.name, "phone": r.phone, "level": r.level, "org_id": r.org_id}
        for r in rows
    ]
    try:
        cache.setex(cache_key, 300, json.dumps(items, ensure_ascii=False))
    except redis.RedisError:
        pass
    return {"source": "db", "items": items}


@app.get("/api/activities/{activity_id}")
def activity_detail(
    activity_id: str,
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
):
    row = session.get(Activity, activity_id)
    if row is None or row.org_id != user.org_id or row.deleted_at is not None:
        raise HTTPException(status_code=404, detail="activity not found")
    return {
        "id": row.id,
        "org_id": row.org_id,
        "title": row.title,
        "starts_at": row.starts_at.isoformat() if row.starts_at else None,
        "capacity": row.capacity,
    }


@app.get("/api/kb/search")
def kb_search(
    q: str = Query(min_length=1),
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
):
    vec = embed(q)
    rows = session.execute(
        select(
            KbDoc.title,
            KbDoc.content,
            KbDoc.embedding.cosine_distance(vec).label("distance"),
        )
        .where(KbDoc.org_id == user.org_id, KbDoc.deleted_at.is_(None))
        .order_by("distance")
        .limit(5)
    ).all()
    return {
        "query": q,
        "hits": [
            {"title": r.title, "snippet": r.content[:80], "score": round(1 - float(r.distance), 4)}
            for r in rows
        ],
    }
