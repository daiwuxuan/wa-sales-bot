"""数据库引擎与会话。

这个文件是「切 Postgres 不用改业务代码」承诺的落点。全项目只有这里
知道底层是 SQLite 还是 Postgres。
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.core.config import settings


class Base(DeclarativeBase):
    pass


def _sqlite_on_connect(dbapi_conn, _record) -> None:
    """SQLite 连接初始化。

    pysqlite 有两个必须处理的默认行为：
      1. 它在 DML 之前才隐式 BEGIN（deferred），两个事务可以先各自读到 qty=1，
         再先后写成 qty=0 和 qty=-1 —— 这就是超卖。
         关掉隐式 BEGIN（isolation_level=None），改由下面的 begin 钩子接管。
      2. 外键约束默认是关闭的。不开的话 REFERENCES 只是注释。
    """
    dbapi_conn.isolation_level = None
    cur = dbapi_conn.cursor()
    cur.execute("PRAGMA journal_mode=WAL")       # 1 写 + N 读，读者不被写者阻塞
    cur.execute("PRAGMA busy_timeout=5000")      # 拿不到锁就等 5s，而不是立刻 SQLITE_BUSY
    cur.execute("PRAGMA foreign_keys=ON")        # 默认关闭，必须显式打开
    cur.execute("PRAGMA synchronous=NORMAL")     # WAL 下的安全/性能折中
    cur.close()


def _sqlite_on_begin(conn) -> None:
    """事务的第一个语句就取得 RESERVED 写锁。

    SQLite 没有 SELECT ... FOR UPDATE。BEGIN IMMEDIATE 把并发写事务在
    数据库层直接串行化，这是防超卖的第一道墙。第二道是 inventory 的条件
    UPDATE（WHERE qty >= n），第三道是 CHECK 约束。
    """
    conn.exec_driver_sql("BEGIN IMMEDIATE")


def create_db_engine(url: str) -> Engine:
    if url.startswith("sqlite"):
        engine = create_engine(
            url,
            future=True,
            # 同步依赖跑在 FastAPI 的线程池里，连接会跨线程复用
            connect_args={"check_same_thread": False},
        )
        event.listens_for(engine, "connect")(_sqlite_on_connect)
        event.listens_for(engine, "begin")(_sqlite_on_begin)
        return engine

    # Postgres：写事务的互斥靠 services/inventory.lock_variants() 里的
    # SELECT ... FOR UPDATE 显式获取。这里不需要额外钩子。
    return create_engine(url, future=True, pool_pre_ping=True)


engine = create_db_engine(settings.database_url)

SessionLocal = sessionmaker(
    bind=engine,
    autoflush=False,
    # commit 之后仍然能读对象属性 —— 否则返回给路由的 ORM 对象会触发
    # 一次「会话已关闭」的懒加载，在响应序列化时才炸。
    expire_on_commit=False,
)


def get_db() -> Iterator[Session]:
    """FastAPI 依赖。

    注意：路由必须写成同步的 `def` 而不是 `async def`。FastAPI 会把同步
    路由丢进线程池执行；写成 async def 再调用同步 ORM 会阻塞整个事件循环，
    AI 调用的几秒内所有请求（包括静态图片）全部无响应。
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@contextmanager
def session_scope() -> Iterator[Session]:
    """给脚本、后台任务、测试用的事务边界。"""
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
