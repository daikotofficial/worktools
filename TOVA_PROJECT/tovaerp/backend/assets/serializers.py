# assets/serializers.py

from rest_framework import serializers
from .models import CustomUser, Profile
from django.core.mail import send_mail
from django.conf import settings


class RegisterSerializer(serializers.ModelSerializer):
    password1 = serializers.CharField(write_only=True, min_length=6)
    password2 = serializers.CharField(write_only=True, min_length=6)
    company_name = serializers.CharField(required=True)
    phone_number = serializers.CharField(required=True)
    address = serializers.CharField(required=True)
    logo = serializers.ImageField(required=True)
    sector = serializers.CharField(required=True)

    class Meta:
        model = CustomUser
        fields = ['email', 'password1', 'password2', 'first_name', 'last_name', 'company_name', 'phone_number', 'address', 'logo', 'sector']

    def validate(self, data):
        if data['password1'] != data['password2']:
            raise serializers.ValidationError("Passwords do not match.")
        return data

    def create(self, validated_data):
        password = validated_data.pop('password1')
        validated_data.pop('password2')
        user = CustomUser.objects.create_user(**validated_data)
        user.set_password(password)
        user.is_active = False
        user.save()

        Profile.objects.create(
            user=user,
            company_name=validated_data['company_name'],
            phone_number=validated_data['phone_number'],
            address=validated_data['address'],
            logo=validated_data['logo'],
            sector=validated_data['sector']
        )

        # Send verification email
        self.send_verification_email(user)

        return user

    def send_verification_email(self, user):
        verification_url = f"{settings.FRONTEND_URL}/verify-email/{user.pk}/"  # Generate a proper link here
        subject = "Verify your email"
        message = f"Please verify your email by clicking on the following link: {verification_url}"
        send_mail(subject, message, settings.EMAIL_HOST_USER, [user.email])

