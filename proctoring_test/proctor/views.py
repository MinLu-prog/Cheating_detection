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
import time
from django.shortcuts import redirect

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

def admin_view(request) :
        return redirect('/admin/')

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

    # Aggregation of quiz results with percentage
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
        percentage = round((correct_answers / total_questions) * 100, 2) if total_questions > 0 else 0

        quiz_results.append({
            'quiz_title': quiz.title,
            'correct_answers': correct_answers,
            'total_questions': total_questions,
            'percentage': percentage
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

import os 
import sys
import signal
import subprocess
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.utils.timezone import now
from .models import Quiz, StudentResponse, Choice

face_process = {}  # Track running face recognition processes per user

@login_required
def take_quiz(request, quiz_id):
    if not is_student(request.user):
        messages.error(request, "Access restricted to students.")
        return redirect('home')

    quiz = get_object_or_404(Quiz, id=quiz_id)

    # Check quiz availability
    if now() < quiz.open_time or now() > quiz.close_time:
        messages.error(request, "Quiz is not currently available.")
        return redirect('available_quizzes')

    # Prevent retake
    if StudentResponse.objects.filter(student=request.user, quiz=quiz).exists():
        messages.warning(request, "You have already taken this quiz.")
        return redirect('student_dashboard')

    if request.method == "POST":
        # Save student answers
        for question in quiz.questions.all():
            selected_choice_id = request.POST.get(f"question_{question.id}")
            if selected_choice_id:
                StudentResponse.objects.create(
                    student=request.user,
                    quiz=quiz,
                    question=question,
                    selected_choice_id=selected_choice_id
                )

        # Stop face recognition subprocess if it exists
        pid = face_process.get(request.user.username)
        if pid:
            try:
                os.kill(pid, signal.SIGTERM)
                del face_process[request.user.username]
            except Exception as e:
                print(f"Could not kill face recognition process {pid}: {e}")

        messages.success(request, "Quiz submitted successfully.")
        return redirect('student_dashboard')  # <-- IMPORTANT: redirect after POST

    # GET: render quiz page
    return render(request, "proctor/take_quiz.html", {
        "quiz": quiz,
        "questions": quiz.questions.all().order_by("order"),
        "time_limit": quiz.time_limit * 60,  # seconds
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


# @csrf_exempt  # Keep only if using JavaScript fetch without CSRF token
# @login_required
# def verify_face(request):
#     if request.method != 'POST':
#         return JsonResponse({'valid': False, 'error': 'Invalid request method'})

#     # Step 1: Decode base64 image
#     try:
#         data = json.loads(request.body)
#         image_data = data.get('image', '').split(',')[1]
#         img_bytes = base64.b64decode(image_data)
#         nparr = np.frombuffer(img_bytes, np.uint8)
#         frame = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
#     except Exception as e:
#         return JsonResponse({'valid': False, 'error': 'Failed to decode image data'})

#     # Step 2: Load dataset
#     dataset_path = os.path.join(settings.BASE_DIR, 'Real-time-Face-Recognition-Project', 'face_dataset')


#     face_data = []
#     labels = []
#     names = {}
#     class_id = 0

#     for fx in os.listdir(dataset_path):
#         if fx.endswith('.npy'):
#             name = fx[:-4]
#             names[class_id] = name
#             data_item = np.load(os.path.join(dataset_path, fx))
#             face_data.append(data_item)
#             labels.append(class_id * np.ones((data_item.shape[0],)))
#             class_id += 1

#     # Step 3: Validate that current user is in the dataset
#     if request.user.username not in names.values():
#         return JsonResponse({'valid': False, 'error': 'Your face is not registered in the dataset'})

#     # Step 4: Train KNN
#     face_dataset = np.concatenate(face_data, axis=0)
#     face_labels = np.concatenate(labels, axis=0).reshape((-1, 1))
#     trainset = np.concatenate((face_dataset, face_labels), axis=1)

#     # Step 5: Detect face in uploaded frame
#     face_cascade = cv2.CascadeClassifier(cv2.data.haarcascades + 'haarcascade_frontalface_alt.xml')
#     gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
#     faces = face_cascade.detectMultiScale(gray, 1.3, 5)

#     if len(faces) == 0:
#         return JsonResponse({'valid': False, 'error': 'No face detected'})

#     for (x, y, w, h) in faces:
#         face_section = frame[y:y+h, x:x+w]
#         face_section = cv2.resize(face_section, (100, 100)).flatten()
#         out = knn(trainset, face_section)
#         predicted_name = names[int(out)]

#         if predicted_name == request.user.username:
#             return JsonResponse({'valid': True})

#     return JsonResponse({'valid': False, 'error': 'Face mismatch'})
# ----------------- Face Verification ----------------- #
import os
import time
import json
import base64
import numpy as np
import cv2
from django.conf import settings
from django.http import JsonResponse, HttpResponseRedirect
from django.contrib.auth.decorators import login_required
from django.urls import reverse

# Toggle face recognition on/off
FACE_RECOGNITION_ENABLED = True  # Set to True to enable recognition again

# ----------------- Improved KNN Helper with distance -----------------
def distance(v1, v2):
    return np.linalg.norm(v1 - v2)

def knn_with_distance(train, test, k=5):
    distances = np.linalg.norm(train[:, :-1] - test, axis=1)
    nearest_indices = np.argsort(distances)[:k]
    nearest_labels = train[nearest_indices, -1]
    min_dist = np.min(distances[nearest_indices])
    
    unique_labels, counts = np.unique(nearest_labels, return_counts=True)
    predicted_class = unique_labels[np.argmax(counts)]
    
    return int(predicted_class), min_dist

# Cache for face datasets
_face_dataset_cache = None
_labels_cache = None
_names_cache = None
_trainset_cache = None
_last_cache_time = 0
_dynamic_threshold = None
CACHE_TIMEOUT = 300

def _load_face_dataset():
    global _face_dataset_cache, _labels_cache, _names_cache, _trainset_cache, _last_cache_time, _dynamic_threshold
    
    current_time = time.time()
    if (_face_dataset_cache is not None and 
        current_time - _last_cache_time < CACHE_TIMEOUT):
        return _face_dataset_cache, _labels_cache, _names_cache, _trainset_cache, _dynamic_threshold
    
    dataset_path = r"D:\Cheating_Detection\Detection\Cheating_detection\proctoring_test\proctoring_test\Real-time-Face-Recognition-Project\face_dataset"
    face_data, labels, names, class_id = [], [], {}, 0

    if not os.path.exists(dataset_path):
        raise FileNotFoundError(f"Face dataset directory not found: {dataset_path}")

    for fx in os.listdir(dataset_path):
        if fx.endswith('.npy'):
            username = fx[:-4]
            names[class_id] = username
            data_item = np.load(os.path.join(dataset_path, fx))
            face_data.append(data_item)
            labels.append(class_id * np.ones((data_item.shape[0],)))
            class_id += 1

    if len(face_data) == 0:
        raise ValueError("No face datasets found in the directory")

    face_dataset = np.concatenate(face_data, axis=0)
    face_labels = np.concatenate(labels, axis=0).reshape((-1, 1))
    trainset = np.concatenate((face_dataset, face_labels), axis=1)
    
    _dynamic_threshold = 2000  # Fixed threshold
    
    _face_dataset_cache = face_dataset
    _labels_cache = face_labels
    _names_cache = names
    _trainset_cache = trainset
    _last_cache_time = current_time
    
    return face_dataset, face_labels, names, trainset, _dynamic_threshold

@login_required
def verify_face(request):
    if request.method != 'POST':
        return JsonResponse({'valid': False, 'error': 'Invalid request method'})

    current_username = request.user.username

    # --- TEMPORARY BYPASS ---
    if not FACE_RECOGNITION_ENABLED:
        return JsonResponse({
            'valid': True,
            'status': 'Face recognition temporarily disabled',
            'username': current_username,
            'confidence': 1.0,
            'distance': 0.0,
            'threshold': 0.0
        })

    # Step 1: Decode image from frontend
    try:
        data = json.loads(request.body)
        image_data = data.get('image', '')
        if not image_data or ',' not in image_data:
            return JsonResponse({'valid': False, 'error': 'Invalid image format'})
        
        image_data = image_data.split(',')[1]
        img_bytes = base64.b64decode(image_data)
        nparr = np.frombuffer(img_bytes, np.uint8)
        frame = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
        
        if frame is None:
            return JsonResponse({'valid': False, 'error': 'Failed to decode image'})
            
    except json.JSONDecodeError:
        return JsonResponse({'valid': False, 'error': 'Invalid JSON data'})
    except Exception as e:
        return JsonResponse({'valid': False, 'error': f'Failed to process image: {str(e)}'})

    # Step 2: Load face datasets
    try:
        face_dataset, face_labels, names, trainset, dynamic_threshold = _load_face_dataset()
    except Exception as e:
        return JsonResponse({'valid': False, 'error': f'Failed to load face dataset: {str(e)}'})

    # Check if current user's face data exists
    user_face_exists = any(current_username == name for name in names.values())
    if not user_face_exists:
        return JsonResponse({
            'valid': False, 
            'error': f'Your face is not registered. Please register your face first.'
        })

    # Step 3: Detect face in current frame
    try:
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        face_cascade = cv2.CascadeClassifier(cv2.data.haarcascades + 'haarcascade_frontalface_alt.xml')
        faces = face_cascade.detectMultiScale(gray, 1.3, 5)
    except Exception as e:
        return JsonResponse({'valid': False, 'error': f'Face detection failed: {str(e)}'})

    if len(faces) == 0:
        print("DEBUG: NO FACE DETECTED - triggering mismatch")
        return _handle_face_mismatch(request, "No face detected", 9999)

    x, y, w, h = sorted(faces, key=lambda f: f[2]*f[3], reverse=True)[0]
    face_section = frame[y:y+h, x:x+w]
    face_section = cv2.resize(face_section, (100, 100)).flatten()

    # Step 4: KNN prediction with distance
    try:
        predicted_class, min_dist = knn_with_distance(trainset, face_section)
    except Exception as e:
        return JsonResponse({'valid': False, 'error': f'Face recognition failed: {str(e)}'})

    predicted_name = names.get(int(predicted_class), "Unknown")
    
    print(f"DEBUG: Distance: {min_dist:.2f}, Threshold: {dynamic_threshold:.2f}")
    print(f"DEBUG: Predicted: {predicted_name}, Expected: {current_username}")

    if min_dist > dynamic_threshold:
        predicted_name = "Unknown"
        print(f"DEBUG: UNKNOWN FACE (distance {min_dist:.2f} > threshold {dynamic_threshold:.2f})")
        return _handle_face_mismatch(request, "Unknown face", min_dist)
    else:
        print(f"DEBUG: RECOGNIZED as {predicted_name} (distance {min_dist:.2f})")

    if predicted_name == current_username:
        session = request.session
        session_key_prefix = f'face_verify_{request.user.id}_'
        session[f'{session_key_prefix}mismatch_count'] = 0
        session[f'{session_key_prefix}last_warning_time'] = None
        session.modified = True
        
        confidence = max(0, min(1, 1 - (min_dist / dynamic_threshold)))
        
        return JsonResponse({
            'valid': True, 
            'status': 'Face matched successfully',
            'username': current_username,
            'confidence': float(confidence),
            'distance': float(min_dist),
            'threshold': float(dynamic_threshold)
        })
    else:
        print(f"DEBUG: WRONG USER - expected {current_username}, got {predicted_name}")
        return _handle_face_mismatch(request, f"Wrong user: {predicted_name}", min_dist)


def _handle_face_mismatch(request, reason, distance_value):
    session = request.session
    session_key_prefix = f'face_verify_{request.user.id}_'
    current_time = time.time()
    
    if f'{session_key_prefix}mismatch_count' not in session:
        session[f'{session_key_prefix}mismatch_count'] = 0
        session[f'{session_key_prefix}last_warning_time'] = None
    
    mismatch_count = session[f'{session_key_prefix}mismatch_count']
    last_warning_time = session[f'{session_key_prefix}last_warning_time']
    
    session[f'{session_key_prefix}mismatch_count'] = mismatch_count + 1
    session[f'{session_key_prefix}last_warning_time'] = current_time
    session.modified = True
    
    new_mismatch_count = mismatch_count + 1
    print(f"DEBUG: Mismatch count: {new_mismatch_count}/3, Reason: {reason}")
    
    if new_mismatch_count == 1:
        return JsonResponse({
            'valid': False, 
            'warning': '⚠️ First warning: Face mismatch detected',
            'reason': reason,
            'mismatch_count': new_mismatch_count,
            'distance': float(distance_value),
            'remaining_chances': 2
        })
    
    elif new_mismatch_count == 2:
        return JsonResponse({
            'valid': False, 
            'warning': '❗️ Strong warning: Face mismatch detected again',
            'reason': reason,
            'mismatch_count': new_mismatch_count,
            'distance': float(distance_value),
            'remaining_chances': 1
        })
    
    elif new_mismatch_count >= 3:
        print("DEBUG: THIRD MISMATCH - TERMINATING EXAM")
        session[f'{session_key_prefix}mismatch_count'] = 0
        session[f'{session_key_prefix}last_warning_time'] = None
        session.modified = True
        
        return JsonResponse({
            'valid': False, 
            'terminated': True,
            'error': '⛔️ Exam terminated due to repeated face mismatches',
            'reason': reason,
            'mismatch_count': new_mismatch_count,
            'redirect_url': reverse('student_dashboard')
        })
    
    else:
        return JsonResponse({
            'valid': False, 
            'error': 'Face mismatch detected',
            'reason': reason,
            'mismatch_count': new_mismatch_count,
            'distance': float(distance_value)
        })



# ---- Start capture face ----
import os
import subprocess
import sys
from django.shortcuts import render, redirect
from django.contrib.auth.decorators import login_required
from django.contrib import messages

@login_required
def capture_face_view(request):
    user = request.user

    # Only allow students
    if not user.groups.filter(name='Students').exists():
        return redirect('home')

    # Dataset folder path (must match face_data.py)
    dataset_path = r"D:\Cheating_Detection\Detection\Cheating_detection\proctoring_test\proctoring_test\Real-time-Face-Recognition-Project\face_dataset"
    os.makedirs(dataset_path, exist_ok=True)

    # Path to this user's dataset file
    dataset_file = os.path.join(dataset_path, f'{user.username}.npy')

    # Skip if already exists
    if os.path.exists(dataset_file):
        messages.info(request, "Your face data already exists.")
        return redirect('student_dashboard')

    if request.method == 'POST':
        # Absolute path to face_data.py
        script_path = r"D:\Cheating_Detection\Detection\Cheating_detection\proctoring_test\proctoring_test\Real-time-Face-Recognition-Project\face_data.py"

        try:
            # Launch script in a new console window (Windows)
            subprocess.Popen(
                [sys.executable, script_path, user.username],
                cwd=r"D:\Cheating_Detection\Detection\Cheating_detection\proctoring_test\proctoring_test\Real-time-Face-Recognition-Project",
                creationflags=subprocess.CREATE_NEW_CONSOLE
            )
            messages.success(request, "Face capture started! Please follow the instructions in the new window.")
            # Redirect immediately to capture_started page
            return redirect('capture_started')
        except Exception as e:
            messages.error(request, f"Failed to start face capture: {str(e)}")

    # Always render the template for GET requests or if POST fails
    return render(request, 'proctor/capture_face.html')

# ---- Capture started page ----
@login_required
def capture_started_view(request):
    return render(request, 'proctor/capture_started.html')


# ---- Check if face data file exists (for Done button) ----
@login_required
def check_face_file(request):
    user = request.user
    dataset_file = r"D:\Cheating_Detection\Detection\Cheating_detection\proctoring_test\proctoring_test\Real-time-Face-Recognition-Project\face_dataset" + f"\\{user.username}.npy"
    exists = os.path.exists(dataset_file)
    return JsonResponse({'exists': exists})

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

# proctor/views.py
from django.shortcuts import render
from django.http import StreamingHttpResponse, JsonResponse
from .models import Quiz, Question
from webcam.ai_proctor import DetectionSystem
import cv2

# ------------------- SINGLE DETECTION INSTANCE -------------------
# Only one instance for the whole server to avoid camera conflicts
ds = DetectionSystem(process_with_camera=True)


# ------------------- STREAMING VIEW -------------------
def quiz_ai_stream(request, quiz_id):
    """
    MJPEG stream of webcam frames processed by AI Proctor.
    Used by <img> in take_quiz.html.
    """
    def gen():
        for frame in ds.run_yield():
            ret, jpeg = cv2.imencode('.jpg', frame)
            if not ret:
                continue
            yield (b'--frame\r\n'
                   b'Content-Type: image/jpeg\r\n\r\n' + jpeg.tobytes() + b'\r\n')
    return StreamingHttpResponse(gen(), content_type='multipart/x-mixed-replace; boundary=frame')


# ------------------- AI STATUS VIEW -------------------
def quiz_ai_status(request, quiz_id):
    """
    Return JSON with AI Proctor status:
    - terminated: True/False
    - reason: explanation if terminated
    - warning: optional warning message
    Polled by JS in take_quiz.html.
    """
    status = {
        "terminated": False,
        "reason": "",
        "warning": None
    }

    # Check last few alerts to decide termination/warnings
    last_alerts = ds.alert_manager.alert_history[-5:]  # last 5 alerts
    active_alerts = [a['type'] for a in last_alerts]

    # Example termination rules
    if 'multi_face' in active_alerts:
        status['terminated'] = True
        status['reason'] = "Multiple faces detected!"
    elif 'no_face' in active_alerts:
        status['terminated'] = True
        status['reason'] = "No face detected!"
    elif 'phone' in active_alerts:
        status['warning'] = "Phone detected!"
    elif 'book' in active_alerts:
        status['warning'] = "Unauthorized material detected!"
    elif 'head_pose' in active_alerts:
        status['warning'] = "Please face the screen!"
    elif 'eye_gaze' in active_alerts:
        status['warning'] = "Eyes off screen!"

    return JsonResponse(status)


# ------------------- FACE CAPTURE PAGE VIEW -------------------

from django.shortcuts import render, redirect
from django.contrib.auth.decorators import login_required

@login_required
def capture_started(request):
    return render(request, 'proctor/capture_started.html')

@login_required
def face_capture_process(request):
    # This page handles the actual webcam capture
    return render(request, 'proctor/face_capture_process.html')

