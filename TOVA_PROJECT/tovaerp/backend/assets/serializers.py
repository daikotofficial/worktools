from django.utils.http import urlsafe_base64_encode
from django.utils.encoding import force_bytes
from django.contrib.auth.tokens import PasswordResetTokenGenerator
from django.contrib.sites.shortcuts import get_current_site
from django.core.mail import send_mail
from django.template.loader import render_to_string
import logging

logger = logging.getLogger(__name__)

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
        user.is_active = False  # User is inactive until email confirmation
        user.save()

        # Create the associated profile
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
        token_generator = PasswordResetTokenGenerator()
        token = token_generator.make_token(user)
        uid = urlsafe_base64_encode(force_bytes(user.pk))

        current_site = get_current_site(self.context['request'])
        domain = current_site.domain
        protocol = 'https' if self.context['request'].is_secure() else 'http'

        email_confirmation_link = f"{protocol}://{domain}/verify-email/{uid}/{token}/"

        logger.debug(f"Generated Confirmation URL: {email_confirmation_link}")

        # Render email template
        message = render_to_string('account/email/email_confirmation_message.html', {
            'user': user,
            'domain': domain,
            'uid': uid,
            'token': token,
            'protocol': protocol,
        })

        # Send the email
        send_mail(
            subject='Confirm your email address',
            message=message,
            from_email='noreply@daikot.com.ng',
            recipient_list=[user.email],
            fail_silently=False,
            html_message=message,
            headers={'X-PM-TrackLinks': 'None'}  # Ensure Postmark doesn't modify the link
        )


