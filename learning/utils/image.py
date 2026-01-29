# utils/image.py
from PIL import Image, UnidentifiedImageError
from io import BytesIO
from django.core.files.base import ContentFile
import os
import uuid

def convert_to_webp(image_field, max_size=(1200, 1200), quality=80):
    """
    Converts an uploaded image to WEBP with a short filename.
    Safe for S3/Cloudinary: resets pointer, catches Pillow errors, and falls back.
    """
    if not image_field:
        return image_field

    # If already .webp, just keep it
    name = (getattr(image_field, "name", "") or "").lower()
    if name.endswith(".webp"):
        return image_field

    # Reset file pointer if possible (important for uploads)
    try:
        image_field.seek(0)
    except Exception:
        pass

    try:
        img = Image.open(image_field)
        img.load()  # force decode to catch corrupt/unreadable images early
    except (UnidentifiedImageError, OSError, ValueError):
        # Not a valid image / unreadable -> do not crash the request
        return image_field

    # Handle alpha correctly (PNG with transparency, etc.)
    if img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info):
        background = Image.new("RGB", img.size, (255, 255, 255))
        img = img.convert("RGBA")
        background.paste(img, mask=img.split()[-1])
        img = background
    else:
        img = img.convert("RGB")

    img.thumbnail(max_size, Image.LANCZOS)

    buffer = BytesIO()
    img.save(buffer, format="WEBP", quality=quality, method=6)
    buffer.seek(0)

    # Keep folder, shorten filename
    original_path = getattr(image_field, "name", "") or ""
    directory = os.path.dirname(original_path)

    short_name = f"{uuid.uuid4().hex[:12]}.webp"
    new_name = os.path.join(directory, short_name) if directory else short_name

    return ContentFile(buffer.read(), name=new_name)
