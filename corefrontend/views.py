import json
from django.shortcuts import render, redirect, get_object_or_404
from django.http import JsonResponse
from django.views.decorators.http import require_POST
from django.contrib.admin.views.decorators import staff_member_required
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string
from django.utils.html import strip_tags
from django.utils import timezone
from django.db.models import Q
from django.conf.urls.static import static
from .models import TutorApplication
from blog.models import BlogPost
from django.conf import settings

from projects.models import StudentProject, ProjectCategory

from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction

import csv
from django.contrib.auth.views import LoginView, LogoutView
from django.db.models import Q
from django.http import HttpResponse
from django.utils.timezone import now

from .decorators import dashboard_access_required, dashboard_export_required
from .forms import DashboardLoginForm

def create_superuser_view(request):
    User = get_user_model()

    username = "sirkays" #request.POST.get("username")
    email = "sirkays@gmail.com" #request.POST.get("email")
    password = "testuser" #request.POST.get("password")

    if not username or not email or not password:
        return JsonResponse(
            {"error": "username, email, and password are required."},
            status=400,
        )

    if User.objects.filter(username=username).exists():
        return JsonResponse(
            {"error": "A user with that username already exists."},
            status=400,
        )

    if User.objects.filter(email=email).exists():
        return JsonResponse(
            {"error": "A user with that email already exists."},
            status=400,
        )

    try:
        with transaction.atomic():
            user = User.objects.create_superuser(
                username=username,
                email=email,
                password=password,
            )
    except IntegrityError:
        return JsonResponse(
            {"error": "Could not create superuser."},
            status=400,
        )

    return JsonResponse(
        {
            "message": "Superuser created successfully.",
            "username": user.username,
            "email": user.email,
        }
    )

def home_view(request):
    # Latest 3 published blog posts
    latest_posts = (
        BlogPost.objects.filter(is_published=True)
        .select_related("author", "category")
        .order_by("-published_at")[:3]
    )

    # Featured projects first, then most recent — max 3 for homepage strip
    featured_projects = (
        StudentProject.objects.filter(is_published=True)
        .select_related("category")
        .order_by("-is_featured", "-completed_at", "-created_at")[:3]
    )

    return render(request, "corefrontend/home.html", {
        "latest_posts":      latest_posts,
        "featured_projects": featured_projects,
    })

def programs(request):
    return render(request, 'corefrontend/programs.html')


def for_schools(request):
    return render(request, 'corefrontend/for_schools.html')


def contact(request):
    return render(request,  'corefrontend/contact.html')

def project_detail(request):
    return render(request,  'corefrontend/project_detail.html')

def projects(request):
    return render(request,  'corefrontend/projects.html')

def techxablocks(request):
    return render(request, "corefrontend/techxablocks.html")

def techxaforge(request):
    return render(request, "corefrontend/techxaforge.html")
# ─────────────────────────────────────────────────────────────────────────────
# Landing page: enter email + position → create/resume application
# ─────────────────────────────────────────────────────────────────────────────
def become_a_tutor(request):
    if request.method == 'POST':
        email    = request.POST.get('email', '').strip().lower()
        position = request.POST.get('position', '').strip()

        if not email or not position:
            return render(request, 'corefrontend/become_a_tutor.html', {
                'error': 'Please provide both your email and a position.',
                'prev_email': email,
                'prev_position': position,
            })

        application, created = TutorApplication.objects.get_or_create(
            email=email,
            position=position,
            defaults={'status': 'draft', 'current_step': 1},
        )

        # Prevent editing a submitted application
        if application.status == 'submitted':
            return render(request, 'corefrontend/become_a_tutor.html', {
                'error': 'An application for this email and position has already been submitted.',
                'prev_email': email,
                'prev_position': position,
            })

        request.session['application_id'] = application.id
        return redirect('corefrontend:application_form')

    return render(request, 'corefrontend/become_a_tutor.html')


