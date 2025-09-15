# accounts/models.py
from django.contrib.auth.models import AbstractUser, BaseUserManager
from django.db import models

from django.contrib.auth.hashers import make_password

class CustomUserManager(BaseUserManager):
    def create_user(self, email, password=None, **extra_fields):
        if not email:
            raise ValueError('The Email field must be set')
        email = self.normalize_email(email)
        user = self.model(email=email, **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_superuser(self, email, password=None, **extra_fields):
        extra_fields.setdefault('is_staff', True)
        extra_fields.setdefault('is_superuser', True)
        extra_fields.setdefault('is_active', True)

        if extra_fields.get('is_staff') is not True:
            raise ValueError('Superuser must have is_staff=True.')
        if extra_fields.get('is_superuser') is not True:
            raise ValueError('Superuser must have is_superuser=True.')
        return self.create_user(email, password, **extra_fields)
class CustomUser(AbstractUser):
    ROLES = (
        ('SUPER_ADMIN', 'Super Admin'),
        ('CUSTOMER', 'Customer'),
        ('SUPPLIER', 'Supplier'),
        ('MANAGER', 'Manager'),
        ('SALES', 'Sales'),
        ('DELIVERY', 'Delivery'),
        ('WAREHOUSE_ADMIN', 'Warehouse Admin'),
        ('DELIVERY_MANAGER', 'Delivery Manager'),
        ('RECOVERY_OFFICER', 'Recovery Officer'),
        ('INVESTOR', 'Investor'),
    )
    role = models.CharField(max_length=30, choices=ROLES, default='CUSTOMER')
    username = None
    email = models.EmailField(unique=True)
    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = ["role"]
    objects = CustomUserManager()

    def save(self, *args, **kwargs):
   
        if self.password and not self.password.startswith(('pbkdf2_sha256$', 'bcrypt$', 'argon2')):
        
            self.password = make_password(self.password)
        
        super().save(*args, **kwargs)
    
    def __str__(self):
        return f"{self.email} ({self.role})"


class PasswordResetCode(models.Model):
    user = models.ForeignKey(
        CustomUser, on_delete=models.CASCADE, related_name="password_reset_codes"
    )
    code = models.CharField(max_length=6)
    expires_at = models.DateTimeField()
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.user.email} - {self.code}"
