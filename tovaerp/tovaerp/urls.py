# tovaerp/urls.py
from django.contrib import admin
from django.urls import path, include
from assets.views import home

urlpatterns = [
    path('', home, name='home'),
    path('admin/', admin.site.urls),
    path('api/auth/', include('dj_rest_auth.urls')),
    path('api/auth/registration/', include('dj_rest_auth.registration.urls')),
    # other URLs
]
