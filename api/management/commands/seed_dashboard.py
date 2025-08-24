from django.core.management.base import BaseCommand, CommandError
from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils import timezone
from datetime import timedelta, datetime

from orgs.models import Organization, OrganizationMembership
from academics.models import Classroom, Subject, StudentProfile, TeacherProfile
from learning.models import Course, Enrollment, Module, Lesson, Bookmark
from assessments.models import Test
from gamification.models import Badge, BadgeAward, PointTransaction, Streak


# ---------- helpers ----------
def model_has_field(model, name: str) -> bool:
    try:
        model._meta.get_field(name)
        return True
    except Exception:
        return False

def put_if_exists(model, d: dict, field: str, value):
    if model_has_field(model, field):
        d[field] = value

def ensure_user_by_login(identifier: str, email_fallback: str | None = None):
    """
    Create/get a user using the model's USERNAME_FIELD.
    If USERNAME_FIELD == 'email', pass identifier as email.
    """
    User = get_user_model()
    login_field = User.USERNAME_FIELD  # e.g., 'email' for your setup
    lookup = {login_field: identifier}
    defaults = {}

    # If the login field is not email, still give the user an email if provided
    if login_field != "email" and email_fallback:
        defaults["email"] = email_fallback

    u, _ = User.objects.get_or_create(**lookup, defaults=defaults)
    return u


