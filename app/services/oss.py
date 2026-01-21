"""
Alibaba Cloud OSS Service

Provides STS-based temporary credentials for direct upload from mobile apps.
"""
from datetime import datetime, timezone
from typing import Optional
import uuid

from app.config import settings


class OSSService:
    """OSS Service for file uploads"""

    def __init__(self):
        self._sts_client = None

    def _get_sts_client(self):
        """Lazy initialization of STS client"""
        if self._sts_client is None:
            if not settings.oss_access_key_id or not settings.oss_access_key_secret:
                return None

            try:
                from alibabacloud_tea_openapi.models import Config
                from alibabacloud_sts20150401.client import Client as StsClient

                config = Config(
                    access_key_id=settings.oss_access_key_id,
                    access_key_secret=settings.oss_access_key_secret,
                    region_id=settings.oss_region_id,
                )
                self._sts_client = StsClient(config)
            except ImportError:
                return None

        return self._sts_client

    def generate_file_key(self, file_type: str, file_ext: str) -> str:
        """Generate a unique file key for OSS storage"""
        today = datetime.now(timezone.utc).strftime("%Y/%m/%d")
        file_id = uuid.uuid4().hex[:12]
        return f"{file_type}/{today}/{file_id}.{file_ext}"

    def get_file_url(self, file_key: str) -> str:
        """Get the CDN URL for a file"""
        if settings.oss_cdn_domain:
            return f"https://{settings.oss_cdn_domain}/{file_key}"
        return f"https://{settings.oss_bucket_name}.{settings.oss_endpoint}/{file_key}"

    def get_upload_url(self) -> str:
        """Get the OSS upload endpoint URL"""
        return f"https://{settings.oss_bucket_name}.{settings.oss_endpoint}"

    async def get_sts_token(self, duration_seconds: int = 3600) -> Optional[dict]:
        """
        Get STS temporary credentials for OSS upload

        Args:
            duration_seconds: Token validity in seconds (900 to 3600)

        Returns:
            Dictionary with AccessKeyId, AccessKeySecret, SecurityToken, Expiration
            or None if STS is not configured
        """
        if not settings.oss_role_arn:
            return None

        sts_client = self._get_sts_client()
        if not sts_client:
            return None

        try:
            from alibabacloud_sts20150401 import models as sts_models

            request = sts_models.AssumeRoleRequest(
                role_arn=settings.oss_role_arn,
                role_session_name=f"upload_{uuid.uuid4().hex[:8]}",
                duration_seconds=duration_seconds,
            )

            response = sts_client.assume_role(request)
            credentials = response.body.credentials

            return {
                "access_key_id": credentials.access_key_id,
                "access_key_secret": credentials.access_key_secret,
                "security_token": credentials.security_token,
                "expiration": credentials.expiration,
            }
        except Exception as e:
            print(f"Error getting STS token: {e}")
            return None

    async def get_upload_credentials(
        self,
        file_type: str,
        file_ext: str,
    ) -> dict:
        """
        Get complete upload credentials for client

        Args:
            file_type: Type of file (image/audio/video)
            file_ext: File extension (jpg/png/mp3 etc.)

        Returns:
            Dictionary with upload_url, file_key, file_url, and STS credentials
        """
        file_key = self.generate_file_key(file_type, file_ext)
        file_url = self.get_file_url(file_key)
        upload_url = self.get_upload_url()

        # Get STS token
        sts_token = await self.get_sts_token()

        result = {
            "upload_url": upload_url,
            "file_key": file_key,
            "file_url": file_url,
            "bucket": settings.oss_bucket_name,
            "region": settings.oss_region_id,
        }

        if sts_token:
            result.update({
                "access_key_id": sts_token["access_key_id"],
                "access_key_secret": sts_token["access_key_secret"],
                "security_token": sts_token["security_token"],
                "expiration": sts_token["expiration"],
            })
        else:
            # Fallback: return placeholder (client should handle this case)
            result.update({
                "access_key_id": None,
                "access_key_secret": None,
                "security_token": None,
                "expiration": None,
            })

        return result


# Global service instance
oss_service = OSSService()
