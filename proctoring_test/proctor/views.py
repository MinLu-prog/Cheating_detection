import logging
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.models import Group
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.utils import timezone
from django.urls import reverse
from django.contrib import messages
from .models import Quiz, Question, Choice, StudentResponse
import logging
from django.http import HttpResponse
from django.db.models import Count, Sum, Case, When, IntegerField
import subprocess
import os
import sys
from .utils import is_teacher, is_student
from .verify_face import verify_face_from_base64

logger = logging.getLogger(__name__)

# Check functions
def is_teacher(user):
    return user.groups.filter(name='Teachers').exists()

def is_student(user):
    return user.groups.filter(name='Students').exists()

# Login view
# views.py
def login_view(request):
    logger.debug(f"Request method: {request.method}, user: {request.user}, authenticated: {request.user.is_authenticated}")

    if request.user.is_authenticated:
        logger.debug("User is already authenticated, redirecting...")
        if is_teacher(request.user):
                return redirect('teacher_dashboard')
        elif is_student(request.user):
                return redirect('student_dashboard')
        elif request.user.is_superuser:
                return redirect('/admin/')
        return redirect('home')

    if request.method == 'POST':
        username = request.POST.get('username')
        password = request.POST.get('password')
        logger.debug(f"Attempting login for username: {username}")
        
        user = authenticate(request, username=username, password=password)

        if user is None:
            logger.debug("Invalid login attempt")
            messages.error(request, 'Invalid username or password.')
            return render(request, 'proctor/login.html')

        login(request, user)
        logger.info(f"User {user.username} logged in successfully")

        # REDIRECTION LOGIC AFTER LOGIN
        if is_teacher(user):
            return redirect('teacher_dashboard')

        elif is_student(user):
            dataset_path = os.path.join(
                os.path.dirname(os.path.abspath(__file__)),
                '../Real-time-Face-Recognition-Project/face_dataset'
            )
            dataset_file = os.path.join(dataset_path, f'{user.username}.npy')

            if not os.path.exists(dataset_file):
                logger.info(f"No dataset found for {user.username}, redirecting to face capture.")
                request.session['pending_face_capture'] = True  # Optional flag
                return redirect('capture_face')
            else:
                return redirect('student_dashboard')


        elif user.is_superuser:
            return redirect('/admin/')

        return redirect('home')

    return render(request, 'proctor/login.html')


 #Logout view
def logout_view(request):
        logger.info(f"User {request.user.username} logged out")
        logout(request)
        messages.success(request, 'You have been logged out.')
        return redirect('home')

# Dashboards
@login_required
def student_dashboard(request):
    if not is_student(request.user):
        messages.error(request, 'Access restricted to students.')
        return redirect('home')

    now = timezone.now()
    available_quizzes = Quiz.objects.filter(
        open_time__lte=now,
        close_time__gte=now,
        status='published'
    ).exclude(
        responses__student=request.user
    )

    # Corrected aggregation of quiz results
    quiz_results = []
    submitted_quizzes = (
        StudentResponse.objects
        .filter(student=request.user)
        .values('quiz')
        .distinct()
    )

    for item in submitted_quizzes:
        quiz_id = item['quiz']
        quiz = Quiz.objects.get(id=quiz_id)
        responses = StudentResponse.objects.filter(student=request.user, quiz=quiz)
        correct_answers = responses.filter(is_correct=True).count()
        total_questions = quiz.questions.count()

        quiz_results.append({
            'quiz_title': quiz.title,
            'correct_answers': correct_answers,
            'total_questions': total_questions
        })

    return render(request, 'proctor/student_dashboard.html', {
        'available_quizzes': available_quizzes,
        'results': quiz_results,
        'now': now,
    })


@login_required
def teacher_dashboard(request):
    if not is_teacher(request.user):
        messages.error(request, 'Access restricted to teachers.')
        return redirect('home')
    
    quizzes = Quiz.objects.filter(teacher=request.user).order_by('-created_at')
    return render(request, 'proctor/teacher_dashboard.html', {
        'quizzes': quizzes,
        'now': timezone.now()
    })