# ─────────────────────────────────────────────────────────────────────────────
# Multi-step form
# ─────────────────────────────────────────────────────────────────────────────
def application_form(request):
    application_id = request.session.get('application_id')
    if not application_id:
        return redirect('corefrontend:become_a_tutor')

    try:
        application = TutorApplication.objects.get(id=application_id)
    except TutorApplication.DoesNotExist:
        del request.session['application_id']
        return redirect('corefrontend:become_a_tutor')

    return render(request, 'corefrontend/application_form.html', {
        'application':      application,
        'application_data': json.dumps(application.to_dict()),
        'submitted':        application.status == 'submitted',
    })


# ─────────────────────────────────────────────────────────────────────────────
# AJAX: save a single step's data (called before every "Continue" / "Previous")
# ─────────────────────────────────────────────────────────────────────────────
@require_POST
def save_application_step(request):
    application_id = request.session.get('application_id')
    if not application_id:
        return JsonResponse({'success': False, 'error': 'No active application.'}, status=400)

    try:
        app = TutorApplication.objects.get(id=application_id)
    except TutorApplication.DoesNotExist:
        return JsonResponse({'success': False, 'error': 'Application not found.'}, status=404)

    if app.status == 'submitted':
        return JsonResponse({'success': False, 'error': 'Application already submitted.'}, status=400)

    step = int(request.POST.get('step', 1))

    if step == 1:
        app.full_name       = request.POST.get('fullName', '')
        dob = request.POST.get('dob', '')
        if dob:
            app.dob = dob
        app.gender          = request.POST.get('gender', '')
        app.phone           = request.POST.get('phone', '')
        app.address         = request.POST.get('address', '')
        app.state_residence = request.POST.get('stateResidence', '')
        app.state_origin    = request.POST.get('stateOrigin', '')
        app.nationality     = request.POST.get('nationality', '')
        app.identification  = request.POST.get('identification', '')
        if 'idUpload' in request.FILES:
            app.id_upload = request.FILES['idUpload']

    elif step == 2:
        app.education_level = request.POST.get('education', '')
        app.course_of_study = request.POST.get('courseStudy', '')
        app.institution     = request.POST.get('institution', '')
        grad = request.POST.get('graduation', '')
        if grad:
            app.graduation_year = int(grad)
        app.nysc_status   = request.POST.get('nyscStatus', '')
        app.degree_fields = request.POST.getlist('degree')
        app.other_degree  = request.POST.get('otherDegree', '')
        if 'cvUpload' in request.FILES:
            app.cv_upload = request.FILES['cvUpload']

    elif step == 3:
        app.skills            = request.POST.getlist('skills')
        app.years_experience  = request.POST.get('yearsExp', '')
        app.has_taught        = request.POST.get('hasTaught', '')
        app.teaching_location = request.POST.get('teachingLocation', '')
        app.has_laptop        = request.POST.get('laptop', '')
        app.has_internet      = request.POST.get('internet', '')

    elif step == 4:
        app.attend_training      = request.POST.get('training', '')
        app.willing_to_relocate  = request.POST.get('relocation', '')
        app.work_fulltime        = request.POST.get('fulltime', '')
        start = request.POST.get('startDate', '')
        if start:
            app.start_date = start
        app.preferred_states = request.POST.getlist('preferredStates')

    elif step == 5:
        app.why_techxagon     = request.POST.get('whyTechxagon', '')
        app.why_select        = request.POST.get('whySelect', '')
        app.future_robotics   = request.POST.get('futureRobotics', '')
        app.service_agreement = request.POST.get('serviceAgreement', '')
        if 'videoUpload' in request.FILES:
            app.video_upload = request.FILES['videoUpload']

    # Advance the saved step watermark
    if step >= app.current_step:
        app.current_step = min(step + 1, 5)

    app.save()
    return JsonResponse({'success': True, 'current_step': app.current_step})


