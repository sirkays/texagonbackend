import os
import django
from datetime import timedelta
from decimal import Decimal

# Configure Django Environment
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "texagonbackend.settings")
django.setup()

from django.utils import timezone
from django.contrib.auth import get_user_model
from django.db import transaction

from orgs.models import Organization, OrganizationMembership, AcademicSession
from billing.models import SubscriptionPlan, OrganizationSubscription, UserAccountSubscription
from academics.models import Subject, Classroom, TeacherProfile, StudentProfile, EnrollmentCertificate, StudentEnrollmentCertificateApproval
from learning.models import Course, CoursePassCriteria, Enrollment
from accounts.models import AdminAccess

User = get_user_model()

def seed_nimet_data():
    print("[*] Starting NiMet Seed Data Process...")

    with transaction.atomic():
        # 1. Create / Get NiMet Organization
        org, created = Organization.objects.get_or_create(
            slug="nimet",
            defaults={
                "name": "Nigerian Meteorological Agency (NiMet)",
                "address": "National Weather Forecasting & Climate Research Centre, Bill Clinton Drive, Nnamdi Azikiwe International Airport",
                "city": "Abuja",
                "state": "FCT",
                "country": "Nigeria",
                "contact_email": "info@nimet.gov.ng",
                "contact_phone": "+234 9 291 9437",
                "is_active": True,
                "year": "2026",
                "allow_unsubscribed_users": True,
                "allow_public_cert_request": True,
                "video_conferencing": Organization.VideoConferencing.KONNECT,
            }
        )
        if not created:
            org.name = "Nigerian Meteorological Agency (NiMet)"
            org.is_active = True
            org.allow_unsubscribed_users = True
            org.allow_public_cert_request = True
            org.save()
        print(f"[OK] Organization ready: {org.name} (id={org.id})")

        # 2. Create Academic Session
        session, _ = AcademicSession.objects.get_or_create(
            organization=org,
            name="2025/2026 Training Session",
            defaults={
                "start_date": timezone.now().date() - timedelta(days=90),
                "end_date": timezone.now().date() + timedelta(days=275),
                "is_current": True,
            }
        )
        session.is_current = True
        session.save()
        print(f"[OK] Academic Session: {session.name}")

        # 3. Create 12-Month Subscription Plan & Org Subscription
        plan, _ = SubscriptionPlan.objects.get_or_create(
            name="NiMet 12-Month Institutional Plan",
            defaults={
                "price": Decimal("0.00"),
                "billing_period": "365",
                "student_limit": 0,
                "is_test": False,
            }
        )
        
        org_sub, _ = OrganizationSubscription.objects.get_or_create(
            organization=org,
            plan=plan,
            defaults={
                "start_date": timezone.now().date() - timedelta(days=30),
                "end_date": timezone.now().date() + timedelta(days=335),
                "status": OrganizationSubscription.Status.ACTIVE,
                "auto_renew": True,
                "payment_method": "Enterprise Agreement",
            }
        )
        org_sub.status = OrganizationSubscription.Status.ACTIVE
        org_sub.end_date = timezone.now().date() + timedelta(days=335)
        org_sub.save()
        print(f"[OK] 12-Month Org Subscription Active: end_date={org_sub.end_date}")

        # 4. Admin Account
        admin_email = "admin@nimet.gov.ng"
        admin_pass = "Password123!"
        admin_user = User.objects.filter(email=admin_email).first()
        if not admin_user:
            admin_user = User.objects.create_superuser(
                email=admin_email,
                username="nimet_admin",
                password=admin_pass,
                first_name="NiMet",
                last_name="Administrator",
                primary_org=org,
                is_active=True,
            )
        else:
            admin_user.set_password(admin_pass)
            admin_user.primary_org = org
            admin_user.is_active = True
            admin_user.is_staff = True
            admin_user.is_superuser = True
            admin_user.save()

        OrganizationMembership.objects.get_or_create(
            user=admin_user,
            organization=org,
            role=OrganizationMembership.Role.ADMIN,
            defaults={"is_active": True}
        )

        admin_access, _ = AdminAccess.objects.get_or_create(
            user=admin_user,
            defaults={
                "selected_organization": org,
                "active": True,
                "super_user": True,
            }
        )
        admin_access.organizations.add(org)
        admin_access.selected_organization = org
        admin_access.active = True
        admin_access.super_user = True
        admin_access.save()
        print(f"[OK] Admin Account created/updated: {admin_email} / {admin_pass}")

        # 5. Teacher Account
        teacher_email = "teacher@nimet.gov.ng"
        teacher_pass = "Password123!"
        teacher_user = User.objects.filter(email=teacher_email).first()
        if not teacher_user:
            teacher_user = User.objects.create_user(
                email=teacher_email,
                username="nimet_teacher",
                password=teacher_pass,
                first_name="Dr. Ibrahim",
                last_name="Mohammed",
                primary_org=org,
                is_active=True,
            )
        else:
            teacher_user.set_password(teacher_pass)
            teacher_user.primary_org = org
            teacher_user.is_active = True
            teacher_user.save()

        OrganizationMembership.objects.get_or_create(
            user=teacher_user,
            organization=org,
            role=OrganizationMembership.Role.TEACHER,
            defaults={"is_active": True}
        )

        teacher_profile, _ = TeacherProfile.objects.get_or_create(
            user=teacher_user,
            defaults={
                "organization": org,
                "bio": "Senior Meteorologist & Chief Instructor in Synoptic and Aeronautical Meteorology.",
                "experience": 12,
            }
        )
        teacher_profile.organization = org
        teacher_profile.save()
        print(f"[OK] Teacher Account created/updated: {teacher_email} / {teacher_pass}")

        # 6. Student Account
        student_email = "student@nimet.gov.ng"
        student_pass = "Password123!"
        student_user = User.objects.filter(email=student_email).first()
        if not student_user:
            student_user = User.objects.create_user(
                email=student_email,
                username="nimet_student",
                password=student_pass,
                first_name="Fatima",
                last_name="Bello",
                primary_org=org,
                is_active=True,
            )
        else:
            student_user.set_password(student_pass)
            student_user.primary_org = org
            student_user.is_active = True
            student_user.save()

        OrganizationMembership.objects.get_or_create(
            user=student_user,
            organization=org,
            role=OrganizationMembership.Role.STUDENT,
            defaults={"is_active": True}
        )

        student_profile, _ = StudentProfile.objects.get_or_create(
            user=student_user,
            defaults={
                "organization": org,
                "gender": "Female",
            }
        )
        student_profile.organization = org
        student_profile.save()

        # 12 Month User Subscription for Student
        UserAccountSubscription.objects.get_or_create(
            organization=org,
            user=student_user,
            defaults={
                "plan": plan,
                "status": UserAccountSubscription.Status.ACTIVE,
                "start_at": timezone.now() - timedelta(days=30),
                "end_at": timezone.now() + timedelta(days=335),
                "auto_renew": True,
            }
        )
        print(f"[OK] Student Account created/updated: {student_email} / {student_pass}")

        # 7. Subject & Classroom
        subject, _ = Subject.objects.get_or_create(
            organization=org,
            name="Synoptic Meteorology & Forecasting",
            defaults={"code": "MET-301"}
        )

        classroom, _ = Classroom.objects.get_or_create(
            organization=org,
            name="Meteorological Cadet Officers 2026",
            defaults={"code": "MET-CADET", "class_type": "public"}
        )
        classroom.teachers.add(teacher_user)
        student_profile.current_classroom = classroom
        student_profile.save()
        print(f"[OK] Classroom & Subject ready: {classroom.name} | {subject.name}")

        # 8. Course for Teacher
        course, _ = Course.objects.get_or_create(
            organization=org,
            subject=subject,
            classroom=classroom,
            teacher=teacher_profile,
            defaults={
                "name": "Introduction to Synoptic Meteorology & Weather Forecasting",
                "description": "Comprehensive foundation in synoptic chart analysis, atmospheric thermodynamics, and operational weather forecasting standards.",
                "is_active": True,
                "general_activation": True,
                "general_activation_date": timezone.now() + timedelta(days=365),
                "course_type": "public",
            }
        )
        CoursePassCriteria.objects.get_or_create(
            course=course,
            defaults={
                "no_of_cbt": 5,
                "no_of_code_submission": 0,
                "total_pass_mark_cbt": 70,
                "total_pass_mark_code": 0,
            }
        )
        print(f"[OK] Course Created: '{course.name}' for {teacher_email}")

        # 8b. Create Module under Course
        from learning.models import Module, Lesson

        module, _ = Module.objects.get_or_create(
            course=course,
            order=1,
            defaults={
                "name": "Fundamentals of Atmospheric Dynamics & Synoptic Charts",
                "description": "Comprehensive introduction to atmospheric pressure distribution, Coriolis deflection, isobaric analysis, and regional weather systems.",
                "difficulty": Module.DifficultyLevel.BEGINNER,
                "estimated_duration_in_minutes": 90,
                "active": True,
            }
        )
        module.name = "Fundamentals of Atmospheric Dynamics & Synoptic Charts"
        module.description = "Comprehensive introduction to atmospheric pressure distribution, Coriolis deflection, isobaric analysis, and regional weather systems."
        module.difficulty = Module.DifficultyLevel.BEGINNER
        module.estimated_duration_in_minutes = 90
        module.active = True
        module.save()
        print(f"[OK] Module Created: '{module.name}' (order={module.order})")

        # 8c. Create 2 Lessons under Module
        lesson_1, _ = Lesson.objects.get_or_create(
            module=module,
            order=1,
            defaults={
                "name": "Atmospheric Pressure Systems & Isobaric Chart Analysis",
                "content_type": Lesson.ContentType.VIDEO,
                "url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
                "duration_seconds": 1800,
                "meta": {
                    "description": "Comprehensive guide on reading surface isobar charts, identifying cyclones, anticyclones, and pressure troughs across West Africa."
                },
                "active": True,
            }
        )
        lesson_1.name = "Atmospheric Pressure Systems & Isobaric Chart Analysis"
        lesson_1.content_type = Lesson.ContentType.VIDEO
        lesson_1.url = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
        lesson_1.duration_seconds = 1800
        lesson_1.meta = {
            "description": "Comprehensive guide on reading surface isobar charts, identifying cyclones, anticyclones, and pressure troughs across West Africa."
        }
        lesson_1.active = True
        lesson_1.save()
        print(f"[OK] Lesson 1 Created: '{lesson_1.name}' (content_type={lesson_1.content_type})")

        lesson_2, _ = Lesson.objects.get_or_create(
            module=module,
            order=2,
            defaults={
                "name": "Tropical Meteorology & Inter-Tropical Discontinuity (ITD)",
                "content_type": Lesson.ContentType.PDF,
                "url": "https://nimet.gov.ng/publications/synoptic-guidelines-2026.pdf",
                "duration_seconds": 2400,
                "meta": {
                    "description": "In-depth study of the seasonal migration of the ITD, monsoon troughs, and squall lines across the Nigerian airspace."
                },
                "active": True,
            }
        )
        lesson_2.name = "Tropical Meteorology & Inter-Tropical Discontinuity (ITD)"
        lesson_2.content_type = Lesson.ContentType.PDF
        lesson_2.url = "https://nimet.gov.ng/publications/synoptic-guidelines-2026.pdf"
        lesson_2.duration_seconds = 2400
        lesson_2.meta = {
            "description": "In-depth study of the seasonal migration of the ITD, monsoon troughs, and squall lines across the Nigerian airspace."
        }
        lesson_2.active = True
        lesson_2.save()
        print(f"[OK] Lesson 2 Created: '{lesson_2.name}' (content_type={lesson_2.content_type})")

        # 9. Enroll Student in Course
        enrollment, _ = Enrollment.objects.get_or_create(
            student=student_profile,
            course=course,
            defaults={
                "academic_session": session,
                "status": Enrollment.Status.ACTIVE,
                "progress_pct": Decimal("100.00"),
                "completed_at": timezone.now(),
            }
        )
        enrollment.status = Enrollment.Status.ACTIVE
        enrollment.progress_pct = Decimal("100.00")
        enrollment.save()
        print(f"[OK] Student Enrolled: {student_email} in {course.name} (Status: ACTIVE, Progress: 100%)")

        # 10. Generate NiMet Certificate
        cert = EnrollmentCertificate.objects.filter(enrollment=enrollment).first()
        if not cert:
            cert = EnrollmentCertificate.objects.create(
                organization=org,
                enrollment=enrollment,
                student=student_profile,
                course=course,
                title="Certificate of Meteorological Achievement & Completion",
                description="In recognition of outstanding academic performance and successful completion of the prescribed training in Synoptic Meteorology and Operational Forecasting.",
                status="issued",
                acquired_at=timezone.now(),
                download_after_days=0,
                issued_by_user=admin_user,
                meta={"template": "nimet"},
            )
        else:
            cert.status = "issued"
            cert.meta = {"template": "nimet"}
            cert.download_after_days = 0
            cert.save()

        # Approve Certificate by Teacher and Admin
        StudentEnrollmentCertificateApproval.objects.get_or_create(
            certificate=cert,
            user=teacher_user,
            user_type="teacher",
            defaults={"approval": True}
        )
        StudentEnrollmentCertificateApproval.objects.get_or_create(
            certificate=cert,
            user=admin_user,
            user_type="admin",
            defaults={"approval": True}
        )
        print(f"[OK] NiMet Certificate Issued: Number={cert.number} (Fully Approved)")

    print("\n[SUCCESS] NiMet Seeding Completed Successfully!")
    print("=" * 60)
    print("ADMIN LOGIN:    admin@nimet.gov.ng   / Password123!")
    print("TEACHER LOGIN:  teacher@nimet.gov.ng / Password123!")
    print("STUDENT LOGIN:  student@nimet.gov.ng / Password123!")
    print("=" * 60)

if __name__ == "__main__":
    seed_nimet_data()