# Quiz creation views
@login_required
def create_quiz_view(request):
    if not is_teacher(request.user):
        return redirect('home')

    if request.method == 'POST':
        title = request.POST['title']
        question_count = int(request.POST['question_count'])
        open_time = request.POST['open_date']
        close_time = request.POST['close_date']
        time_limit = int(request.POST['time_limit'])

        quiz = Quiz.objects.create(
            title=title,
            teacher=request.user,
            open_time=open_time,
            close_time=close_time,
            time_limit=time_limit,
            question_count=question_count
        )
        return redirect('add_questions', quiz_id=quiz.id)

    return render(request, 'proctor/create_quiz.html')


@login_required
def add_questions(request, quiz_id):
    quiz = get_object_or_404(Quiz, id=quiz_id, teacher=request.user)
    existing_question_count = quiz.questions.count()

    if request.method == 'POST':
        question_text = request.POST['question']
        correct_index = int(request.POST['correct'])

        question = Question.objects.create(
            quiz=quiz,
            text=question_text,
            order=existing_question_count
        )

        for i in range(4):
            Choice.objects.create(
                question=question,
                text=request.POST[f'answer{i+1}'],
                is_correct=(i == correct_index),
                order=i
            )

        if quiz.questions.count() >= quiz.question_count:
            quiz.status = 'published'
            quiz.save()
            messages.success(request, 'Quiz published successfully.')
            return redirect('teacher_dashboard')

        return redirect('add_questions', quiz_id=quiz.id)

    return render(request, 'proctor/add_question.html', {
        'quiz': quiz,
        'current_question': existing_question_count + 1,
        'total_questions': quiz.question_count
    })


@login_required
def save_quiz_info(request):
    if not is_teacher(request.user):
        return JsonResponse({'error': 'Unauthorized'}, status=403)

    if request.method == 'POST':
        try:
            required_fields = ['title', 'open_date', 'close_date', 'time_limit', 'question_count']
            if not all(field in request.POST for field in required_fields):
                return JsonResponse({'error': 'Missing required fields'}, status=400)

            quiz = Quiz.objects.create(
                title=request.POST['title'],
                teacher=request.user,
                open_time=request.POST['open_date'],
                close_time=request.POST['close_date'],
                time_limit=request.POST['time_limit'],
                description=request.POST.get('description', '')
            )
            
            request.session.update({
                'current_quiz_id': quiz.id,
                'question_count': int(request.POST['question_count']),
                'current_question_index': 0
            })
            request.session.modified = True
            
            logger.info(f"Quiz {quiz.id} created by {request.user.username}")
            return JsonResponse({
                'status': 'ok',
                'quiz_id': quiz.id,
                'redirect_url': reverse('add_question')
            })

        except ValueError as e:
            logger.error(f"Validation error: {str(e)}")
            return JsonResponse({'error': str(e)}, status=400)
        except Exception as e:
            logger.error(f"Unexpected error: {str(e)}")
            return JsonResponse({'error': 'Server error'}, status=500)

    return JsonResponse({'error': 'Invalid request method'}, status=405)

@login_required
def add_question(request):
    if not is_teacher(request.user):
        return JsonResponse({'error': 'Unauthorized'}, status=403)

    if request.method == 'GET':
        quiz_id = request.session.get('current_quiz_id')
        if not quiz_id:
            messages.error(request, 'No active quiz session')
            return redirect('teacher_dashboard')
            
        current = request.session.get('current_question_index', 0)
        total = request.session.get('question_count', 0)
        
        if current >= total:
            messages.success(request, 'Quiz completed successfully!')
            return redirect('teacher_dashboard')
            
        return render(request, 'proctor/add_question.html', {
            'current_question': current + 1,
            'total_questions': total
        })
        
    elif request.method == 'POST':
        quiz_id = request.session.get('current_quiz_id')
        if not quiz_id:
            return JsonResponse({'error': 'No active quiz'}, status=400)
            
        quiz = get_object_or_404(Quiz, id=quiz_id)
        
        try:
            question = Question.objects.create(
                quiz=quiz,
                text=request.POST['question'],
                order=request.session.get('current_question_index', 0)
            )
            
            answers = [
                request.POST.get('answer1'),
                request.POST.get('answer2'),
                request.POST.get('answer3'),
                request.POST.get('answer4')
            ]
            correct_index = int(request.POST['correct'])
            
            for i, answer in enumerate(answers):
                if answer:
                    Choice.objects.create(
                        question=question,
                        text=answer,
                        is_correct=(i == correct_index),
                        order=i
                    )
            
            current = request.session.get('current_question_index', 0) + 1
            request.session['current_question_index'] = current
            request.session.modified = True
            
            if current >= request.session['question_count']:
                quiz.status = 'published'
                quiz.save()
                request.session.flush()
                messages.success(request, 'Quiz published successfully!')
                return JsonResponse({
                    'status': 'complete',
                    'redirect_url': reverse('teacher_dashboard')
                })
            
            return JsonResponse({
                'status': 'ok',
                'current_question': current + 1,
                'total_questions': request.session['question_count']
            })
            
        except Exception as e:
            logger.error(f"Question creation error: {str(e)}")
            return JsonResponse({'error': str(e)}, status=500)
    
    return JsonResponse({'error': 'Invalid request'}, status=400)

