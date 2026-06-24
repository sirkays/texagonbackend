import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'texagonbackend.settings')
django.setup()

from django.db import connection

with connection.cursor() as cursor:
    try:
        cursor.execute("ALTER TABLE store_product DROP COLUMN is_digital CASCADE;")
        print("Dropped is_digital column successfully.")
    except Exception as e:
        print(f"Error dropping column: {e}")
