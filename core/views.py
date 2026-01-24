# views.py
from typing import Optional, Dict, Any, List
import traceback

from django.conf import settings
from django.db import transaction
from django.db.models import Q, Count, Max
from django.shortcuts import get_object_or_404
from django.utils.text import slugify

from rest_framework import status
from rest_framework.decorators import api_view, permission_classes, authentication_classes
from rest_framework.response import Response

from rest_framework_api_key.permissions import HasAPIKey

from api.authentication import SessionTokenAuthentication
from core.permissions import IsAdminAccess
from rest_framework_api_key.permissions import HasAPIKey

from orgs.models import OrganizationMembership
from academics.models import StudentProfile, Classroom, TeacherProfile

from learning.models import (
    Module,
    Lesson,
    Course,
    Enrollment,
    CoursePassCriteria,
)
from gamification.models import LeaderboardSeason
from core.models import StudentDevice
from core.permissions import IsAdminAccess   # your existing permission

from store.models import Category, Product, ProductImage
from store.serializers import (
    CategorySerializer,
    ProductAdminSerializer,
    ProductImageSerializer,
)

from .utils import (
    _get_student_for_user,
    _is_org_admin_or_teacher,
    _season_to_dict,
    _parse_dt,
    _resolve_org,
    _criteria_to_dict,
    _parse_positive_int,
    _get_user_avatar_url,
    _try_fetch_courses_for_classroom,
    _get_admin_selected_org_id

)



