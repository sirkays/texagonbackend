import os
import django
import sys
from datetime import timedelta
from django.utils import timezone

# Setup django environment
sys.path.append(r"c:\Users\sirkays\Desktop\workspace\texagon_academy\techxagon_application\texagonbackend")
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "texagonbackend.settings")
django.setup()

from django.conf import settings
settings.ALLOWED_HOSTS = ['*']

from rest_framework.test import APIClient
from academics.models import TeacherProfile
from learning.models import Course
from assessments.models import Test
from api.models import SessionToken

def run_test():
    teacher_profile = TeacherProfile.objects.first()
    if not teacher_profile:
        print("No TeacherProfile found in database.")
        return
        
    user = teacher_profile.user
    org = teacher_profile.organization
    
    print(f"Testing with User: {user.email}, Organization: {org.name}")
    
    token = SessionToken.objects.filter(
        user=user,
        is_active=True,
        expires_at__gt=timezone.now()
    ).first()
    
    if not token:
        token = SessionToken.objects.create(
            user=user,
            key="test_session_token_key_123456",
            expires_at=timezone.now() + timedelta(days=1),
            is_active=True
        )
    
    client = APIClient()
    client.force_authenticate(user=user)
    
    session = client.session
    session['organization_id'] = org.id
    session.save()
    
    headers = {
        "HTTP_AUTHORIZATION": "Api-Key WefMykHH.C4jZy9FYP3WbZdy7aBgP4L1Bg7vXChB8",
        "HTTP_X_ORGANIZATION_ID": str(org.id),
        "HTTP_X_SESSION_TOKEN": token.key
    }

    # 1. Test Assignments
    print("\n--- Testing Assignments Endpoint ---")
    response = client.get("/api/assignments/", **headers)
    print(f"Status Code: {response.status_code}")
    if response.status_code == 200:
        print("SUCCESS: Loaded assignments!")
        print(f"  - Count: {len(response.data.get('results', [])) if isinstance(response.data, dict) else len(response.data)}")
    else:
        print("FAILED:", response.content)

if __name__ == "__main__":
    run_test()
