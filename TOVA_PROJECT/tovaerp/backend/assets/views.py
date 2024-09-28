from rest_framework.permissions import AllowAny
from rest_framework.authentication import SessionAuthentication
from django.contrib.auth.decorators import login_required
from django.shortcuts import render, redirect
from rest_framework_simplejwt.tokens import AccessToken
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from django.contrib import messages
from django.utils.timezone import now
from .models import CustomUser
import logging

from django.utils.encoding import force_str
from django.utils.http import urlsafe_base64_decode
from django.contrib.auth.tokens import PasswordResetTokenGenerator

logger = logging.getLogger(__name__)

@login_required(login_url='/login/')
def dashboard(request):
    return render(request, 'dashboard.html')

@login_required(login_url='/login/')
def add_asset(request):
    return render(request, 'add_asset.html')

@login_required(login_url='/login/')
def home(request):
    return render(request, 'home.html')


class VerifyEmailView(APIView):
    permission_classes = [AllowAny]

    def get(self, request, uidb64, token):
        try:
            uid = force_str(urlsafe_base64_decode(uidb64))
            user = CustomUser.objects.get(pk=uid)
        except (TypeError, ValueError, OverflowError, CustomUser.DoesNotExist):
            user = None

        token_generator = PasswordResetTokenGenerator()

        if user is not None and token_generator.check_token(user, token):
            user.is_active = True
            user.save()
            messages.success(request, 'Your account has been activated successfully!')
            return redirect('confirmation_success')
        else:
            logger.error(f"Token validation failed for user {uid}")
            messages.error(request, 'The confirmation link is invalid or has expired.')
            return redirect('verification_failed')

def confirmation_success(request):
    return render(request, 'registration/confirmation_success.html')

def verification_failed(request):
    return render(request, 'registration/verification_failed.html')


