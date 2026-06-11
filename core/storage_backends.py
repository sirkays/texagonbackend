from django.conf import settings
from django.core.files.storage import storages

def lesson_file_storage():
    if getattr(settings, "IS_S3", False):
        return storages["default"]       # S3
    if getattr(settings, "IS_LOCAL", False):
        return storages["default"]       # FileSystemStorage (local dev)
    return storages["cloudinary_raw"]    # Cloudinary (production non-S3)


def dynamic_storage():
    """
    Returns the appropriate storage backend based on environment.
    - S3 when IS_S3=True (production with S3)
    - FileSystemStorage when IS_LOCAL=True (local dev)
    - Cloudinary otherwise (production with Cloudinary)
    """
    if getattr(settings, "IS_S3", False):
        return storages["default"]       # S3
    if getattr(settings, "IS_LOCAL", False):
        return storages["default"]       # FileSystemStorage (local dev)
    return storages["cloudinary"]        # Cloudinary