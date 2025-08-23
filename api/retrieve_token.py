from .models import SessionToken

def get_token_from_header(request):
    print(request.META)
    token = request.META.get("HTTP_X_SESSION_TOKEN")
    print(token, " tk")
    if not token:
        auth = request.META.get("HTTP_AUTHORIZATION", "")
        if auth.startswith("Session "):
            token = auth[len("Session "):].strip()

    if not token:
        return False

    try:
        st = SessionToken.objects.get(key=token, is_active=True)
        print(st)
        return st
    except SessionToken.DoesNotExist:
        return False