# Student views
@login_required
def available_quizzes(request):
    if not is_student(request.user):
        messages.error(request, 'Access restricted to students.')
        return redirect('home')
        
    now = timezone.now()
    quizzes = Quiz.objects.filter(
        open_time__lte=now,
        close_time__gte=now,
        status='published'
    ).exclude(
        responses__student=request.user
    )
    return render(request, 'proctor/available_quizzes.html', {
        'quizzes': quizzes,
        'now': now
    })

import subprocess
import sys
import os
import signal
from django.utils.timezone import now

face_process = {}  # Global PID tracker (in production, use session or DB)

@login_required
def take_quiz(request, quiz_id):
    if not is_student(request.user):
        messages.error(request, 'Access restricted to students.')
        return redirect('home')

    quiz = get_object_or_404(Quiz, id=quiz_id)

    if now() < quiz.open_time or now() > quiz.close_time:
        messages.error(request, 'Quiz is not currently available')
        return redirect('available_quizzes')

    # Don't allow retake
    if StudentResponse.objects.filter(student=request.user, quiz=quiz).exists():
        messages.warning(request, 'You have already taken this quiz')
        return redirect('student_dashboard')

    # ---- Start face recognition script ----
    base_dir = os.path.dirname(os.path.abspath(__file__))
    script_path = os.path.join(base_dir, '../Real-time-Face-Recognition-Project/face_recognition_script.py')  # adjust name

    try:
        process = subprocess.Popen(
            [sys.executable, script_path],
            cwd=os.path.dirname(script_path),
            creationflags=subprocess.CREATE_NEW_CONSOLE if os.name == 'nt' else 0
        )
        # Save the process ID with user's session key
        face_process[request.user.username] = process.pid
        print(f"Started face recognition with PID: {process.pid}")
    except Exception as e:
        print(f"Failed to launch face recognition: {e}")
        messages.error(request, 'Failed to start face recognition')

    # ---- Show the quiz ----
    return render(request, 'proctor/take_quiz.html', {
        'quiz': quiz,
        'questions': quiz.questions.all().order_by('order'),
        'time_limit': quiz.time_limit * 60
    })


import subprocess
import sys
import os
import signal
from django.utils.timezone import now
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from .models import Quiz, StudentResponse  # adjust imports if needed

face_process = {}  # Global PID tracker (in production, use session or DB)

