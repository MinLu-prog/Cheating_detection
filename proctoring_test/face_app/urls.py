from django.urls import path
from . import views

urlpatterns = [
    path('', views.test_face, name='test_face'),
    path('live/', views.live_face, name='live_face'),
    path('capture/', views.capture_face, name='capture_face'),
    path('verify/', views.verify_face, name='verify_face'),
    path('verify_page/', views.verify_face_page, name='verify_face_page'), 

]