@api_view(["GET"])
@permission_classes([HasAPIKey])
@authentication_classes([SessionTokenAuthentication])
def active_modules_for_user(request):
    """
    Return all *active* Modules connected to the authenticated student.

    Scoping:
      - Only Modules with Module.active=True
      - Only Modules belonging to Courses the student is actively enrolled in
      - Course.is_active is also respected (optional, toggle with ?only_active_courses=0 to ignore)

    Query params:
      - q: text search (module name, course name, subject)
      - course_id: filter to a specific course
      - subject_id: filter to a subject
      - teacher_id: filter to a teacher profile id
      - page (default 1), page_size (default 20, max 100)
      - only_active_courses (default 1): if 1, restrict to Course.is_active=True
      - debug=1 to include traceback in error responses

    Response:
      {
        "count": <int>,
        "page": <int>,
        "page_size": <int>,
        "results": [
          {
            "id": <module_id>,
            "name": "...",
            "order": <int>,
            "active": true,
            "course": {
              "id": <int>, "name": "...", "subject": "...",
              "classroom": "...", "teacher": "Display Name"
            },
            "lessons_count": <int>,             # active lessons in the module
            "last_updated": "ISO8601 or null",   # last lesson update in the module
            "course_progress": <int>,            # % from Enrollment.progress_pct
            "recent_lesson": {                   # first lesson by order (active) if any
              "id": <int>,
              "title": "...",
              "content_type": "video|audio|pdf|doc|link",
              "duration_seconds": <int>,
              "url": "file/url/or/external/url"
            }
          },
          ...
        ]
      }
    """
    try:
        user = request.user
        student = _get_student_for_user(user)
        if not student:
            return Response(
                {"count": 0, "page": 1, "page_size": 0, "results": [], "detail": "Student profile not found."},
                status=status.HTTP_200_OK,
            )

        # ----------- parsing filters / pagination -----------
        def _i(v, d, cap=None):
            try:
                x = int(v) if v is not None else d
                return min(x, cap) if cap else x
            except Exception:
                return d

        q = (request.query_params.get("q") or "").strip()
        course_id = request.query_params.get("course_id")
        subject_id = request.query_params.get("subject_id")
        teacher_id = request.query_params.get("teacher_id")
        only_active_courses = _i(request.query_params.get("only_active_courses"), 1)

        page = _i(request.query_params.get("page"), 1)
        page_size = _i(request.query_params.get("page_size"), 20, cap=100)

        # ----------- student enrollments -----------
        enroll_qs = Enrollment.objects.filter(student=student, status=Enrollment.Status.ACTIVE)
        if only_active_courses:
            enroll_qs = enroll_qs.filter(course__is_active=True)

        enrolled_course_ids = list(enroll_qs.values_list("course_id", flat=True))
        if not enrolled_course_ids:
            return Response({"count": 0, "page": page, "page_size": page_size, "results": []},
                            status=status.HTTP_200_OK)

        progress_map = {
            e.course_id: int(e.progress_pct or 0)
            for e in enroll_qs
        }

        # ----------- base modules queryset -----------
        modules_qs = (
            Module.objects
            .filter(active=True, course_id__in=enrolled_course_ids)
            .select_related("course", "course__subject", "course__classroom", "course__teacher__user")
            .annotate(
                lessons_count=Count("lessons", filter=Q(lessons__active=True), distinct=True),
                last_updated=Max("lessons__updated_at"),
            )
        )

        if course_id:
            modules_qs = modules_qs.filter(course_id=course_id)
        if subject_id:
            modules_qs = modules_qs.filter(course__subject_id=subject_id)
        if teacher_id:
            modules_qs = modules_qs.filter(course__teacher_id=teacher_id)
        if q:
            modules_qs = modules_qs.filter(
                Q(name__icontains=q) |
                Q(course__name__icontains=q) |
                Q(course__subject__name__icontains=q)
            )

        modules_qs = modules_qs.order_by("course_id", "order", "id")

        total = modules_qs.count()
        start = (page - 1) * page_size
        end = start + page_size
        modules_page = list(modules_qs[start:end])

        # ----------- fetch a recent/first active lesson per module -----------
        module_ids = [m.id for m in modules_page]
        latest_by_module: Dict[int, Lesson] = {}
        if module_ids:
            # first active lesson by (order, id)
            for ls in (Lesson.objects
                       .filter(active=True, module_id__in=module_ids)
                       .order_by("module_id", "order", "id")):
                if ls.module_id not in latest_by_module:
                    latest_by_module[ls.module_id] = ls

        # ----------- build response -----------
        results: List[Dict[str, Any]] = []
        for m in modules_page:
            c: Course = m.course
            teacher_user = getattr(c.teacher, "user", None) if c and c.teacher else None
            teacher_name = (teacher_user.get_full_name() or teacher_user.username) if teacher_user else None
            subj_name = getattr(c.subject, "name", None) if c and c.subject else None
            classroom_name = getattr(c.classroom, "name", None) if c and c.classroom else None

            rl = latest_by_module.get(m.id)
            recent = None
            if rl:
                # link to file or external url
                url = None
                if rl.file:
                    try:
                        url = rl.file.url
                    except Exception:
                        url = None
                if not url:
                    url = rl.url or None

                recent = {
                    "id": rl.id,
                    "title": rl.name,
                    "content_type": rl.content_type,
                    "duration_seconds": int(rl.duration_seconds or 0),
                    "url": url,
                }

            results.append({
                "id": m.id,
                "name": m.name,
                "order": m.order,
                "active": m.active,
                "course": {
                    "id": c.id if c else None,
                    "name": c.name if c else None,
                    "subject": subj_name,
                    "classroom": classroom_name,
                    "teacher": teacher_name,
                },
                "lessons_count": int(getattr(m, "lessons_count", 0) or 0),
                "last_updated": m.last_updated.isoformat() if getattr(m, "last_updated", None) else None,
                "course_progress": progress_map.get(c.id if c else None, 0),
                "recent_lesson": recent,
            })

        return Response(
            {"count": total, "page": page, "page_size": page_size, "results": results},
            status=status.HTTP_200_OK,
        )

    except Exception as e:
        err = {"detail": "Failed to load active modules for user.", "error": f"{type(e).__name__}: {e}"}
        if request.query_params.get("debug") in {"1", "true", "True"} or getattr(settings, "DEBUG", False):
            err["traceback"] = traceback.format_exc()
        return Response(err, status=status.HTTP_500_INTERNAL_SERVER_ERROR)



