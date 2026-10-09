"""FastAPI 入口。"""

from __future__ import annotations

import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.api.routes import analytics, broadcast, chat, mock_webhook, orders, products, review
from app.core.config import settings

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(name)s | %(message)s",
)

app = FastAPI(
    title="WhatsApp AI 销售机器人",
    version="0.1.0",
    description="Mock 阶段的完整骨架。未真实对接 WhatsApp Cloud API。",
)

# 本地开发放开。生产要把这里换成具体域名。
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 商家上传的商品图。StaticFiles 只挂具体子目录，不挂项目根 ——
# 挂根目录等于把 .env 和数据库一起暴露出去。
settings.upload_dir.mkdir(parents=True, exist_ok=True)
app.mount("/uploads", StaticFiles(directory=str(settings.upload_dir)), name="uploads")

for router in (
    mock_webhook.router,
    chat.router,
    products.router,
    orders.router,
    analytics.router,
    review.router,
    broadcast.router,
):
    app.include_router(router)


@app.get("/api/health")
def health() -> dict:
    from app.ai.factory import current_mode

    return {
        "status": "ok",
        "llm": current_mode(),
        "database": settings.database_url.split("://")[0],
    }


@app.on_event("startup")
def _startup() -> None:
    from app.core.database import Base, engine
    import app.models  # noqa: F401  确保所有模型都被注册

    Base.metadata.create_all(engine)

    from app.ai.factory import current_mode

    mode = current_mode()
    logging.getLogger(__name__).info(
        "LLM 模式：%s（%s）", mode["mode"], mode["model"]
    )
