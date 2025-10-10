# app: ide/utils.py
from academics.models import StudentProfile, TeacherProfile

def user_is_submission_student(user, submission) -> bool:
    sp = submission.student
    return sp and sp.user_id == user.id

def user_teaches_lesson(user, lesson) -> bool:
    # lesson.module.course.teacher is a TeacherProfile
    t = getattr(getattr(getattr(lesson, "module", None), "course", None), "teacher", None)
    return bool(t and t.user_id == user.id)

def user_is_teacher(user) -> bool:
    return hasattr(user, "teacher_profile")