@api_view(["GET", "POST"])
@permission_classes([HasAPIKey])
@authentication_classes([SessionTokenAuthentication])
def leaderboard_seasons_view(request):
    """
    GET  /api/admin/settings/leaderboard-seasons
    POST /api/admin/settings/leaderboard-seasons

    Headers:
      Authorization: Api-Key <YOUR_API_KEY>
      X-Session-Token: <session_token>

    Body (POST):
      {
        "name": "2026 Academic Year",
        "slug": "2026-academic-year",   # optional; will slugify(name) if omitted
        "start_at": "2026-01-01T00:00:00Z",
        "end_at": "2026-12-31T23:59:59Z",
        "is_active": false              # optional
      }
    """
    try:
        org, err = _resolve_org(request)
        if err:
            return err

        if not _is_org_admin_or_teacher(request, org):
            return Response({"detail": "Forbidden."}, status=status.HTTP_403_FORBIDDEN)

        if request.method == "GET":
            qs = LeaderboardSeason.objects.filter(organization=org).order_by("-start_at", "-id")
            return Response([_season_to_dict(s) for s in qs])

        # POST (create)
        data = request.data or {}

        name = (data.get("name") or "").strip()
        if not name:
            return Response({"detail": "name is required."}, status=status.HTTP_400_BAD_REQUEST)

        slug = (data.get("slug") or "").strip()
        if not slug:
            slug = slugify(name)[:128]

        start_at = _parse_dt(data.get("start_at"))
        end_at = _parse_dt(data.get("end_at"))
        if not start_at or not end_at:
            return Response(
                {"detail": "start_at and end_at must be valid ISO 8601 datetimes."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if start_at >= end_at:
            return Response({"detail": "start_at must be before end_at."}, status=status.HTTP_400_BAD_REQUEST)

        is_active = bool(data.get("is_active", False))

        with transaction.atomic():
            # if making active, deactivate others first
            if is_active:
                LeaderboardSeason.objects.filter(organization=org, is_active=True).update(is_active=False)

            s = LeaderboardSeason.objects.create(
                organization=org,
                name=name,
                slug=slug,
                start_at=start_at,
                end_at=end_at,
                is_active=is_active,
            )

        return Response(_season_to_dict(s), status=status.HTTP_201_CREATED)

    except Exception as e:
        traceback.print_exc()
        return Response(
            {"detail": "Unexpected error", "error": str(e)},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )


@api_view(["GET", "PATCH", "DELETE"])
@permission_classes([HasAPIKey])
@authentication_classes([SessionTokenAuthentication])
def leaderboard_season_detail_view(request, season_id: int):
    """
    GET    /api/admin/settings/leaderboard-seasons/<season_id>
    PATCH  /api/admin/settings/leaderboard-seasons/<season_id>
    DELETE /api/admin/settings/leaderboard-seasons/<season_id>

    Body (PATCH): any of
      {
        "name": "...",
        "slug": "...",
        "start_at": "ISO",
        "end_at": "ISO",
        "is_active": true/false
      }
    """
    try:
        org, err = _resolve_org(request)
        if err:
            return err

        if not _is_org_admin_or_teacher(request, org):
            return Response({"detail": "Forbidden."}, status=status.HTTP_403_FORBIDDEN)

        try:
            s = LeaderboardSeason.objects.get(id=season_id, organization=org)
        except LeaderboardSeason.DoesNotExist:
            return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)

        if request.method == "GET":
            return Response(_season_to_dict(s))

        if request.method == "DELETE":
            s.delete()
            return Response(status=status.HTTP_204_NO_CONTENT)

        # PATCH
        data = request.data or {}

        name = data.get("name", None)
        slug = data.get("slug", None)
        start_at_val = data.get("start_at", None)
        end_at_val = data.get("end_at", None)
        is_active_val = data.get("is_active", None)

        if name is not None:
            name = (name or "").strip()
            if not name:
                return Response({"detail": "name cannot be empty."}, status=status.HTTP_400_BAD_REQUEST)
            s.name = name
            # if slug not explicitly provided, keep existing slug

        if slug is not None:
            slug = (slug or "").strip()
            if not slug:
                return Response({"detail": "slug cannot be empty."}, status=status.HTTP_400_BAD_REQUEST)
            s.slug = slug

        if start_at_val is not None:
            start_at = _parse_dt(start_at_val)
            if not start_at:
                return Response({"detail": "start_at must be valid ISO 8601 datetime."}, status=status.HTTP_400_BAD_REQUEST)
            s.start_at = start_at

        if end_at_val is not None:
            end_at = _parse_dt(end_at_val)
            if not end_at:
                return Response({"detail": "end_at must be valid ISO 8601 datetime."}, status=status.HTTP_400_BAD_REQUEST)
            s.end_at = end_at

        # validate date range if either changed
        if s.start_at and s.end_at and s.start_at >= s.end_at:
            return Response({"detail": "start_at must be before end_at."}, status=status.HTTP_400_BAD_REQUEST)

        with transaction.atomic():
            if is_active_val is not None:
                make_active = bool(is_active_val)
                if make_active:
                    LeaderboardSeason.objects.filter(organization=org, is_active=True).exclude(id=s.id).update(is_active=False)
                s.is_active = make_active

            s.save()

        return Response(_season_to_dict(s), status=status.HTTP_200_OK)

    except Exception as e:
        traceback.print_exc()
        return Response(
            {"detail": "Unexpected error", "error": str(e)},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )


@api_view(["POST"])
@permission_classes([HasAPIKey])
@authentication_classes([SessionTokenAuthentication])
def leaderboard_season_set_active_view(request, season_id: int):
    """
    POST /api/admin/settings/leaderboard-seasons/<season_id>/set-active
    Sets this season as the ONLY active season for the org (atomic).
    """
    try:
        org, err = _resolve_org(request)
        if err:
            return err

        if not _is_org_admin_or_teacher(request, org):
            return Response({"detail": "Forbidden."}, status=status.HTTP_403_FORBIDDEN)

        try:
            s = LeaderboardSeason.objects.get(id=season_id, organization=org)
        except LeaderboardSeason.DoesNotExist:
            return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)

        with transaction.atomic():
            LeaderboardSeason.objects.filter(organization=org, is_active=True).exclude(id=s.id).update(is_active=False)
            s.is_active = True
            s.save()

        return Response(_season_to_dict(s), status=status.HTTP_200_OK)

    except Exception as e:
        traceback.print_exc()
        return Response(
            {"detail": "Unexpected error", "error": str(e)},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )




