from django.contrib import admin
from django.urls import path, re_path, include
from django.views.generic import TemplateView
from django.conf import settings
from django.views.static import serve
from django.http import HttpResponse
import os

def serve_static(request, path):
    full_path = os.path.join(str(settings.STATIC_DIR), path)
    if not os.path.exists(full_path):
        return HttpResponse(status=404)
    return serve(request, path, document_root=str(settings.STATIC_DIR))


urlpatterns = [
    path('admin/', admin.site.urls),
    path('api/', include('api.urls')),

    re_path(r'^static/(?P<path>.+)$', serve_static),

    # SPA fallback — catch-all must be last
    re_path(
        r'^(?!api/)(?!static/).*$',
        TemplateView.as_view(template_name="index.html"),
    ),
]