from django.contrib.auth.backends import ModelBackend
from django.db.models import Q
from .models import User
from academics.models import StudentProfile

class IdentifierBackend(ModelBackend):
    """
    Custom authentication backend to allow login with either email, username, or student admission number.
    """
    def authenticate(self, request, email=None, password=None, **kwargs):
        # The 'email' argument might be passed as 'username' by some standard Django components
        identifier = email or kwargs.get('username')
        
        if not identifier:
            return None

        try:
            # 1. Try finding by email or username
            user = User.objects.get(Q(email__iexact=identifier) | Q(username__iexact=identifier))
        except User.DoesNotExist:
            # 2. Try finding by admission number in StudentProfile
            try:
                student = StudentProfile.objects.get(admission_no__iexact=identifier)
                user = student.user
            except StudentProfile.DoesNotExist:
                return None

        if user.check_password(password) and self.user_can_authenticate(user):
            return user
        return None