@api_view(["GET", "POST", "PATCH"])
@permission_classes([HasAPIKey])
@authentication_classes([SessionTokenAuthentication])
def course_pass_criteria_view(request, course_id: int):
    """
    GET   /api/admin/courses/<course_id>/pass-criteria
    POST  /api/admin/courses/<course_id>/pass-criteria   (create or replace)
    PATCH /api/admin/courses/<course_id>/pass-criteria   (partial update)

    Headers:
      Authorization: Api-Key <YOUR_API_KEY>
      X-Session-Token: <session_token>

    Body (POST/PATCH):
      {
        "no_of_cbt": 10,
        "no_of_code_submission": 10,
        "total_pass_mark_cbt": 500,
        "total_pass_mark_code": 500
      }
    """
    try:
        org, err = _resolve_org(request)
        if err:
            return err

        if not _is_org_admin_or_teacher(request, org):
            return Response({"detail": "Forbidden."}, status=status.HTTP_403_FORBIDDEN)

        course = get_object_or_404(Course, id=course_id, organization=org)

        # GET
        if request.method == "GET":
            criteria = getattr(course, "pass_criteria", None)
            if not criteria:
                # return defaults (optional) or 404. I prefer "empty + defaults" for UI simplicity.
                return Response(
                    {
                        "course_id": course.id,
                        "no_of_cbt": 10,
                        "no_of_code_submission": 10,
                        "total_pass_mark_cbt": 500,
                        "total_pass_mark_code": 500,
                        "exists": False,
                    },
                    status=status.HTTP_200_OK,
                )
            d = _criteria_to_dict(criteria)
            d["exists"] = True
            return Response(d, status=status.HTTP_200_OK)

        # POST/PATCH (upsert)
        data = request.data or {}

        # Defaults:
        default_no_of_cbt = 10
        default_no_of_code_submission = 10
        default_total_pass_mark_cbt = 500
        default_total_pass_mark_code = 500

        # For PATCH: only update provided fields
        is_patch = request.method == "PATCH"

        no_of_cbt, err_resp = _parse_positive_int(
            data.get("no_of_cbt"), "no_of_cbt", default=None if is_patch else default_no_of_cbt
        )
        if err_resp:
            return err_resp

        no_of_code_submission, err_resp = _parse_positive_int(
            data.get("no_of_code_submission"),
            "no_of_code_submission",
            default=None if is_patch else default_no_of_code_submission,
        )
        if err_resp:
            return err_resp

        total_pass_mark_cbt, err_resp = _parse_positive_int(
            data.get("total_pass_mark_cbt"),
            "total_pass_mark_cbt",
            default=None if is_patch else default_total_pass_mark_cbt,
        )
        if err_resp:
            return err_resp

        total_pass_mark_code, err_resp = _parse_positive_int(
            data.get("total_pass_mark_code"),
            "total_pass_mark_code",
            default=None if is_patch else default_total_pass_mark_code,
        )
        if err_resp:
            return err_resp

        with transaction.atomic():
            criteria, created = CoursePassCriteria.objects.get_or_create(course=course)

            # Apply fields (POST replaces all; PATCH only updates provided)
            if not is_patch or "no_of_cbt" in data:
                criteria.no_of_cbt = no_of_cbt if no_of_cbt is not None else criteria.no_of_cbt
            if not is_patch or "no_of_code_submission" in data:
                criteria.no_of_code_submission = (
                    no_of_code_submission
                    if no_of_code_submission is not None
                    else criteria.no_of_code_submission
                )
            if not is_patch or "total_pass_mark_cbt" in data:
                criteria.total_pass_mark_cbt = (
                    total_pass_mark_cbt
                    if total_pass_mark_cbt is not None
                    else criteria.total_pass_mark_cbt
                )
            if not is_patch or "total_pass_mark_code" in data:
                criteria.total_pass_mark_code = (
                    total_pass_mark_code
                    if total_pass_mark_code is not None
                    else criteria.total_pass_mark_code
                )

            criteria.save()

        out = _criteria_to_dict(criteria)
        out["created"] = created
        return Response(out, status=status.HTTP_201_CREATED if created else status.HTTP_200_OK)

    except Exception as e:
        print(e)
        traceback.print_exc()
        return Response(
            {"detail": "Unexpected error", "error": str(e)},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )







def _paginate(request, qs, serializer_cls, *, context=None):
    page = int(request.query_params.get("page", 1) or 1)
    page_size = int(request.query_params.get("page_size", 20) or 20)
    page_size = max(1, min(page_size, 100))

    total = qs.count()
    start = (page - 1) * page_size
    end = start + page_size
    rows = qs[start:end]

    ser = serializer_cls(rows, many=True, context=context or {})
    return Response({
        "count": total,
        "page": page,
        "page_size": page_size,
        "results": ser.data
    })


# -------------------------
# Categories CRUD
# -------------------------

@api_view(["GET", "POST"])
@permission_classes([HasAPIKey, IsAdminAccess])
@authentication_classes([SessionTokenAuthentication])
def admin_categories_list_create(request):
    if request.method == "GET":
        qs = Category.objects.all().order_by("name")
        return _paginate(request, qs, CategorySerializer, context={"request": request})

    ser = CategorySerializer(data=request.data, context={"request": request})
    ser.is_valid(raise_exception=True)
    obj = ser.save()
    return Response(CategorySerializer(obj, context={"request": request}).data, status=status.HTTP_201_CREATED)


@api_view(["GET", "PATCH", "DELETE"])
@permission_classes([HasAPIKey, IsAdminAccess])
@authentication_classes([SessionTokenAuthentication])
def admin_categories_detail(request, category_id):
    obj = Category.objects.filter(id=category_id).first()
    if not obj:
        return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)

    if request.method == "GET":
        return Response(CategorySerializer(obj, context={"request": request}).data)

    if request.method == "PATCH":
        ser = CategorySerializer(obj, data=request.data, partial=True, context={"request": request})
        ser.is_valid(raise_exception=True)
        obj = ser.save()
        return Response(CategorySerializer(obj, context={"request": request}).data)

    # DELETE
    obj.delete()
    return Response(status=status.HTTP_204_NO_CONTENT)


# -------------------------
# Products CRUD
# -------------------------

