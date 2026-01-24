# utils/image.py
from PIL import Image
from io import BytesIO
from django.core.files.base import ContentFile
import os

def convert_to_webp(image_field, max_size=(1200, 1200), quality=80):
    img = Image.open(image_field)
    img = img.convert("RGB")  # WebP needs RGB

    # Resize (keeps aspect ratio)
    img.thumbnail(max_size, Image.LANCZOS)

    buffer = BytesIO()
    img.save(buffer, format="WEBP", quality=quality)
    buffer.seek(0)

    file_name = os.path.splitext(image_field.name)[0] + ".webp"
    return ContentFile(buffer.read(), name=file_name)
