from .models import UserProfile


def request_site_key(request):
    return request.get_host().strip().casefold()


def ensure_user_site(user, request):
    site_key = request_site_key(request)
    profile, _ = UserProfile.objects.get_or_create(user=user)
    if not profile.site_key:
        profile.site_key = site_key
        profile.save(update_fields=["site_key", "updated_at"])
    return profile.site_key


def user_site_key(user):
    return UserProfile.objects.filter(user=user).values_list("site_key", flat=True).first() or ""


def user_belongs_to_request_site(user, request):
    site_key = user_site_key(user)
    return not site_key or site_key == request_site_key(request)
