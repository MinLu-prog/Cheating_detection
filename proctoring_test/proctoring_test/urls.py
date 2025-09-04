from django.urls import path, include
from proctor import views
from django.contrib import admin

urlpatterns = [
    path('admin/', admin.site.urls),
    path('', views.homepage, name='home'),
    path('about/', views.about, name='about'),
    path('login/', views.login_view, name='login'),
    path('logout/', views.logout_view, name='logout'),
    path('admin',views.admin_view,name= 'admin'),

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
    path('verify-face/', views.verify_face, name='verify_face'),
    path('capture-face/', views.capture_face_view, name='capture_face'),
    path('capture_started/', views.capture_started, name='capture_started'),
    path('check_face_file/', views.check_face_file, name='check_face_file'),  # for Done button
    path('teacher/quiz/<int:quiz_id>/results/', views.teacher_quiz_results, name='teacher_quiz_results'),

    #path('quiz/<int:quiz_id>/', views.quiz_detail, name='quiz_detail'),
    path('quiz/<int:quiz_id>/edit/', views.edit_quiz, name='edit_quiz'),
    path('quiz/<int:quiz_id>/delete/', views.delete_quiz, name='delete_quiz'),
    #path('quiz/<int:quiz_id>/submit/', views.submit_quiz, name='submit_quiz'),

    # Quiz page
    path('quiz/<int:quiz_id>/', views.take_quiz, name='take_quiz'),

    # AI Proctor MJPEG stream
    path('quiz/<int:quiz_id>/ai_stream/', views.quiz_ai_stream, name='quiz_ai_stream'),

    # AI Proctor status JSON endpoint
    path('quiz/<int:quiz_id>/ai_status/', views.quiz_ai_status, name='quiz_ai_status'),
]
