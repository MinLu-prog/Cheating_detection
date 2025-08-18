from django.urls import path
from .views import video_feed, proctoring_launcher

urlpatterns = [
    path('video_feed/', video_feed, name='video_feed'),
    path('start_proctoring/', proctoring_launcher, name='start_proctoring'),
]