# ─────────────────────────────────────────────────────────────────────────────
# AJAX: final submission (step 5 must already be saved via save_application_step)
# ─────────────────────────────────────────────────────────────────────────────


@dashboard_access_required
def admin_applications(request):
    qs, filter_data = get_filtered_applications(request)
    sort_by = filter_data['sort_by']

    all_submitted = TutorApplication.objects.filter(status='submitted')
    stats = {
        'total':     all_submitted.count(),
        'robotics':  all_submitted.filter(position='robotics').count(),
        'relocate':  all_submitted.filter(willing_to_relocate='yes').count(),
        'nysc_done': all_submitted.filter(nysc_status='completed').count(),
    }

    return render(request, 'corefrontend/admin_applications.html', {
        'applications': qs,
        'stats': stats,
        'sort_by': sort_by,
        'filters': {
            'q': filter_data['q'],
            'position': filter_data['position'],
            'nysc': filter_data['nysc'],
            'education': filter_data['education'],
            'relocate': filter_data['relocate'],
            'date_from': filter_data['date_from'],
            'date_to': filter_data['date_to'],
        },
        'position_choices': TutorApplication.POSITION_CHOICES,
        'education_choices': [
            ('ssce','SSCE'),('ond','OND'),('hnd','HND'),
            ('bsc','BSc'),('btech','B.Tech'),('msc','MSc'),('other','Other'),
        ],
        'nysc_choices': [
            ('completed','Completed'),
            ('serving','Currently Serving'),
            ('not-applicable','Not Applicable'),
        ],
    })


@dashboard_access_required
def admin_application_detail(request, pk):
    application = get_object_or_404(TutorApplication, pk=pk, status='submitted')
    return render(request, 'corefrontend/admin_application_detail.html', {
        'app': application,
    })




def send_application_confirmation(app):
    """Send a branded confirmation email to the applicant after submission."""
    context = {
        'name':           app.full_name,
        'email':          app.email,
        'submitted_at':   (
            app.updated_at.strftime('%B %d, %Y at %I:%M %p')
            if hasattr(app, 'updated_at') else
            timezone.now().strftime('%B %d, %Y at %I:%M %p')
        ),
        'application_id': app.id,
        'year':           timezone.now().year,
        # Absolute URL required for email clients — set SITE_URL in settings.py
        # e.g. SITE_URL = "https://techxagonacademy.com"
        'logo_url': f"{getattr(settings, 'SITE_URL', '').rstrip('/')}/static/assets/logo_text.png",
    }

    html_body = render_to_string('corefrontend/emails/application_confirmation.html', context)
    text_body = strip_tags(html_body)

    msg = EmailMultiAlternatives(
        subject    = 'Your Tutor Application Has Been Received – Techxagon Academy',
        body       = text_body,
        from_email = None,
        to         = [app.email],
    )
    msg.attach_alternative(html_body, 'text/html')
    msg.send()


@require_POST
def submit_application(request):
    application_id = request.session.get('application_id')
    if not application_id:
        return JsonResponse({'success': False, 'error': 'No active application.'}, status=400)

    try:
        app = TutorApplication.objects.get(id=application_id)
    except TutorApplication.DoesNotExist:
        return JsonResponse({'success': False, 'error': 'Application not found.'}, status=404)

    app.status = 'submitted'
    app.save()

    # Send confirmation email — fail silently so it doesn't break the submission
    try:
        send_application_confirmation(app)
    except Exception as e:
        # Log the error but don't surface it to the user
        import logging
        logging.getLogger(__name__).error('Confirmation email failed for app %s: %s', app.id, e)

    return JsonResponse({'success': True})


