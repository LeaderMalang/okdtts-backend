from rest_framework import permissions, viewsets, status
from rest_framework.authtoken.models import Token
from rest_framework.decorators import action,parser_classes
from rest_framework.response import Response
from rest_framework.parsers import MultiPartParser, FormParser

from django.core.mail import send_mail
from django.utils import timezone
from datetime import timedelta
import random


from .serializers import (
    AuthTokenSerializer,
    UserSerializer,
    PartySerializer,

    PasswordResetConfirmSerializer,

    PasswordResetRequestSerializer,
    PartyProfileSerializer,
    PartyProfileUpdateSerializer,

)
from inventory.models import Party
from hr.models import Employee
from hr.serializers import EmployeeSerializer
from user.models import CustomUser, PasswordResetCode
class UserViewSet(viewsets.ModelViewSet):
    """ViewSet providing CRUD operations for users and a `me` endpoint."""

    queryset = CustomUser.objects.all()
    serializer_class = UserSerializer
    permission_classes = [permissions.IsAuthenticated]

    @action(detail=False, methods=["get"], permission_classes=[permissions.IsAuthenticated])
    def me(self, request):
        serializer = self.get_serializer(request.user)
        return Response(serializer.data)


class AuthViewSet(viewsets.ViewSet):
    """ViewSet handling authentication actions like login and token refresh."""

    serializer_class = AuthTokenSerializer

    @action(detail=False, methods=["post"], url_path="login")
    def login(self, request):
        serializer = self.serializer_class(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)
        user = serializer.validated_data["user"]
        token, _ = Token.objects.get_or_create(user=user)
        
        if user.role == "CUSTOMER":
            try:

                party = Party.objects.get(user=user)
                
            except Party.DoesNotExist:

                party = None # Activate party if exists
            return Response({"token": token.key, "user": UserSerializer(user).data,"party": PartySerializer(party).data if party else None,"role": user.role})
        elif user.role == "SALES":
            try:

                employee = Employee.objects.get(user=user)
                
            except Employee.DoesNotExist:

                employee = None # Activate party if exists
            return Response({"token": token.key, "user": UserSerializer(user).data, "employee": EmployeeSerializer(employee).data if employee else None,"role": user.role})
        elif user.role == "SUPER_ADMIN":
            
            return Response({"token": token.key, "user": UserSerializer(user).data,"role": user.role})

    @action(detail=False, methods=["post"], url_path="refresh", permission_classes=[permissions.IsAuthenticated])
    def refresh(self, request):
        token, _ = Token.objects.get_or_create(user=request.user)
        return Response({"token": token.key})

    @action(detail=False, methods=["post"], url_path="register",parser_classes=[MultiPartParser, FormParser])
    def register(self, request):
        """Register a new customer with inactive user account."""
        password = request.data.get("password")
        email = request.data.get("email")
        if not email or not password:
            return Response(
                {"detail": "Email or Password is required"}, status=status.HTTP_400_BAD_REQUEST
            )
        if CustomUser.objects.filter(email=email).exists():
            return Response(
                {"detail": "User with this email already exists"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        user=CustomUser.objects.create_user(
            email=email,
            password=password,
            role="CUSTOMER",
            is_active=False,
        )
        data = request.data.copy()
        party_serializer = PartySerializer(data=data,context={"request": request,"user": user,"party_type": "customer"})
        party_serializer.is_valid(raise_exception=True)

        

        party = party_serializer.save(party_type="customer")

        

        return Response(
            {
                "message": "Registration successful. Await approval.",
                "party": PartySerializer(party).data,
            },
            status=status.HTTP_201_CREATED,
        )


    @action(detail=False, methods=["post"], url_path="reset-password/confirm")
    def reset_password_confirm(self, request):
        serializer = PasswordResetConfirmSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        user = serializer.validated_data["user"]
        new_password = serializer.validated_data["new_password"]
        user.set_password(new_password)
        user.save()

        serializer.validated_data["reset_code"].delete()

        return Response(status=status.HTTP_204_NO_CONTENT)

    @action(
        detail=False,
        methods=["post"],
        url_path="reset-password/request",
    )
    def reset_password_request(self, request):
        serializer = PasswordResetRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        email = serializer.validated_data["email"]
        user = CustomUser.objects.get(email=email, is_active=True)
        code = f"{random.randint(0, 999999):06d}"
        expires_at = timezone.now() + timedelta(minutes=10)
        PasswordResetCode.objects.create(user=user, code=code, expires_at=expires_at)
        send_mail(
            "Password Reset Code",
            f"Your password reset code is {code}",
            None,
            [email],
        )
        return Response({"message": "If the email exists, a reset code has been sent."})





class ProfileViewSet(viewsets.ViewSet):
    """
    Authenticated endpoints to fetch and update the logged-in user's profile.
    """
    permission_classes = [permissions.IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser]  # enable image upload

    def _get_party(self, request):
        party = getattr(request.user, "party", None)
        if not party:
            # Fallback (if older users exist without a Party)
            party = Party.objects.filter(user_id=request.user.id).first()
            if party and not party.user:
                party.user = request.user
                party.save(update_fields=["user"])
        return party

    @action(detail=False, methods=["get"])
    def me(self, request):
        """
        Returns: { user: {...}, party: {...} }
        """
        party = self._get_party(request)
        return Response({
            # "user": UserSerializer(request.user).data,
            "party": PartyProfileSerializer(party).data if party else None,
        })

    @action(detail=False, methods=["patch", "post"], url_path="profile")
    def update_profile(self, request):
        """
        PATCH/POST multipart form:
          - Party fields (name, address, phone, proprietor, license_*, business_image, city, area, lat/lng)
          - Optional: user_email, new_password
        """
        party = self._get_party(request)
        if not party:
            return Response({"detail": "Party not found for this user."}, status=404)

        serializer = PartyProfileUpdateSerializer(
            party, data=request.data, partial=True, context={"request": request}
        )
        serializer.is_valid(raise_exception=True)
        party = serializer.save()

        return Response({
            "message": "Profile updated.",
            # "user": UserSerializer(request.user).data,
            "party": PartyProfileSerializer(party).data,
        }, status=200)