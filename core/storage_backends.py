from django.conf import settings
from django.core.files.storage import storages

def lesson_file_storage():
    # If S3 is active, use default (which is S3)
    if getattr(settings, "IS_S3", False):
        return storages["default"]
    # Otherwise use Cloudinary Raw storage
    return storages["cloudinary_raw"]


def dynamic_storage():
    """
    Use S3 when IS_S3=True, otherwise use Cloudinary.
    """
    if getattr(settings, "IS_S3", False):
        return storages["default"]   # S3
    return storages["cloudinary"]   # Cloudinary