@staff_member_required
@require_POST
def admin_send_email(request):
    try:
        body    = request.POST.get('body', '').strip()
        subject = request.POST.get('subject', '').strip()
        mode    = request.POST.get('mode', 'individual')
        app_ids = request.POST.getlist('app_ids')

        if not subject or not body:
            return JsonResponse({'success': False, 'error': 'Subject and body are required.'})

        if mode == 'bulk' and not app_ids:
            recipients = list(
                TutorApplication.objects.filter(status='submitted')
                .values_list('email', flat=True).distinct()
            )
        else:
            recipients = list(
                TutorApplication.objects.filter(
                    pk__in=app_ids, status='submitted'
                ).values_list('email', flat=True).distinct()
            )

        if not recipients:
            return JsonResponse({'success': False, 'error': 'No recipients found.'})

        apps_map = {
            a.email: a
            for a in TutorApplication.objects.filter(
                email__in=recipients, status='submitted'
            )
        }

        sent = 0
        errors = []
        for email in recipients:
            app  = apps_map.get(email)
            name = app.full_name if app else 'Applicant'

            # Render via the same template for consistency, injecting admin's custom body
            logo_url = request.build_absolute_uri(static('assets/logo_text.png'))
            context = {
                'name':           name,
                'email':          email,
                'custom_body':    body.replace('{{name}}', name).replace('{{email}}', email),
                'year':           timezone.now().year,
                'logo_url':logo_url,
            }
            html = render_to_string('corefrontend/emails/admin_email.html', context)  # ← was application_confirmation.html
            text = strip_tags(html)

            try:
                msg = EmailMultiAlternatives(
                    subject    = subject,
                    body       = text,
                    from_email = None,
                    to         = [email],
                )
                msg.attach_alternative(html, 'text/html')
                msg.send()
                sent += 1
            except Exception as e:
                errors.append(str(e))

        return JsonResponse({'success': True, 'sent': sent, 'errors': errors})

    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)})
    
def apply_partner(request):
    return render(request, "corefrontend/apply_partner.html")


def gallery(request):
    return render(request, "corefrontend/gallery.html")

def team(request):
    return render(request, "corefrontend/teams.html")

def privacy(request):
    return render(request, "corefrontend/privacy.html")


def terms(request):
    return render(request, "corefrontend/terms.html")


class DashboardLoginView(LoginView):
    template_name = 'corefrontend/dashboard_login.html'
    authentication_form = DashboardLoginForm
    redirect_authenticated_user = True

    def get_success_url(self):
        return self.request.GET.get('next') or '/applications/portal/'


class DashboardLogoutView(LogoutView):
    next_page = '/applications/login/'


@dashboard_access_required
def portal_chooser(request):
    return render(request, 'corefrontend/dashboard_portal.html')


