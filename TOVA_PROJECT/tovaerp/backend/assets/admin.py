# assets/admin.py

from django.contrib import admin
from .models import CustomUser, Asset, Location, Department

admin.site.register(CustomUser)
admin.site.register(Asset)
admin.site.register(Location)
admin.site.register(Department)

