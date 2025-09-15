from django.contrib.auth import authenticate
from django.utils import timezone
from rest_framework import serializers

from inventory.models import Party
from .models import CustomUser, PasswordResetCode
from utils.geocode import reverse_geocode
from setting.models import City, Area
from voucher.models import ChartOfAccount
class UserSerializer(serializers.ModelSerializer):
    """Serializer for the User model exposing basic fields."""

    class Meta:
        model = CustomUser
        fields = ("id", "email", "role")


class AuthTokenSerializer(serializers.Serializer):
    """Serializer for obtaining auth tokens using login credentials."""

    username = serializers.CharField()
    password = serializers.CharField(trim_whitespace=False, write_only=True)

    def validate(self, attrs):
        username = attrs.get("username")
        password = attrs.get("password")
        user_exist =CustomUser.objects.filter(email=username,is_active=True).exists()  # Ensure user exists
        if user_exist is False:
            
            raise serializers.ValidationError(
                "User with this email does not exist or not active.",
                code="authorization",
            )
        user = authenticate(
            request=self.context.get("request"),
            username=username,
            password=password,
        )
        if not user:
            raise serializers.ValidationError(
                "Unable to authenticate with provided credentials",
                code="authorization",
            )
        attrs["user"] = user
        return attrs


class PasswordResetRequestSerializer(serializers.Serializer):
    email = serializers.EmailField()

    def validate(self, attrs):
        email = attrs.get("email")
        if not CustomUser.objects.filter(email=email, is_active=True).exists():
            raise serializers.ValidationError(
                "User with this email does not exist or not active."
            )
        return attrs


class PartySerializer(serializers.ModelSerializer):
    """Serializer for the `Party` model used during registration."""

    
    business_image=serializers.ImageField(required=True)
    user = UserSerializer(required=False)
    class Meta:
        model = Party
        fields = (
            "id",
            "name",
            "address",
            "phone",
            "party_type",
            "proprietor",
            "license_no",
            "license_expiry",
            "business_image",
            "user",
            "latitude",
            "longitude",
        )
        read_only_fields = ("id", "party_type")
    # def validate(self, attrs):
    #     user = self.context.get("user")
    #     if user and attrs.get("email") and attrs["email"].lower() != user.email.lower():
    #         # keep them in sync; or just overwrite below
    #         attrs["email"] = user.email
    #     return attrs

    def create(self, validated_data):
        user = self.context.get("user")
        if not user:
            raise serializers.ValidationError("User context is required.")
        # ensure party type
        validated_data["party_type"] = self.context.get("party_type", "customer")
        # mirror email from user
        # validated_data["email"] = user.email
        # remove any stray 'user' if sent in payload
        validated_data.pop("user", None)
        party = Party.objects.create(user=user, **validated_data)
        code=party.name[:3].upper()+str(party.id).zfill(4)
        account=ChartOfAccount.objects.create(name=party.name,account_type_id=1,code=code,parent_account_id=3)
        geo = reverse_geocode(validated_data.get("latitude"), validated_data.get("longitude"))
        party.chart_of_account=account
        if geo.get("ok"):
            # upsert City/Area by name (simple)
            if geo.get("city"):
                city_obj, _ = City.objects.get_or_create(name=geo["city"])
                party.city = city_obj
            if geo.get("area"):
                area_obj, _ = Area.objects.get_or_create(name=geo["area"], defaults={"city": party.city})
                # if Area has FK to City, ensure you set it
                party.area = area_obj
            party.save()
        return party


class PasswordResetConfirmSerializer(serializers.Serializer):
    """Validate a password reset code and supply a new password."""

    email = serializers.EmailField()
    code = serializers.CharField()
    new_password = serializers.CharField(write_only=True)

    def validate(self, attrs):
        email = attrs.get("email")
        code = attrs.get("code")

        try:
            reset_code = PasswordResetCode.objects.get(user__email=email, code=code)
        except PasswordResetCode.DoesNotExist:
            raise serializers.ValidationError("Invalid reset code.")

        if reset_code.expires_at < timezone.now():
            raise serializers.ValidationError("Reset code has expired.")

        attrs["user"] = reset_code.user
        attrs["reset_code"] = reset_code
        return attrs




class PartyProfileSerializer(serializers.ModelSerializer):
    """Readable serializer (returned to client)."""
    user = UserSerializer(read_only=True)

    class Meta:
        model = Party
        fields = (
            "id", "name", "address", "phone","party_type",
            "proprietor", "license_no", "license_expiry",
            "business_image", "city", "area", "latitude", "longitude",
            "user",
        )
        read_only_fields = ("id", "party_type", "user")  # email comes from User

class PartyProfileUpdateSerializer(serializers.ModelSerializer):
    """
    Writable serializer for updates.
    Allows updating Party fields + optional user email/password.
    """
    # Optional: allow changing user email/password via these fields
    user_email = serializers.EmailField(required=False, allow_blank=False)
    new_password = serializers.CharField(write_only=True, required=False, allow_blank=False)

    class Meta:
        model = Party
        fields = (
            "name", "address", "phone", "proprietor", "license_no", "license_expiry"
           ,"user_email", "new_password",
        )

    def update(self, instance: Party, validated_data):
        user = self.context["request"].user

        # Handle email/password updates on the auth user
        user_email = validated_data.pop("user_email", None)
        new_password = validated_data.pop("new_password", None)

        if user_email and user_email.lower() != user.email.lower():
            user.email = user_email
            user.username = user_email  # if you use username = email
            user.save(update_fields=["email", "username"])

            # Keep Party.email in sync (if you store it there too)
            instance.email = user_email

        if new_password:
            user.set_password(new_password)
            user.save(update_fields=["password"])

        # Update Party fields
        for k, v in validated_data.items():
            setattr(instance, k, v)
        instance.save()

        return instance