@login_required
def take_quiz(request, quiz_id):
    if not is_student(request.user):
        messages.error(request, 'Access restricted to students.')
        return redirect('home')

    quiz = get_object_or_404(Quiz, id=quiz_id)

    if now() < quiz.open_time or now() > quiz.close_time:
        messages.error(request, 'Quiz is not currently available')
        return redirect('available_quizzes')

    # Don't allow retake
    if StudentResponse.objects.filter(student=request.user, quiz=quiz).exists():
        messages.warning(request, 'You have already taken this quiz')
        return redirect('student_dashboard')

    if request.method == 'POST':
        # Save submitted answers
        for question in quiz.questions.all():
            selected_option_id = request.POST.get(f'question_{question.id}')
            if selected_option_id:
                StudentResponse.objects.create(
                    student=request.user,
                    quiz=quiz,
                    question=question,
                    selected_option_id=selected_option_id
                )

        # ---- Kill face recognition process ----
        pid = face_process.get(request.user.username)
        if pid:
            try:
                os.kill(pid, signal.SIGTERM)
                print(f"Killed face recognition with PID: {pid}")
                del face_process[request.user.username]
            except Exception as e:
                print(f"Could not kill process {pid}: {e}")

        messages.success(request, 'Quiz submitted successfully.')
        return redirect('student_dashboard')

    # ---- Start face recognition script ----
    base_dir = os.path.dirname(os.path.abspath(__file__))
    script_path = os.path.join(base_dir, '../Real-time-Face-Recognition-Project/face_recognition_script.py')  # adjust path

    try:
        process = subprocess.Popen(
            [sys.executable, script_path],
            cwd=os.path.dirname(script_path),
            creationflags=subprocess.CREATE_NEW_CONSOLE if os.name == 'nt' else 0
        )
        face_process[request.user.username] = process.pid
        print(f"Started face recognition with PID: {process.pid}")
    except Exception as e:
        print(f"Failed to launch face recognition: {e}")
        messages.error(request, 'Failed to start face recognition')

    # ---- Render quiz page ----
    return render(request, 'proctor/take_quiz.html', {
        'quiz': quiz,
        'questions': quiz.questions.all().order_by('order'),
        'time_limit': quiz.time_limit * 60
    })

import cv2, numpy as np, base64, os
from django.views.decorators.csrf import csrf_exempt
from django.http import JsonResponse
from django.contrib.auth.decorators import login_required
from django.utils.decorators import method_decorator
from django.conf import settings

# --- Utility functions ---
def distance(v1, v2):
    return np.sqrt(((v1 - v2) ** 2).sum())

def knn(train, test, k=5):
    dist = []
    for i in range(train.shape[0]):
        ix = train[i, :-1]
        iy = train[i, -1]
        d = distance(test, ix)
        dist.append([d, iy])
    dk = sorted(dist, key=lambda x: x[0])[:k]
    labels = np.array(dk)[:, -1]
    output = np.unique(labels, return_counts=True)
    index = np.argmax(output[1])
    return output[0][index]

# --- Face verification view ---
# views.py

import base64
import numpy as np
import cv2
import os
import json
from django.conf import settings
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.contrib.auth.decorators import login_required

from .knn_module import knn  # Ensure you import your KNN function correctly


@csrf_exempt  # Keep only if using JavaScript fetch without CSRF token
@login_required
def verify_face(request):
    if request.method != 'POST':
        return JsonResponse({'valid': False, 'error': 'Invalid request method'})

    # Step 1: Decode base64 image
    try:
        data = json.loads(request.body)
        image_data = data.get('image', '').split(',')[1]
        img_bytes = base64.b64decode(image_data)
        nparr = np.frombuffer(img_bytes, np.uint8)
        frame = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
    except Exception as e:
        return JsonResponse({'valid': False, 'error': 'Failed to decode image data'})

    # Step 2: Load dataset
    dataset_path = os.path.join(settings.BASE_DIR, 'Real-time-Face-Recognition-Project', 'face_dataset')


    face_data = []
    labels = []
    names = {}
    class_id = 0

    for fx in os.listdir(dataset_path):
        if fx.endswith('.npy'):
            name = fx[:-4]
            names[class_id] = name
            data_item = np.load(os.path.join(dataset_path, fx))
            face_data.append(data_item)
            labels.append(class_id * np.ones((data_item.shape[0],)))
            class_id += 1

    # Step 3: Validate that current user is in the dataset
    if request.user.username not in names.values():
        return JsonResponse({'valid': False, 'error': 'Your face is not registered in the dataset'})

    # Step 4: Train KNN
    face_dataset = np.concatenate(face_data, axis=0)
    face_labels = np.concatenate(labels, axis=0).reshape((-1, 1))
    trainset = np.concatenate((face_dataset, face_labels), axis=1)

    # Step 5: Detect face in uploaded frame
    face_cascade = cv2.CascadeClassifier(cv2.data.haarcascades + 'haarcascade_frontalface_alt.xml')
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    faces = face_cascade.detectMultiScale(gray, 1.3, 5)

    if len(faces) == 0:
        return JsonResponse({'valid': False, 'error': 'No face detected'})

    for (x, y, w, h) in faces:
        face_section = frame[y:y+h, x:x+w]
        face_section = cv2.resize(face_section, (100, 100)).flatten()
        out = knn(trainset, face_section)
        predicted_name = names[int(out)]

        if predicted_name == request.user.username:
            return JsonResponse({'valid': True})

    return JsonResponse({'valid': False, 'error': 'Face mismatch'})

