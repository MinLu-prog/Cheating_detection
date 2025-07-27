from django.urls import path, include
from proctor import views
from django.contrib import admin

urlpatterns = [
    path('admin/', admin.site.urls),
    path('', views.homepage, name='home'),
    path('about/', views.about, name='about'),
    path('login/', views.login_view, name='login'),
    path('logout/', views.logout_view, name='logout'),

    # Teacher URLs
    path('teacher_dashboard/', views.teacher_dashboard, name='teacher_dashboard'),
    path('teacher/create-quiz/', views.create_quiz_view, name='create_quiz'),
    path('teacher/save-quiz/', views.save_quiz_info, name='save_quiz_info'),
    path('teacher/add-question/', views.add_question, name='add_question'),
    path('quiz/<int:quiz_id>/add-questions/', views.add_questions, name='add_questions'),

    # Student URLs
    path('student_dashboard/', views.student_dashboard, name='student_dashboard'),
    path('student/quizzes/', views.available_quizzes, name='available_quizzes'),
    path('student/take-quiz/<int:quiz_id>/', views.take_quiz, name='take_quiz'),

    # Webcam app
    path('webcam/', include('webcam.urls')),

    # Face capture
    path('capture-face/', views.capture_face_view, name='capture_face'),

]
