from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static
from django.contrib.auth.views import LoginView
from assets.views import home, dashboard, add_asset, VerifyEmailView, confirmation_success, verification_failed

urlpatterns = [
    path('', home, name='home'),
    path('dashboard/', dashboard, name='dashboard'),
    path('add-asset/', add_asset, name='add_asset'),
    path('admin/', admin.site.urls),

    path('api/auth/', include('dj_rest_auth.urls')),
    path('api/auth/registration/', include('dj_rest_auth.registration.urls')),

    path('verify-email/<uidb64>/<token>/', VerifyEmailView.as_view(), name='email-verify'),
    path('confirmation-success/', confirmation_success, name='confirmation_success'),
    path('verification-failed/', verification_failed, name='verification_failed'),

    path('login/', LoginView.as_view(), name='login'),
    path('accounts/', include('allauth.urls')),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)


