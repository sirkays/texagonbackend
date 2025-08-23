from django.shortcuts import render
from orgs.models import OrganizationMembership 
from django.conf import settings
from api.retrieve_token import get_token_from_header
from api.authentication import SessionTokenAuthentication
from rest_framework.response import Response
from rest_framework.decorators import api_view, permission_classes, authentication_classes
from rest_framework_api_key.permissions import HasAPIKey
from rest_framework import status


@api_view(["GET"])
@permission_classes([HasAPIKey])
@authentication_classes([SessionTokenAuthentication])
def post_login(request):
    # SessionTokenAuthentication guarantees request.user if the session token is valid
    user = getattr(request, "user", None)
    if not user or not user.is_authenticated:
        return Response({"detail": "Invalid or missing session token."}, status=status.HTTP_401_UNAUTHORIZED)

    # If users can belong to multiple orgs, pick one (latest active) or return all
    membership = (
        OrganizationMembership.objects
        .filter(user=user)
        .order_by("-id")
        .first()
    )
    if membership is None:
        return Response({"detail": "Organization not found."}, status=status.HTTP_400_BAD_REQUEST)

    if not membership.is_active:
        return Response({"detail": "The user has been deactivated."}, status=status.HTTP_403_FORBIDDEN)
    return Response(
        {
            "detail": "User access granted",
            "org_membership_pk": membership.pk,
            "role": membership.role,
        },
        status=status.HTTP_200_OK,  # <- correct constant
    )
