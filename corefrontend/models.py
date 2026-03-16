from django.db import models
from django.utils.text import slugify


class OurProgram(models.Model):
    name = models.CharField(max_length=225)
    active = models.BooleanField(default=True)


class TutorApplication(models.Model):
    POSITION_CHOICES = [
        ('robotics', 'Robotics & AI Tutor'),
        # Add more as they open
    ]
    STATUS_CHOICES = [
        ('draft', 'Draft'),
        ('submitted', 'Submitted'),
    ]

    # ── Identity ──────────────────────────────────────────────
    email    = models.EmailField()
    position = models.CharField(max_length=50, choices=POSITION_CHOICES)
    status   = models.CharField(max_length=20, choices=STATUS_CHOICES, default='draft')
    current_step = models.PositiveSmallIntegerField(default=1)
    created_at   = models.DateTimeField(auto_now_add=True)
    updated_at   = models.DateTimeField(auto_now=True)

    # ── Step 1 · Personal ─────────────────────────────────────
    full_name       = models.CharField(max_length=255, blank=True)
    dob             = models.DateField(null=True, blank=True)
    gender          = models.CharField(max_length=20, blank=True)
    phone           = models.CharField(max_length=30, blank=True)
    address         = models.TextField(blank=True)
    state_residence = models.CharField(max_length=60, blank=True)
    state_origin    = models.CharField(max_length=60, blank=True)
    nationality     = models.CharField(max_length=60, blank=True)
    identification  = models.CharField(max_length=20, blank=True)
    id_upload       = models.FileField(upload_to='applications/ids/', blank=True, null=True)

    # ── Step 2 · Education ────────────────────────────────────
    education_level  = models.CharField(max_length=20, blank=True)
    course_of_study  = models.CharField(max_length=255, blank=True)
    institution      = models.CharField(max_length=255, blank=True)
    graduation_year  = models.PositiveIntegerField(null=True, blank=True)
    nysc_status      = models.CharField(max_length=20, blank=True)
    degree_fields    = models.JSONField(default=list, blank=True)   # list of strings
    other_degree     = models.CharField(max_length=255, blank=True)
    cv_upload        = models.FileField(upload_to='applications/cvs/', blank=True, null=True)

    # ── Step 3 · Skills ───────────────────────────────────────
    skills           = models.JSONField(default=list, blank=True)   # list of strings
    years_experience = models.CharField(max_length=10, blank=True)
    has_taught       = models.CharField(max_length=5, blank=True)
    teaching_location = models.CharField(max_length=255, blank=True)
    has_laptop       = models.CharField(max_length=5, blank=True)
    has_internet     = models.CharField(max_length=5, blank=True)

    # ── Step 4 · Availability ─────────────────────────────────
    attend_training    = models.CharField(max_length=5, blank=True)
    willing_to_relocate = models.CharField(max_length=5, blank=True)
    work_fulltime      = models.CharField(max_length=5, blank=True)
    start_date         = models.DateField(null=True, blank=True)
    preferred_states   = models.JSONField(default=list, blank=True) # list of strings

    # ── Step 5 · Screening ────────────────────────────────────
    why_techxagon    = models.TextField(blank=True)
    why_select       = models.TextField(blank=True)
    future_robotics  = models.TextField(blank=True)
    service_agreement = models.CharField(max_length=5, blank=True)
    video_upload     = models.FileField(upload_to='applications/videos/', blank=True, null=True)

    class Meta:
        unique_together = ['email', 'position']
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.email} — {self.get_position_display()} [{self.status}]"

    def to_dict(self):
        """Serialise all fields for JSON delivery to the frontend."""
        return {
            'email':    self.email,
            'position': self.position,
            'status':   self.status,
            'current_step': self.current_step,
            # step 1
            'fullName':       self.full_name,
            'dob':            str(self.dob) if self.dob else '',
            'gender':         self.gender,
            'phone':          self.phone,
            'address':        self.address,
            'stateResidence': self.state_residence,
            'stateOrigin':    self.state_origin,
            'nationality':    self.nationality,
            'identification': self.identification,
            'hasIdUpload':    bool(self.id_upload),
            # step 2
            'education':   self.education_level,
            'courseStudy': self.course_of_study,
            'institution': self.institution,
            'graduation':  str(self.graduation_year) if self.graduation_year else '',
            'nyscStatus':  self.nysc_status,
            'degree':      self.degree_fields,
            'otherDegree': self.other_degree,
            'hasCvUpload': bool(self.cv_upload),
            # step 3
            'skills':           self.skills,
            'yearsExp':         self.years_experience,
            'hasTaught':        self.has_taught,
            'teachingLocation': self.teaching_location,
            'laptop':           self.has_laptop,
            'internet':         self.has_internet,
            # step 4
            'training':         self.attend_training,
            'relocation':       self.willing_to_relocate,
            'fulltime':         self.work_fulltime,
            'startDate':        str(self.start_date) if self.start_date else '',
            'preferredStates':  self.preferred_states,
            # step 5
            'whyTechxagon':    self.why_techxagon,
            'whySelect':       self.why_select,
            'futureRobotics':  self.future_robotics,
            'serviceAgreement': self.service_agreement,
            'hasVideoUpload':  bool(self.video_upload),
        }