class Command(BaseCommand):
    help = "Seed rich dashboard data for a student (default user pk=9)."

    def add_arguments(self, parser):
        parser.add_argument("--student", type=int, default=9, help="User pk of the student")

    @transaction.atomic
    def handle(self, *args, **opts):
        student_user_pk = opts["student"]
        User = get_user_model()

        # 1) Ensure student user exists
        try:
            student_user = User.objects.get(pk=student_user_pk)
        except User.DoesNotExist:
            raise CommandError(f"User pk={student_user_pk} does not exist")

        # 2) Organization + membership
        org, _ = Organization.objects.get_or_create(
            slug="acme-high-academy",
            defaults={
                "name": "Acme High School",
                "city": "Lagos",
                "state": "Lagos",
                "country": "Nigeria",
                "contact_email": "admin@acme-high.test",
                "is_active": True,
            },
        )

        OrganizationMembership.objects.get_or_create(
            user=student_user, organization=org,
            defaults={"role": "student", "is_active": True},
        )

        # 3) Classroom & Subjects
        classroom, _ = Classroom.objects.get_or_create(
            organization=org, name="SS1 A",
            defaults={"code": "SSA-1"},
        )

        sub_react, _ = Subject.objects.get_or_create(organization=org, name="Advanced React Development", defaults={"code": "REACT-ADV"})
        sub_py, _    = Subject.objects.get_or_create(organization=org, name="Python for Data Science", defaults={"code": "PY-DS"})
        sub_algo, _  = Subject.objects.get_or_create(organization=org, name="JavaScript Algorithms", defaults={"code": "JS-ALGO"})

        # 4) Student/Teacher profiles
        student, _ = StudentProfile.objects.get_or_create(
            user=student_user,
            defaults={"organization": org, "current_classroom": classroom, "admission_no": "STU-0009"},
        )
        # Ensure org/classroom are set correctly
        update_fields = []
        if getattr(student, "organization_id", None) != org.id:
            student.organization = org; update_fields.append("organization")
        if hasattr(student, "current_classroom") and student.current_classroom_id != classroom.id:
            student.current_classroom = classroom; update_fields.append("current_classroom")
        if update_fields:
            student.save(update_fields=update_fields)

        # Create 3 teacher users using the model's USERNAME_FIELD
        # If your login is email, these identifiers are emails:
        t1 = ensure_user_by_login("t.reactsensei@test.local")
        t2 = ensure_user_by_login("t.datasage@test.local")
        t3 = ensure_user_by_login("t.algoguru@test.local")

        t_react, _ = TeacherProfile.objects.get_or_create(user=t1, defaults={"organization": org})
        t_py, _    = TeacherProfile.objects.get_or_create(user=t2, defaults={"organization": org})
        t_algo, _  = TeacherProfile.objects.get_or_create(user=t3, defaults={"organization": org})

        # 5) Courses
        c_react, _ = Course.objects.get_or_create(
            organization=org, subject=sub_react, classroom=classroom, teacher=t_react,
            defaults={"name": "Advanced React Development", "is_active": True},
        )
        c_py, _ = Course.objects.get_or_create(
            organization=org, subject=sub_py, classroom=classroom, teacher=t_py,
            defaults={"name": "Python for Data Science", "is_active": True},
        )
        c_algo, _ = Course.objects.get_or_create(
            organization=org, subject=sub_algo, classroom=classroom, teacher=t_algo,
            defaults={"name": "JavaScript Algorithms", "is_active": True},
        )

        # 6) Modules & Lessons
        def build_course(course, spec):
            mod, _ = Module.objects.get_or_create(course=course, name=f"{course.name} - Basics", defaults={"order": 1})
            lessons = []
            for idx, (title, secs) in enumerate(spec, start=1):
                defaults = {"name": title}
                if model_has_field(Lesson, "duration_seconds"):
                    defaults["duration_seconds"] = secs
                put_if_exists(Lesson, defaults, "content_type", "video")
                l, created = Lesson.objects.get_or_create(module=mod, order=idx, defaults=defaults)
                if not created:
                    # keep titles/durations in sync
                    changed = False
                    if l.name != title:
                        l.name = title; changed = True
                    if model_has_field(Lesson, "duration_seconds") and getattr(l, "duration_seconds", None) != secs:
                        l.duration_seconds = secs; changed = True
                    if changed:
                        l.save()
                lessons.append(l)
            return lessons

        react_lessons = build_course(c_react, [
            ("React Hooks Deep Dive", 45*60),
            ("State Management with Redux", 55*60),
            ("Performance & Suspense", 40*60),
        ])
        py_lessons = build_course(c_py, [
            ("NumPy Arrays", 50*60),
            ("Pandas DataFrames", 60*60),
            ("Visualization with Matplotlib", 45*60),
        ])
        algo_lessons = build_course(c_algo, [
            ("Greedy Strategies", 30*60),
            ("Dynamic Programming", 60*60),
            ("Graph Algorithms", 50*60),
        ])

        # 7) Enrollments (progress to match your UI)
        e_react, _ = Enrollment.objects.get_or_create(student=student, course=c_react, defaults={"progress_pct": 75})
        if e_react.progress_pct != 75: e_react.progress_pct = 75; e_react.save(update_fields=["progress_pct"])

        e_py, _ = Enrollment.objects.get_or_create(student=student, course=c_py, defaults={"progress_pct": 45})
        if e_py.progress_pct != 45: e_py.progress_pct = 45; e_py.save(update_fields=["progress_pct"])

        e_algo, _ = Enrollment.objects.get_or_create(student=student, course=c_algo, defaults={"progress_pct": 90})
        if e_algo.progress_pct != 90: e_algo.progress_pct = 90; e_algo.save(update_fields=["progress_pct"])

        # 8) Bookmarks (so dashboard can infer next lesson)
        bm_defaults = {}
        put_if_exists(Bookmark, bm_defaults, "position_seconds", 10)
        Bookmark.objects.get_or_create(student=student, lesson=react_lessons[0], defaults=bm_defaults)

        bm_defaults = {}
        put_if_exists(Bookmark, bm_defaults, "position_seconds", 20)
        Bookmark.objects.get_or_create(student=student, lesson=py_lessons[0], defaults=bm_defaults)

        bm_defaults = {}
        put_if_exists(Bookmark, bm_defaults, "position_seconds", 15)
        Bookmark.objects.get_or_create(student=student, lesson=algo_lessons[0], defaults=bm_defaults)

        # 9) Upcoming Tests (future dates)
        now = timezone.now()
        tomorrow_14 = (now + timedelta(days=1)).replace(hour=14, minute=0, second=0, microsecond=0)
        year = now.year
        dec28_10 = timezone.make_aware(datetime(year, 12, 28, 10, 0, 0), timezone.get_current_timezone())

        t1_defaults, t2_defaults = {}, {}
        put_if_exists(Test, t1_defaults, "start_at", tomorrow_14)
        put_if_exists(Test, t1_defaults, "duration_minutes", 30)
        put_if_exists(Test, t1_defaults, "visibility", "public")

        put_if_exists(Test, t2_defaults, "start_at", dec28_10)
        put_if_exists(Test, t2_defaults, "duration_minutes", 45)
        put_if_exists(Test, t2_defaults, "visibility", "public")

        t1, _ = Test.objects.get_or_create(course=c_react, title="React Fundamentals Quiz", defaults=t1_defaults)
        t2, _ = Test.objects.get_or_create(course=c_py,    title="Python Basics Assessment", defaults=t2_defaults)

        # 10) Gamification (XP=7500, 3/6 achievements, streak=15)
        badge_names = ["Quick Starter", "Steady Learner", "Quiz Whiz", "Code Warrior", "Marathoner", "Knowledge Seeker"]
        badges = []
        for name in badge_names:
            b, _ = Badge.objects.get_or_create(organization=org, name=name)
            badges.append(b)

        awards_spec = [
            ("Quick Starter", now - timedelta(days=7)),
            ("Quiz Whiz",    now - timedelta(days=3)),
            ("Code Warrior", now - timedelta(days=1)),  # most recent
        ]
        for name, when in awards_spec:
            b = next(b for b in badges if b.name == name)
            award_defaults = {}
            put_if_exists(BadgeAward, award_defaults, "awarded_at", when)
            put_if_exists(BadgeAward, award_defaults, "reason", f"Awarded: {name}")
            BadgeAward.objects.get_or_create(badge=b, student=student, defaults=award_defaults)

        PointTransaction.objects.get_or_create(student=student, points=2500, defaults={"reason": "Course progress bonus"})
        PointTransaction.objects.get_or_create(student=student, points=3000, defaults={"reason": "Assessment excellence"})
        PointTransaction.objects.get_or_create(student=student, points=2000, defaults={"reason": "Streak reward"})

        streak_defaults = {"current_days": 15}
        put_if_exists(Streak, streak_defaults, "longest_days", 20)
        put_if_exists(Streak, streak_defaults, "last_activity", now)
        Streak.objects.get_or_create(student=student, defaults={**streak_defaults})

        self.stdout.write(self.style.SUCCESS(f"Seed complete for student user pk={student_user_pk}."))
