from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password, ValidationError as PasswordValidationError
from django.db import transaction

from rest_framework import serializers, status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

class ResetPasswordSerializer(serializers.Serializer):
    # identifying the target user (one required for admin resets)
    user_id = serializers.IntegerField(required=False)
    email = serializers.EmailField(required=False)

    # required for both flows
    new_password = serializers.CharField(write_only=True, min_length=8)

    # required when user resets their own password
    current_password = serializers.CharField(write_only=True, required=False)

    def validate(self, data):
        request_user = self.context["request"].user

        # Determine target user if provided
        target_user = None
        if data.get("user_id"):
            try:
                target_user = User.objects.get(id=data["user_id"])
            except User.DoesNotExist:
                raise serializers.ValidationError({"user_id": "User with this id does not exist."})
        elif data.get("email"):
            try:
                target_user = User.objects.get(email=data["email"])
            except User.DoesNotExist:
                raise serializers.ValidationError({"email": "User with this email does not exist."})
        else:
            # No target provided -> implies self
            target_user = request_user

        # Permission check: if trying to change someone else's password, require staff
        if target_user != request_user and not (request_user.is_staff or request_user.is_superuser):
            raise serializers.ValidationError("You do not have permission to change another user's password.")

        # If self-reset, require current_password and verify
        if target_user == request_user:
            current = data.get("current_password")
            if not current:
                raise serializers.ValidationError({"current_password": "Current password is required to change your own password."})
            if not request_user.check_password(current):
                raise serializers.ValidationError({"current_password": "Current password is incorrect."})

        # validate new_password with Django validators (length already enforced by min_length)
        new_password = data.get("new_password")
        try:
            validate_password(new_password, user=target_user)
        except PasswordValidationError as e:
            raise serializers.ValidationError({"new_password": list(e.messages)})

        # Attach resolved target to validated data for use in view
        data["target_user"] = target_user
        return data
