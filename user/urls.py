from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .views import AuthViewSet, UserViewSet, ProfileViewSet


router = DefaultRouter()
router.register(r"users", UserViewSet)
router.register(r"auth", AuthViewSet, basename="auth")

router.register(r"me", ProfileViewSet, basename="me")
urlpatterns = [
    path("", include(router.urls)),
]

