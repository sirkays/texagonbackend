import os
import django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'texagonbackend.settings')
django.setup()

from django.test import RequestFactory
from learning.views import resource_materials
from accounts.models import User

# Get the first student user
user = User.objects.filter(role='student').first()

factory = RequestFactory()
request = factory.get('/learning/api/academics/resources/')
request.user = user

response = resource_materials(request)
import json
print(json.dumps(response.data, indent=2))
