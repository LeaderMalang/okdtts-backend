from django.contrib.auth import get_user_model
from notification.models import Notification

User = get_user_model()

def notify_user_and_party(user, party, title, message):
    """Create notifications for a user and optionally a party's associated user."""
    if user:
        Notification.objects.create(user=user, title=title, message=message)
    if party and getattr(party, "email", None):
        party_user = User.objects.filter(email=party.email).first()
        if party_user:
            Notification.objects.create(user=party_user, title=title, message=message)