@api_view(["GET", "POST"])
@permission_classes([HasAPIKey, IsAdminAccess])
@authentication_classes([SessionTokenAuthentication])
def admin_products_list_create(request):
    if request.method == "GET":
        qs = Product.objects.select_related("category").prefetch_related("images").order_by("-created_at")

        q = (request.query_params.get("q") or "").strip()
        if q:
            qs = qs.filter(
                Q(title__icontains=q) |
                Q(slug__icontains=q) |
                Q(sku__icontains=q)
            )

        product_type = (request.query_params.get("product_type") or "").strip()
        if product_type:
            qs = qs.filter(product_type=product_type)

        category_id = (request.query_params.get("category_id") or "").strip()
        if category_id:
            qs = qs.filter(category_id=category_id)

        is_active = request.query_params.get("is_active")
        if is_active in ("true", "false"):
            qs = qs.filter(is_active=(is_active == "true"))

        return _paginate(request, qs, ProductAdminSerializer, context={"request": request})

    # POST create
    ser = ProductAdminSerializer(data=request.data, context={"request": request})
    ser.is_valid(raise_exception=True)
    obj = ser.save()
    return Response(ProductAdminSerializer(obj, context={"request": request}).data, status=status.HTTP_201_CREATED)


@api_view(["GET", "PATCH", "DELETE"])
@permission_classes([HasAPIKey, IsAdminAccess])
@authentication_classes([SessionTokenAuthentication])
def admin_products_detail(request, product_id):
    obj = Product.objects.select_related("category").prefetch_related("images").filter(id=product_id).first()
    if not obj:
        return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)

    if request.method == "GET":
        return Response(ProductAdminSerializer(obj, context={"request": request}).data)

    if request.method == "PATCH":
        ser = ProductAdminSerializer(obj, data=request.data, partial=True, context={"request": request})
        ser.is_valid(raise_exception=True)
        obj = ser.save()
        return Response(ProductAdminSerializer(obj, context={"request": request}).data)

    # DELETE
    obj.delete()
    return Response(status=status.HTTP_204_NO_CONTENT)


# -------------------------
# Product Images (upload + delete + reorder)
# -------------------------

@api_view(["POST"])
@permission_classes([HasAPIKey, IsAdminAccess])
@authentication_classes([SessionTokenAuthentication])
@transaction.atomic
def admin_product_images_upload(request, product_id):
    product = Product.objects.filter(id=product_id).first()
    if not product:
        return Response({"detail": "Product not found."}, status=status.HTTP_404_NOT_FOUND)

    # multipart expected: file in "product_image"
    file = request.FILES.get("product_image")
    if not file:
        return Response({"detail": "product_image file is required."}, status=status.HTTP_400_BAD_REQUEST)

    alt_text = request.data.get("alt_text", "") or ""
    sort_order = int(request.data.get("sort_order") or 0)

    img = ProductImage.objects.create(
        product=product,
        product_image=file,
        alt_text=alt_text,
        sort_order=sort_order,
    )

    return Response(ProductImageSerializer(img, context={"request": request}).data, status=status.HTTP_201_CREATED)


@api_view(["DELETE", "PATCH"])
@permission_classes([HasAPIKey, IsAdminAccess])
@authentication_classes([SessionTokenAuthentication])
def admin_product_images_detail(request, image_id):
    img = ProductImage.objects.select_related("product").filter(id=image_id).first()
    if not img:
        return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)

    if request.method == "PATCH":
        # allow alt_text / sort_order edits
        ser = ProductImageSerializer(img, data=request.data, partial=True, context={"request": request})
        ser.is_valid(raise_exception=True)
        img = ser.save()
        return Response(ProductImageSerializer(img, context={"request": request}).data)

    img.delete()
    return Response(status=status.HTTP_204_NO_CONTENT)




