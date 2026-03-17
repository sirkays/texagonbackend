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


@staff_member_required
def admin_applications(request):
    qs = TutorApplication.objects.filter(status='submitted')

    # ── Search ──────────────────────────────────────────────────────────────
    q = request.GET.get('q', '').strip()
    if q:
        qs = qs.filter(
            Q(full_name__icontains=q) |
            Q(email__icontains=q) |
            Q(state_residence__icontains=q) |
            Q(institution__icontains=q)
        )

    # ── Filters ─────────────────────────────────────────────────────────────
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
    date_to   = request.GET.get('date_to', '')
    if date_from:
        qs = qs.filter(updated_at__date__gte=date_from)
    if date_to:
        qs = qs.filter(updated_at__date__lte=date_to)

    # ── Sort ────────────────────────────────────────────────────────────────
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

    # ── Stats ────────────────────────────────────────────────────────────────
    all_submitted = TutorApplication.objects.filter(status='submitted')
    stats = {
        'total':     all_submitted.count(),
        'robotics':  all_submitted.filter(position='robotics').count(),
        'relocate':  all_submitted.filter(willing_to_relocate='yes').count(),
        'nysc_done': all_submitted.filter(nysc_status='completed').count(),
    }

    return render(request, 'corefrontend/admin_applications.html', {
        'applications': qs,
        'stats':        stats,
        'sort_by':      sort_by,
        'filters': {
            'q':         q,
            'position':  position,
            'nysc':      nysc,
            'education': education,
            'relocate':  relocate,
            'date_from': date_from,
            'date_to':   date_to,
        },
        'position_choices':  TutorApplication.POSITION_CHOICES,
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


@staff_member_required
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


