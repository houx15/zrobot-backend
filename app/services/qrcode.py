"""
QR Code Generation Service

Generates QR codes for parent-student binding.
"""
import io
import base64
import uuid
from datetime import datetime, timezone

import qrcode
from qrcode.image.styledpil import StyledPilImage
from qrcode.image.styles.moduledrawers import RoundedModuleDrawer

from app.config import settings
from app.services.oss import oss_service


class QRCodeService:
    """QR Code generation service"""

    def generate_qrcode_image(self, data: str) -> bytes:
        """
        Generate a QR code image

        Args:
            data: The data to encode in the QR code

        Returns:
            PNG image bytes
        """
        qr = qrcode.QRCode(
            version=1,
            error_correction=qrcode.constants.ERROR_CORRECT_M,
            box_size=10,
            border=4,
        )
        qr.add_data(data)
        qr.make(fit=True)

        # Create styled QR code
        img = qr.make_image(
            image_factory=StyledPilImage,
            module_drawer=RoundedModuleDrawer(),
            fill_color="black",
            back_color="white",
        )

        # Convert to bytes
        buffer = io.BytesIO()
        img.save(buffer, format="PNG")
        buffer.seek(0)
        return buffer.getvalue()

    def generate_qrcode_base64(self, data: str) -> str:
        """
        Generate a QR code and return as base64 data URL

        Args:
            data: The data to encode

        Returns:
            Base64 data URL string (data:image/png;base64,...)
        """
        image_bytes = self.generate_qrcode_image(data)
        base64_data = base64.b64encode(image_bytes).decode("utf-8")
        return f"data:image/png;base64,{base64_data}"

    async def generate_and_upload_qrcode(
        self,
        data: str,
        qrcode_id: str,
    ) -> str:
        """
        Generate a QR code and upload to OSS

        Args:
            data: The data to encode
            qrcode_id: Unique ID for the QR code

        Returns:
            URL of the uploaded QR code image
        """
        # Generate QR code image
        image_bytes = self.generate_qrcode_image(data)

        # Generate file key
        today = datetime.now(timezone.utc).strftime("%Y/%m/%d")
        file_key = f"qrcode/{today}/{qrcode_id}.png"

        # Upload to OSS
        # Note: For production, you'd use OSS SDK to upload
        # For now, we'll return a placeholder URL or use base64

        # If OSS is configured, upload the image
        if settings.oss_bucket_name and settings.oss_access_key_id:
            try:
                import oss2

                auth = oss2.Auth(
                    settings.oss_access_key_id,
                    settings.oss_access_key_secret,
                )
                bucket = oss2.Bucket(
                    auth,
                    f"https://{settings.oss_endpoint}",
                    settings.oss_bucket_name,
                )

                # Upload the image
                bucket.put_object(file_key, image_bytes)

                # Return the CDN URL
                return oss_service.get_file_url(file_key)
            except Exception as e:
                print(f"Failed to upload QR code to OSS: {e}")
                # Fall back to base64
                return self.generate_qrcode_base64(data)
        else:
            # Return base64 data URL if OSS is not configured
            return self.generate_qrcode_base64(data)


# Global service instance
qrcode_service = QRCodeService()