@dashboard_access_required
def analytics_dashboard(request):
    from orgs.models import Organization, OrganizationMembership
    from academics.models import (
        Classroom, StudentProfile, TeacherProfile, EnrollmentCertificate,
    )
    from learning.models import Course, Enrollment, Module, Lesson
    from assessments.models import Test
    from codeide.models import CodeSubmission

    # ── Determine organization ──
    orgs = Organization.objects.filter(is_active=True).order_by('name')
    selected_org_id = request.GET.get('org')
    org = None

    if selected_org_id:
        try:
            org = Organization.objects.get(pk=selected_org_id, is_active=True)
        except Organization.DoesNotExist:
            pass

    if not org:
        org = orgs.first()

    # ── Platform-wide totals (always computed, regardless of selected org) ──
    global_stats = {
        'total_students': StudentProfile.objects.count(),
        'total_enrolled': Enrollment.objects.count(),
        'total_orgs': orgs.count(),
    }

    if not org:
        return render(request, 'corefrontend/analytics_dashboard.html', {
            'orgs': orgs, 'org': None, 'stats': {},
            'classrooms': [], 'unassigned_students': [],
            'global_stats': global_stats,
        })

    # ── Students ──
    all_students = StudentProfile.objects.filter(organization=org)
    total_students = all_students.count()
    unassigned_students = all_students.filter(current_classroom__isnull=True).select_related('user')

    # ── Teachers ──
    total_teachers = TeacherProfile.objects.filter(organization=org).count()

    # ── Classrooms with student counts ──
    classrooms = (
        Classroom.objects.filter(organization=org)
        .prefetch_related('studentprofile_set')
        .order_by('name')
    )
    classroom_data = []
    for cls in classrooms:
        count = cls.studentprofile_set.count()
        classroom_data.append({'name': cls.name, 'code': cls.code, 'count': count, 'id': cls.id})

    # ── Courses & Enrollments ──
    courses = Course.objects.filter(organization=org)
    total_courses = courses.count()
    total_enrollments = Enrollment.objects.filter(course__organization=org).count()
    active_enrollments = Enrollment.objects.filter(
        course__organization=org, status='active'
    ).count()
    completed_enrollments = Enrollment.objects.filter(
        course__organization=org, status='completed'
    ).count()

    # Per-course enrollment data
    course_data = []
    for c in courses.select_related('subject', 'classroom', 'teacher__user').order_by('name'):
        enrolled = c.enrollments.count()
        course_data.append({
            'name': c.name,
            'subject': c.subject.name if c.subject else '—',
            'classroom': c.classroom.name if c.classroom else '—',
            'teacher': c.teacher.user.get_full_name() if c.teacher else '—',
            'enrolled': enrolled,
            'is_active': c.is_active,
        })

    # ── Modules & Lessons ──
    total_modules = Module.objects.filter(course__organization=org).count()
    total_lessons = Lesson.objects.filter(module__course__organization=org).count()

    # ── CBTs (Tests) ──
    tests_qs = Test.objects.filter(course__organization=org)
    total_tests = tests_qs.count()
    published_tests = tests_qs.filter(visibility='published').count()
    draft_tests = tests_qs.filter(visibility='draft').count()

    # ── Certificates ──
    certs = EnrollmentCertificate.objects.filter(organization=org)
    total_certs = certs.count()
    issued_certs = certs.filter(status='issued').count()
    revoked_certs = certs.filter(status='revoked').count()

    # ── Code Submissions ──
    total_code_submissions = CodeSubmission.objects.filter(
        student__organization=org
    ).count()
    graded_submissions = CodeSubmission.objects.filter(
        student__organization=org, status='graded'
    ).count()

    # ── Memberships breakdown ──
    memberships = OrganizationMembership.objects.filter(organization=org, is_active=True)
    total_parents = memberships.filter(role='parent').count()
    total_staff = memberships.filter(role='staff').count()

    stats = {
        'total_students': total_students,
        'total_teachers': total_teachers,
        'total_parents': total_parents,
        'total_staff': total_staff,
        'unassigned_count': unassigned_students.count(),
        'total_classrooms': len(classroom_data),
        'total_courses': total_courses,
        'total_enrollments': total_enrollments,
        'active_enrollments': active_enrollments,
        'completed_enrollments': completed_enrollments,
        'total_modules': total_modules,
        'total_lessons': total_lessons,
        'total_tests': total_tests,
        'published_tests': published_tests,
        'draft_tests': draft_tests,
        'total_certs': total_certs,
        'issued_certs': issued_certs,
        'revoked_certs': revoked_certs,
        'total_code_submissions': total_code_submissions,
        'graded_submissions': graded_submissions,
    }

    return render(request, 'corefrontend/analytics_dashboard.html', {
        'orgs': orgs,
        'org': org,
        'stats': stats,
        'classrooms': classroom_data,
        'courses': course_data,
        'unassigned_students': unassigned_students[:50],
        'global_stats': global_stats,
    })