@login_required
def capture_face_view(request):
    user = request.user

    # Only students should proceed
    if not user.groups.filter(name='Students').exists():
        return redirect('home')

    # Define path to face dataset folder
    base_dir = os.path.dirname(os.path.abspath(__file__))
    dataset_path = os.path.join(base_dir, '../Real-time-Face-Recognition-Project/face_dataset')

    # Abort if the dataset folder does not exist
    if not os.path.isdir(dataset_path):
        logger.error("Face dataset folder does not exist.")
        return render(request, 'proctor/capture_face.html', {
            'error': 'Face dataset folder not found. Please contact admin.'
        })

    # Path to this user's dataset file
    dataset_file = os.path.join(dataset_path, f'{user.username}.npy')

    # If face data already exists, skip
    if os.path.exists(dataset_file):
        logger.info(f"Face data already exists for {user.username}")
        return redirect('student_dashboard')

    if request.method == 'POST':
        script_path = os.path.join(base_dir, '../Real-time-Face-Recognition-Project/face_data.py')

        try:
            subprocess.Popen(
                [sys.executable, script_path, user.username],
                cwd=os.path.join(base_dir, '../Real-time-Face-Recognition-Project'),
                creationflags=subprocess.CREATE_NEW_CONSOLE if os.name == 'nt' else 0  # Only on Windows
            )
            logger.info(f"Launched face capture script for {user.username}")
            return render(request, 'proctor/capture_started.html')  # Optional screen: "Face capture started..."
        except Exception as e:
            logger.error(f"Failed to launch face capture script: {e}")
            return render(request, 'proctor/capture_face.html', {'error': f"Error: {str(e)}"})

    return render(request, 'proctor/capture_face.html')


# General views
def homepage(request):
    return render(request, 'proctor/home.html')

def about(request):
    return render(request, 'proctor/about.html')

from django.shortcuts import render, get_object_or_404, redirect
from django.contrib import messages
from .models import Quiz, StudentResponse

def teacher_quiz_results(request, quiz_id):
    # Ensure only the teacher who created the quiz can access
    quiz = get_object_or_404(Quiz, id=quiz_id, teacher=request.user)

    # Fetch all responses for this quiz
    responses = StudentResponse.objects.filter(
        quiz=quiz
    ).select_related("student", "question", "selected_choice")

    # Organize results per student
    student_results = {}
    for response in responses:
        student = response.student
        if student not in student_results:
            student_results[student] = {
                "score": 0,
                "total": quiz.questions.count(),
                "answers": []
            }

        # Get the correct answer text
        correct_choice = response.question.choices.filter(is_correct=True).first()
        correct_text = correct_choice.text if correct_choice else "No correct option set"

        # Add answer details
        if response.is_correct:
            student_results[student]["score"] += 1

        student_results[student]["answers"].append({
            "question": response.question.text,
            "selected": response.selected_choice.text if response.selected_choice else "No answer",
            "correct": response.is_correct,
            "correct_answer": correct_text
        })

    return render(request, "proctor/teacher_quiz_results.html", {
        "quiz": quiz,
        "student_results": student_results
    })


def delete_quiz(request, quiz_id):
    if request.method == 'POST':
        quiz = get_object_or_404(Quiz, id=quiz_id, teacher=request.user)
        quiz.delete()
        messages.success(request, f'Quiz "{quiz.title}" has been deleted.')
        return redirect('teacher_dashboard')
    else:
        messages.warning(request, 'Invalid request method.')
        return redirect('teacher_dashboard')

from django.shortcuts import get_object_or_404, redirect
from .models import Quiz

def edit_quiz(request, quiz_id):
    # Temporary: Just redirect back to dashboard until full edit is implemented
    quiz = get_object_or_404(Quiz, id=quiz_id, teacher=request.user)
    return redirect('teacher_dashboard')