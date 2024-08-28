# tovaerp/urls.py

from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static
from assets.views import home, dashboard, add_asset, verify_email  # Import your views here

urlpatterns = [
    path('', home, name='home'),
    path('dashboard/', dashboard, name='dashboard'),  # Use the imported view here
    path('add-asset/', add_asset, name='add_asset'),
    path('admin/', admin.site.urls),
    path('api/auth/', include('dj_rest_auth.urls')),
    path('api/auth/registration/', include('dj_rest_auth.registration.urls')),
    path('verify-email/<int:uid>/', verify_email, name='verify_email'),  # Email verification
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)

