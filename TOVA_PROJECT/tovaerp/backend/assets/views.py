# assets/views.py

from django.contrib.auth.decorators import login_required
from django.shortcuts import render, get_object_or_404, redirect
from django.contrib.auth.models import User
from .models import CustomUser
from django.contrib.auth.tokens import default_token_generator
from django.utils.http import urlsafe_base64_decode
from django.utils.encoding import force_str
from django.contrib import messages

@login_required(login_url='/login/')
def dashboard(request):
    return render(request, 'dashboard.html')

@login_required(login_url='/login/')
def add_asset(request):
    return render(request, 'add_asset.html')

@login_required(login_url='/login/')
def home(request):
    return render(request, 'home.html')

def verify_email(request, uidb64, token):
    try:
        uid = force_str(urlsafe_base64_decode(uidb64))
        user = CustomUser.objects.get(pk=uid)
    except (TypeError, ValueError, OverflowError, CustomUser.DoesNotExist):
        user = None

    if user is not None and default_token_generator.check_token(user, token):
        user.is_active = True
        user.save()
        messages.success(request, 'Your account has been activated successfully!')
        return redirect('confirmation_success')
    else:
        messages.error(request, 'The confirmation link is invalid or has expired.')
        return redirect('home')

def confirmation_success(request):
    return render(request, 'registration/confirmation_success.html')
