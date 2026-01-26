"""
URL configuration for quail project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/5.2/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from django.contrib import admin
from django.urls import path, include
from rest_framework.routers import DefaultRouter
from expmanager.views import ExperimentScriptViewSet, ExperimentRunViewSet, RunLogViewSet, FileViewSet, DatasetViewSet
from panels.views import PanelViewSet, PanelConfigViewSet
from core.auth_views import CustomAuthToken, logout_view
from core.ttl_views import ttl_devices, ttl_level, ttl_override

# REST API Router
router = DefaultRouter()
router.register(r'scripts', ExperimentScriptViewSet, basename='script')
router.register(r'runs', ExperimentRunViewSet, basename='run')
router.register(r'logs', RunLogViewSet, basename='log')
router.register(r'files', FileViewSet, basename='file')
router.register(r'datasets', DatasetViewSet, basename='dataset')
router.register(r'panels', PanelViewSet, basename='panel')
router.register(r'panel-configs', PanelConfigViewSet, basename='panel-config')

urlpatterns = [
    path('admin/', admin.site.urls),
    path('api/', include(router.urls)),
    path('api/login/', CustomAuthToken.as_view(), name='api_login'),
    path('api/logout/', logout_view, name='api_logout'),
    path('api/ttl/devices/', ttl_devices),
    path('api/ttl/level/', ttl_level),
    path('api/ttl/override/', ttl_override),
]
