import logging

from django.core.management.base import BaseCommand
from django.contrib.auth import get_user_model
from orgs.models import Organization

User = get_user_model()
logger = logging.getLogger(__name__)

class Command(BaseCommand):
    help = "Resets the is_generated flag to False for all students in a specified organization."

    def add_arguments(self, parser):
        parser.add_argument('org_pk', type=int, help='The primary key of the organization')

    def handle(self, *args, **kwargs):
        org_pk = kwargs['org_pk']
        
        try:
            org = Organization.objects.get(pk=org_pk)
        except Organization.DoesNotExist:
            self.stdout.write(self.style.ERROR(f"Organization with pk {org_pk} does not exist."))
            return

        # Find users who have an OrganizationMembership with role="student" for this org
        # Note: We filter for both True and False just in case, or only True. 
        # The prompt says "all Student User model is_generated will become False, that checked or change to false"
        # So it's best to grab all who have it as True and update them, or just update all of them.
        students = User.objects.filter(
            memberships__organization=org,
            memberships__role='student',
            is_generated=True
        ).distinct()

        count = students.count()
        if count == 0:
            self.stdout.write(self.style.WARNING(f"No students found with is_generated=True in organization '{org.name}' (pk={org.pk})."))
            return
        
        # Update is_generated to False
        students.update(is_generated=False)
        
        self.stdout.write(self.style.SUCCESS(f"Successfully updated {count} student(s) to is_generated=False in organization '{org.name}' (pk={org.pk})."))
