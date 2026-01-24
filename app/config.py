from pydantic_settings import BaseSettings
from functools import lru_cache
from typing import Optional


class Settings(BaseSettings):
    # Application
    app_name: str = "AI-Learning-Tablet"
    app_env: str = "development"
    debug: bool = True
    secret_key: str = "your-secret-key-here"

    # Server
    host: str = "0.0.0.0"
    port: int = 8093

    # Database
    database_url: str = (
        "postgresql+asyncpg://postgres:password@localhost:5432/ai_learning_tablet"
    )

    # Redis
    redis_url: str = "redis://localhost:6379/0"

    # JWT
    jwt_secret_key: str = "your-jwt-secret-key"
    jwt_algorithm: str = "HS256"
    jwt_access_token_expire_minutes: int = 10080  # 7 days

    # Alibaba Cloud OSS
    oss_access_key_id: Optional[str] = None
    oss_access_key_secret: Optional[str] = None
    oss_bucket_name: Optional[str] = None
    oss_endpoint: str = "oss-cn-shenzhen.aliyuncs.com"
    oss_cdn_domain: Optional[str] = None
    oss_region_id: str = "cn-shenzhen"
    oss_role_arn: Optional[str] = None  # RAM Role ARN for STS

    # Zhipu AI
    zhipu_api_key: Optional[str] = None

    # ByteDance/Volcano Engine - ASR
    volc_app_id: Optional[str] = None
    volc_access_token: Optional[str] = None
    volc_cluster_id: Optional[str] = None

    # ByteDance/Volcano Engine - TTS
    volc_tts_voice_type: str = "zh_female_tianmeixiaoyuan_moon_bigtts"  # Default voice
    volc_tts_sample_rate: int = 24000
    volc_tts_resource_id: Optional[str] = None

    # Doubao LLM
    doubao_api_key: Optional[str] = None
    doubao_model_id: str = "ep-20250120163453-j7slq"  # Default model endpoint
    doubao_api_base_url: str = "https://ark.cn-beijing.volces.com/api/v3"

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        case_sensitive = False


@lru_cache()
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
