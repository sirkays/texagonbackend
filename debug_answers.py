import django, os, sys
sys.path.insert(0, os.path.dirname(__file__))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'texagonbackend.settings')
django.setup()

from assessments.models import TestAttempt, TestAnswer, Question, Choice

a = TestAttempt.objects.get(id=222)
print(f"Attempt 222: test={a.test_id}, status={a.status}")
print("JSON answers:")
for item in a.answers:
    qid = item.get("question")
    cid = item.get("choice")
    choice_exists = Choice.objects.filter(id=cid).exists() if cid else False
    q_exists = Question.objects.filter(id=qid).exists() if qid else False
    print(f"  q={qid} (exists={q_exists}), choice={cid} (exists={choice_exists}), awarded={item.get('awarded')}")

print("\nTestAnswer rows:")
rows = TestAnswer.objects.filter(attempt=a).select_related("question")
for r in rows:
    print(f"  q={r.question_id} sc_id={r.selected_choice_id} ids={r.selected_choice_ids} awarded={r.awarded_points} auto={r.is_auto_graded}")

print(f"\nChoices for test {a.test_id} questions:")
choices = Choice.objects.filter(question__test_id=a.test_id).values_list("id", "question_id", "text", "is_correct")
for c in choices[:20]:
    print(f"  Choice id={c[0]}, q={c[1]}, text={c[2][:30]}, correct={c[3]}")
