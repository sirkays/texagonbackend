from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password, ValidationError as PasswordValidationError
from django.db import transaction

from rest_framework import serializers, status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
User = get_user_model()

class ResetPasswordSerializer(serializers.Serializer):
    user_id = serializers.IntegerField(required=False)
    email = serializers.EmailField(required=False)
    new_password = serializers.CharField(write_only=True, min_length=8)
    current_password = serializers.CharField(write_only=True, required=False)
    new_email = serializers.EmailField(required=False, write_only=True)

    def validate(self, data):
        request_user = self.context["request"].user

        # Resolve target_user
        target_user = None
        if data.get("user_id"):
            try:
                target_user = User.objects.get(id=data["user_id"])
            except User.DoesNotExist:
                raise serializers.ValidationError({"user_id": "User with this id does not exist."})
        elif data.get("email") and not data.get("new_email"):
            try:
                target_user = User.objects.get(email=data["email"])
            except User.DoesNotExist:
                raise serializers.ValidationError({"email": "User with this email does not exist."})
        else:
            target_user = request_user

        # Permission check
        if target_user != request_user and not (request_user.is_staff or request_user.is_superuser):
            raise serializers.ValidationError("You do not have permission to change another user's password or email.")

        # If self-reset, require current_password and verify
        if target_user == request_user:
            current = data.get("current_password")
            if not current:
                raise serializers.ValidationError({"current_password": "Current password is required to change your own password."})
            if not request_user.check_password(current):
                raise serializers.ValidationError({"current_password": "Current password is incorrect."})

        # Disallow using the same password again
        new_password = data.get("new_password")
        # Only check if the target_user has a usable password (check_password handles unusable)
        if target_user.check_password(new_password):
            raise serializers.ValidationError({"new_password": "New password must be different from your current password."})

        # Validate new_password with Django validators
        try:
            validate_password(new_password, user=target_user)
        except PasswordValidationError as e:
            raise serializers.ValidationError({"new_password": list(e.messages)})

        # Validate new_email uniqueness (if provided)
        new_email = data.get("new_email")
        if new_email:
            normalized = User.objects.normalize_email(new_email)
            already = User.objects.filter(email__iexact=normalized).exclude(pk=target_user.pk).exists()
            if already:
                raise serializers.ValidationError({"new_email": "This email is already in use by another account."})
            data["new_email"] = normalized

        data["target_user"] = target_user
        return data