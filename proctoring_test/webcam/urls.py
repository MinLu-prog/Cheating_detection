# webcam/urls.py
from django.urls import path
from . import views

app_name = 'webcam'

urlpatterns = [
    # Live AI Proctoring video feed for a specific quiz
    path('quiz/<int:quiz_id>/ai_stream/', views.quiz_ai_stream, name='quiz_ai_stream'),

    # AI Proctoring status endpoint for polling
    path('quiz/<int:quiz_id>/ai_status/', views.quiz_ai_status, name='quiz_ai_status'),

    #path('quiz/<int:quiz_id>/ai_frame/', views.quiz_ai_frame_annotated, name='quiz_ai_frame_annotated')

]