@api_view(["GET"])
@permission_classes([HasAPIKey, IsAdminAccess])
@authentication_classes([SessionTokenAuthentication])
def admin_classroom_modal_data(request, classroom_id: int):
    try:
        """
        GET /api/admin/classrooms/<id>/modal/

        Returns ONLY what the ClassroomDetailsModal needs.
        ✅ No Student Grade included.
        """
        classroom = (
            Classroom.objects.select_related("organization")
            .prefetch_related("teachers")
            .filter(id=classroom_id)
            .first()
        )
        if not classroom:
            return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)

        # Students in this classroom
        students_qs = (
            StudentProfile.objects.select_related("user")
            .filter(organization_id=classroom.organization_id, current_classroom_id=classroom.id)
            .order_by("user__first_name", "user__last_name")
        )

        students = [
            {
                "id": s.id,
                "user_id": s.user_id,
                "name": _full_name(s.user),
                "email": getattr(s.user, "email", "") or "",
                "avatar_url": _get_user_avatar_url(request, s.user),
                "admission_no": s.admission_no or "",
            }
            for s in students_qs
        ]

        # Teachers attached to classroom (many-to-many)
        teacher_user_ids = list(classroom.teachers.values_list("id", flat=True))
        teachers_profiles = (
            TeacherProfile.objects.select_related("user")
            .prefetch_related("specialties")
            .filter(organization_id=classroom.organization_id, user_id__in=teacher_user_ids)
        )
        # map for quick lookup
        tp_by_user = {tp.user_id: tp for tp in teachers_profiles}

        teachers = []
        for u in classroom.teachers.all():
            tp = tp_by_user.get(u.id)
            teachers.append(
                {
                    "user_id": u.id,
                    "name": u.get_full_name(),
                    "email": getattr(u, "email", "") or "",
                    "avatar_url": _get_user_avatar_url(request, u),
                    "specialties": [s.name for s in (tp.specialties.all() if tp else [])],
                }
            )

        # Courses (optional inference)
        courses, courses_count = _try_fetch_courses_for_classroom(classroom)

        payload = {
            "id": classroom.id,
            "name": classroom.name,
            "code": classroom.code,
            "description": getattr(classroom, "description", "") or "",
            "stats": {
                "students": len(students),
                "teachers": len(teachers),
                "courses": courses_count,
            },
            "students": students,  # ✅ no grade field here
            "teachers": teachers,
            "courses": courses,
        }
        return Response(payload, status=status.HTTP_200_OK)

    except Exception as e:
        print(e)
    return Response({}, status=status.HTTP_400_BAD_REQUEST)




def _device_dict(d: StudentDevice):
    return {
        "id": d.id,
        "device_id": d.device_id,
        "user_agent": d.user_agent,
        "first_seen": d.first_seen,
        "last_seen": d.last_seen,
    }


def _student_result(sp: StudentProfile):
    u = sp.user
    full_name = (u.get_full_name() or "").strip()
    return {
        "student_id": sp.id,
        "user_id": u.id,
        "email": u.email,
        "full_name": full_name or None,
        "organization_id": sp.organization_id,
        "devices": [_device_dict(d) for d in sp.devices.order_by("-last_seen")],
    }


@api_view(["GET"])
@permission_classes([HasAPIKey, IsAdminAccess])
@authentication_classes([SessionTokenAuthentication])
def admin_student_devices_search(request):
    """
    GET /api/admin/student-devices?query=...&limit=20
    Search by student email or name, return students + their devices.
    Scoped to adminaccess.selected_organization.
    """
    org_id = _get_admin_selected_org_id(request)
    if not org_id:
        return Response(
            {"detail": "No selected organization for this admin."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    query = (request.query_params.get("query") or "").strip()
    limit = int(request.query_params.get("limit") or 20)
    limit = max(1, min(limit, 50))

    if not query:
        return Response({"count": 0, "results": []}, status=status.HTTP_200_OK)

    # Search students in selected org by email or name
    qs = (
        StudentProfile.objects
        .select_related("user")
        .prefetch_related("devices")
        .filter(organization_id=org_id)
        .filter(
            Q(user__email__icontains=query)
            | Q(user__first_name__icontains=query)
            | Q(user__last_name__icontains=query)
            | Q(user__first_name__icontains=query.split(" ")[0])  # helps partials
        )
        .order_by("user__first_name", "user__last_name")
    )

    students = list(qs[:limit])
    return Response(
        {"count": qs.count(), "results": [_student_result(sp) for sp in students]},
        status=status.HTTP_200_OK,
    )


@api_view(["DELETE"])
@permission_classes([HasAPIKey, IsAdminAccess])
@authentication_classes([SessionTokenAuthentication])
@transaction.atomic
def admin_student_device_delete(request, device_pk: int):
    """
    DELETE /api/admin/student-devices/<device_pk>/
    Deletes one StudentDevice (scoped to admin selected org).
    """
    org_id = _get_admin_selected_org_id(request)
    if not org_id:
        return Response(
            {"detail": "No selected organization for this admin."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    device = (
        StudentDevice.objects
        .select_related("student", "student__user")
        .filter(id=device_pk, student__organization_id=org_id)
        .first()
    )
    if not device:
        return Response({"detail": "Device not found."}, status=status.HTTP_404_NOT_FOUND)

    device.delete()
    return Response({"detail": "Deleted."}, status=status.HTTP_200_OK)
