from django.urls import path
from . import views

urlpatterns = [
    path('test/', views.proctoring_launcher, name='proctoring_launcher'),
]