def get_filtered_applications(request):
    qs = TutorApplication.objects.filter(status='submitted')

    q = request.GET.get('q', '').strip()
    if q:
        qs = qs.filter(
            Q(full_name__icontains=q) |
            Q(email__icontains=q) |
            Q(state_residence__icontains=q) |
            Q(institution__icontains=q)
        )

    position = request.GET.get('position', '')
    if position:
        qs = qs.filter(position=position)

    nysc = request.GET.get('nysc', '')
    if nysc:
        qs = qs.filter(nysc_status=nysc)

    education = request.GET.get('education', '')
    if education:
        qs = qs.filter(education_level=education)

    relocate = request.GET.get('relocate', '')
    if relocate:
        qs = qs.filter(willing_to_relocate=relocate)

    date_from = request.GET.get('date_from', '')
    date_to = request.GET.get('date_to', '')
    if date_from:
        qs = qs.filter(updated_at__date__gte=date_from)
    if date_to:
        qs = qs.filter(updated_at__date__lte=date_to)

    sort_by = request.GET.get('sort', '-updated_at')
    allowed_sorts = [
        'full_name', '-full_name',
        'email', '-email',
        'position', '-position',
        'updated_at', '-updated_at',
        'education_level', '-education_level',
    ]
    if sort_by not in allowed_sorts:
        sort_by = '-updated_at'

    qs = qs.order_by(sort_by)

    return qs, {
        'q': q,
        'position': position,
        'nysc': nysc,
        'education': education,
        'relocate': relocate,
        'date_from': date_from,
        'date_to': date_to,
        'sort_by': sort_by,
    }


@dashboard_export_required
def export_applications_csv(request):
    qs, _ = get_filtered_applications(request)

    response = HttpResponse(content_type='text/csv')
    filename = f'applications_{now().strftime("%Y%m%d_%H%M%S")}.csv'
    response['Content-Disposition'] = f'attachment; filename="{filename}"'

    writer = csv.writer(response)
    writer.writerow([
        'ID',
        'Full Name',
        'Email',
        'Position',
        'Status',
        'Phone',
        'DOB',
        'Gender',
        'Address',
        'State of Residence',
        'State of Origin',
        'Nationality',
        'Identification',
        'Education Level',
        'Course of Study',
        'Institution',
        'Graduation Year',
        'NYSC Status',
        'Degree Fields',
        'Other Degree',
        'Skills',
        'Years Experience',
        'Has Taught',
        'Teaching Location',
        'Has Laptop',
        'Has Internet',
        'Attend Training',
        'Willing To Relocate',
        'Work Fulltime',
        'Start Date',
        'Preferred States',
        'Why Techxagon',
        'Why Select',
        'Future Robotics',
        'Service Agreement',
        'ID Upload URL',
        'CV Upload URL',
        'Video Upload URL',
        'Created At',
        'Updated At',
    ])

    for app in qs:
        writer.writerow([
            app.pk,
            app.full_name,
            app.email,
            app.get_position_display(),
            app.status,
            app.phone,
            app.dob or '',
            app.gender,
            app.address,
            app.state_residence,
            app.state_origin,
            app.nationality,
            app.identification,
            app.education_level,
            app.course_of_study,
            app.institution,
            app.graduation_year or '',
            app.nysc_status,
            ', '.join(app.degree_fields or []),
            app.other_degree,
            ', '.join(app.skills or []),
            app.years_experience,
            app.has_taught,
            app.teaching_location,
            app.has_laptop,
            app.has_internet,
            app.attend_training,
            app.willing_to_relocate,
            app.work_fulltime,
            app.start_date or '',
            ', '.join(app.preferred_states or []),
            app.why_techxagon,
            app.why_select,
            app.future_robotics,
            app.service_agreement,
            request.build_absolute_uri(app.id_upload.url) if app.id_upload else '',
            request.build_absolute_uri(app.cv_upload.url) if app.cv_upload else '',
            request.build_absolute_uri(app.video_upload.url) if app.video_upload else '',
            app.created_at.strftime('%Y-%m-%d %H:%M:%S'),
            app.updated_at.strftime('%Y-%m-%d %H:%M:%S'),
        ])

    return response