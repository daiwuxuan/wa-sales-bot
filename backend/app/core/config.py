"""集中配置。所有密钥只在这里出现，只被后端进程读取。"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

# <项目根>/backend/app/core/config.py -> parents[2] == <项目根>/backend
BACKEND_DIR = Path(__file__).resolve().parents[2]
PROJECT_ROOT = BACKEND_DIR.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=BACKEND_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # 数据库用绝对路径。相对路径会被工作目录影响，
    # 而 uvicorn / pytest / seed 脚本的工作目录并不一致。
    database_url: str = f"sqlite:///{(PROJECT_ROOT / 'data' / 'app.db').as_posix()}"

    openai_api_key: SecretStr | None = None
    openai_model: str = "gpt-4o"
    llm_mode: str = "auto"

    public_base_url: str = "http://localhost:8000"
    admin_token: str = ""

    auto_order_max_cents: int = 200_000
    quote_ttl_minutes: int = 30

    # 商品图上传
    storage_dir: Path = BACKEND_DIR / "storage"
    upload_dir: Path = BACKEND_DIR / "storage" / "uploads"
    max_upload_bytes: int = 5 * 1024 * 1024

    # AI 循环硬上限：防止模型原地打转烧钱
    max_agent_steps: int = 6
    # 每次带进上下文的历史消息条数
    history_limit: int = 20

    @property
    def llm_should_use_openai(self) -> bool:
        """唯一的模式决策点。其余代码一律通过 ai.factory.get_llm_client() 取客户端。"""
        if self.llm_mode == "mock":
            return False
        if self.llm_mode == "openai":
            return True
        return self.openai_api_key is not None and bool(
            self.openai_api_key.get_secret_value().strip()
        )

    def public_url_for(self, key: str | None) -> str | None:
        """把库里存的相对 key 拼成绝对 URL。"""
        if not key:
            return None
        if key.startswith(("http://", "https://")):
            return key
        return f"{self.public_base_url.rstrip('/')}/{key.lstrip('/')}"


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
