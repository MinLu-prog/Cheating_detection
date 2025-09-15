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
            return redirect('profile')

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
# views.py
from django.core.paginator import Paginator

@login_required
def profile(request):
    role = "User"
    base_template = "base.html"

    if request.user.groups.filter(name="Teachers").exists():
        role = "Teacher"
        base_template = "base.html"
        user_quizzes_qs = Quiz.objects.filter(teacher=request.user).order_by('-created_at')
        published_count = user_quizzes_qs.filter(status='published').count()

        # NEW: paginate the table only
        paginator = Paginator(user_quizzes_qs, 10)  # 10 per page (adjust if you want)
        qpage = request.GET.get("qpage")
        quizzes_page = paginator.get_page(qpage)

    elif request.user.groups.filter(name="Students").exists():
        role = "Student"
        base_template = "base_student.html"
        user_quizzes_qs = Quiz.objects.none()
        published_count = 0
        paginator = Paginator(user_quizzes_qs, 10)
        quizzes_page = paginator.get_page(1)
    elif request.user.groups.filter(name="Admins").exists():
        role = "Admin"
        base_template = "base.html"
        user_quizzes_qs = Quiz.objects.all().order_by('-created_at')
        published_count = user_quizzes_qs.filter(status='published').count()
        paginator = Paginator(user_quizzes_qs, 10)
        qpage = request.GET.get("qpage")
        quizzes_page = paginator.get_page(qpage)
    else:
        user_quizzes_qs = Quiz.objects.none()
        published_count = 0
        paginator = Paginator(user_quizzes_qs, 10)
        quizzes_page = paginator.get_page(1)

    context = {
        "user_quizzes": user_quizzes_qs,   # keep queryset for counts in the top “Profile” tab
        "quizzes_page": quizzes_page,      # use this for the paginated table
        "published_count": published_count,
        "role": role,
        "base_template": base_template,
        "now": timezone.now(),
    }
    return render(request, "proctor/profile.html", context)

 #Logout view
@login_required
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

    # ✅ Count students from the "Students" group
    students_group = Group.objects.get(name="Students")
    total_students = students_group.user_set.count()

    return render(request, 'proctor/teacher_dashboard.html', {
        'quizzes': quizzes,
        'now': timezone.now(),
        'total_students': total_students,
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

from django.shortcuts import render
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.core.paginator import Paginator
from django.db.models import Q
@login_required
def teacher_list(request):
    query = request.GET.get("q", "")
    sort = request.GET.get("sort", "name")  # default sorting by name

    teachers = User.objects.filter(groups__name="Teachers")
    if query:
        teachers = teachers.filter(
            Q(first_name__icontains=query) |
            Q(last_name__icontains=query) |
            Q(email__icontains=query)
        )

    # Sorting
    if sort == "name":
        teachers = teachers.order_by('first_name')
    elif sort == "-name":
        teachers = teachers.order_by('-first_name')
    elif sort == "email":
        teachers = teachers.order_by('email')

    # Pagination
    paginator = Paginator(teachers, 10)  # 10 per page
    page_number = request.GET.get("page")
    page_obj = paginator.get_page(page_number)

    context = {
        "teachers": page_obj,
        "query": query,
        "sort": sort,
    }
    return render(request, "proctor/teacher_list.html", context)

@login_required
def student_list(request):
    query = request.GET.get("q", "")
    sort = request.GET.get("sort", "name")  # default sorting by name

    students = User.objects.filter(groups__name="Students")
    if query:
        students = students.filter(
            Q(first_name__icontains=query) |
            Q(last_name__icontains=query) |
            Q(email__icontains=query)
        )

    # Sorting
    if sort == "name":
        students = students.order_by('first_name')
    elif sort == "-name":
        students = students.order_by('-first_name')
    elif sort == "email":
        students = students.order_by('email')

    # Pagination
    paginator = Paginator(students, 10)  # 10 per page
    page_number = request.GET.get("page")
    page_obj = paginator.get_page(page_number)

    context = {
        "students": page_obj,
        "query": query,
        "sort": sort,
    }
    return render(request, "proctor/student_list.html", context)

from .models import Quiz, Question, Choice, StudentResponse, MatchingPair,BlankAnswer
@login_required
def add_questions(request, quiz_id):
    quiz = get_object_or_404(Quiz, id=quiz_id, teacher=request.user)
    existing_question_count = quiz.questions.count()

    if request.method == "POST":
        q_type = request.POST.get("question_type")
        q_text = request.POST.get("text")
        section = request.POST.get("section", "Default")

        if not q_type or (q_type != "match" and not q_text):
            messages.error(request, "Question type and body are required.")
            return redirect("add_questions", quiz_id=quiz.id)

        # Create question
        question = Question.objects.create(
            quiz=quiz,
            text=q_text,
            order=existing_question_count,
            section=section,
            question_type=q_type  # use submitted type directly
        )

        # ---------------------- MCQ ----------------------
        if q_type == "multiple":
            option_index = 1
            correct_count = 0
            while True:
                opt_key = f"option{option_index}"
                corr_key = f"correct{option_index}"

                if opt_key not in request.POST:
                    break

                text = request.POST.get(opt_key)
                if text:
                    is_correct = corr_key in request.POST
                    if is_correct:
                        correct_count += 1
                    Choice.objects.create(
                        question=question,
                        text=text,
                        is_correct=is_correct,
                        order=option_index
                    )
                option_index += 1

            if correct_count == 0:
                messages.warning(request, "At least one correct answer should be marked.")

        # ---------------------- True/False ----------------------
        elif q_type == "truefalse":
            correct_value = request.POST.get("correct")
            if correct_value not in ["True", "False"]:
                messages.error(request, "You must select the correct True/False answer.")
                question.delete()
                return redirect("add_questions", quiz_id=quiz.id)

            Choice.objects.create(
                question=question, text="True", is_correct=(correct_value == "True"), order=1
            )
            Choice.objects.create(
                question=question, text="False", is_correct=(correct_value == "False"), order=2
            )

        # ---------------------- Fill in the Blank ----------------------
        elif q_type == "fill":
            blanks = [key for key in request.POST.keys() if key.startswith("correct")]
            for i, blank in enumerate(sorted(blanks, key=lambda x: int(x.replace("correct", "")))):
                ans_text = request.POST.get(blank).strip()
                if ans_text:
                    BlankAnswer.objects.create(
                        question=question,
                        correct_text=ans_text
                    )

        elif q_type == "match":
                    left_keys = sorted([k for k in request.POST if k.startswith("left")])
                    right_keys = sorted([k for k in request.POST if k.startswith("right")])

                    pair_count = 0
                    for l_key, r_key in zip(left_keys, right_keys):
                        left_text = request.POST.get(l_key, "").strip()
                        right_text = request.POST.get(r_key, "").strip()
                        if left_text and right_text:
                            MatchingPair.objects.create(
                                question=question,
                                left_text=left_text,
                                right_text=right_text
                            )
                            pair_count += 1

                    if pair_count == 0:
                        messages.error(request, "You must provide at least one matching pair.")
                        question.delete()
                        return redirect("add_questions", quiz_id=quiz.id)

        # ---------------------- Check if quiz is complete ----------------------
        if quiz.questions.count() >= quiz.question_count:
            quiz.status = "published"
            quiz.save()
            messages.success(request, "Quiz published successfully!")
            return redirect("teacher_dashboard")
        else:
            messages.success(
                request,
                f"Question saved! ({quiz.questions.count()}/{quiz.question_count})"
            )
            return redirect("add_questions", quiz_id=quiz.id)


    # GET request
    context = {
        "quiz": quiz,
        "current_question": existing_question_count + 1,
        "total_questions": quiz.question_count,
    }
    return render(request, "proctor/add_questions.html", context)


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
from .models import Quiz, StudentResponse, Choice, MatchingPair

face_process = {}  # Track running face recognition processes per user

from django.shortcuts import render, get_object_or_404, redirect
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.utils.timezone import now
import os, signal

from .models import Quiz, StudentResponse, Choice
from .utils import is_student  # your helper

from django.db import transaction
import logging

logger = logging.getLogger(__name__)

@login_required
def take_quiz(request, quiz_id):
    if not is_student(request.user):
        messages.error(request, "Access restricted to students.")
        return redirect('home')

    quiz = get_object_or_404(Quiz, id=quiz_id)

    # Check availability
    if now() < quiz.open_time or now() > quiz.close_time:
        messages.error(request, "Quiz is not currently available.")
        return redirect('available_quizzes')

    # Prevent retake
    if StudentResponse.objects.filter(student=request.user, quiz=quiz).exists():
        messages.warning(request, "You have already taken this quiz.")
        return redirect('student_dashboard')

    if request.method == "POST":
        saved_any = False
        try:
            with transaction.atomic():
                for question in quiz.questions.all():
                    field_name = f"question_{question.id}"

                    # read posted value(s)
                    # For checkboxes (multi-select) template should use name="question_<id>" and send multiple values
                    posted_values = request.POST.getlist(field_name)  # returns [] if not present
                    single_value = request.POST.get(field_name)  # None if not present

                    # normalize question type (support both naming schemes)
                    qtype = (question.question_type or "").lower()
                    if qtype in ("multiple", "mcq", "multiplechoice", "multiple_choice"):
                        # Could be single-radio or multiple-checkboxes depending on template.
                        # If getlist returns length > 1 OR the input was checkbox, handle multiple selections.
                        if posted_values:
                            # Posted values usually are choice IDs; iterate and create responses
                            for val in posted_values:
                                try:
                                    cid = int(val)
                                except (TypeError, ValueError):
                                    logger.warning(f"Invalid choice id for question {question.id}: {val}")
                                    continue
                                # ensure choice exists and belongs to question
                                try:
                                    choice = Choice.objects.get(id=cid, question=question)
                                except Choice.DoesNotExist:
                                    logger.warning(f"Choice id {cid} not found for question {question.id}")
                                    continue

                                StudentResponse.objects.create(
                                    student=request.user,
                                    quiz=quiz,
                                    question=question,
                                    selected_choice=choice
                                )
                                saved_any = True
                        elif single_value:
                            # fallback single selection (radio) -> single_value should be a choice id
                            try:
                                cid = int(single_value)
                                choice = Choice.objects.get(id=cid, question=question)
                                StudentResponse.objects.create(
                                    student=request.user,
                                    quiz=quiz,
                                    question=question,
                                    selected_choice=choice
                                )
                                saved_any = True
                            except (TypeError, ValueError):
                                logger.warning(f"Invalid single MCQ value for q {question.id}: {single_value}")
                            except Choice.DoesNotExist:
                                logger.warning(f"Choice {single_value} not found for q {question.id}")
                        else:
                            # No answer posted for this question; optionally you can create an empty StudentResponse
                            logger.info(f"No MCQ answer posted for question {question.id} by {request.user.username}")

                    elif qtype in ("truefalse", "tf", "true_false"):
                        # Expect a single value "True" or "False" or "true"/"false"
                        answer = single_value
                        if answer is None:
                            # maybe posted as list, take first
                            answer = posted_values[0] if posted_values else None

                        if answer is not None:
                            StudentResponse.objects.create(
                                student=request.user,
                                quiz=quiz,
                                question=question,
                                text_answer=str(answer).strip()
                            )
                            saved_any = True
                        else:
                            logger.info(f"No TF answer posted for question {question.id}")

                    elif qtype in ("fill", "blank", "fillintheblank", "fill_in_the_blank"):
                        answer = single_value or (posted_values[0] if posted_values else "")
                        if answer is not None:
                            StudentResponse.objects.create(
                                student=request.user,
                                quiz=quiz,
                                question=question,
                                text_answer=str(answer).strip()
                            )
                            saved_any = True
                        else:
                            logger.info(f"No fill answer for question {question.id}")
                    elif qtype in ("match", "matching"):  # support both spellings
                            raw_answer = single_value or (posted_values[0] if posted_values else "{}")
                            try:
                                parsed = json.loads(raw_answer) if raw_answer else {}
                            except Exception:
                                parsed = {}
                                logger.warning(f"Invalid matching JSON for q {question.id}: {raw_answer}")

                            StudentResponse.objects.create(
                                student=request.user,
                                quiz=quiz,
                                question=question,
                                matched_pairs=parsed
                            )
                            saved_any = True
                    else:
                        # Unknown/other question type: attempt to store text
                        answer = single_value or (posted_values[0] if posted_values else None)
                        if answer:
                            StudentResponse.objects.create(
                                student=request.user,
                                quiz=quiz,
                                question=question,
                                text_answer=str(answer).strip()
                            )
                            saved_any = True
                        else:
                            logger.info(f"No answer posted for unknown-type question {question.id}")

                # --- stop face process if exists (your existing logic) ---
                pid = face_process.get(request.user.username)
                if pid:
                    try:
                        os.kill(pid, signal.SIGTERM)
                        del face_process[request.user.username]
                    except Exception as e:
                        logger.warning(f"Could not kill face process {pid}: {e}")

        except Exception as e:
            logger.exception(f"Error saving responses for user {request.user.username}, quiz {quiz.id}: {e}")
            messages.error(request, "There was a problem submitting your quiz. Please try again.")
            return redirect('take_quiz', quiz_id=quiz.id)

        if saved_any:
            messages.success(request, "Quiz submitted successfully.")
        else:
            messages.warning(request, "You submitted the quiz but no answers were recorded (no inputs detected).")

        return redirect('student_dashboard')

    # GET: render quiz page
    return render(request, "proctor/take_quiz.html", {
        "quiz": quiz,
        "questions": quiz.questions.all().order_by("order"),
        "time_limit": quiz.time_limit * 60,  # in seconds
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
FACE_RECOGNITION_ENABLED = False  # Set to True to enable recognition again

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
    
    dataset_path = os.path.join(settings.BASE_DIR, 'Real-time-Face-Recognition-Project', 'face_dataset')
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
import os
import subprocess
import sys
from django.shortcuts import render, redirect
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.http import JsonResponse

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
from django.shortcuts import render, get_object_or_404
from .models import Quiz, StudentResponse

def teacher_quiz_results(request, quiz_id):
    # Ensure only the teacher who created the quiz can access
    quiz = get_object_or_404(Quiz, id=quiz_id, teacher=request.user)

    # Fetch all responses for this quiz
    responses = StudentResponse.objects.filter(quiz=quiz).select_related(
        "student", "question", "selected_choice"
    )

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

        # Matching question
        if response.question.question_type == "match":
            pairs = []
            correct_pairs = {
                pair.left_text: pair.right_text for pair in response.question.matching_pairs.all()
            }

            if response.matched_pairs:
                for left, right in response.matched_pairs.items():
                    correct = correct_pairs.get(left) == right
                    pairs.append({
                        "left": left,
                        "right": right,
                        "correct": correct
                    })
                    if correct:
                        student_results[student]["score"] += 1 / max(len(correct_pairs), 1)

            student_results[student]["answers"].append({
                "question": response.question.text,
                "type": "match",
                "selected": pairs,
                "correct_answer": ", ".join([f"{l} → {r}" for l, r in correct_pairs.items()]),
            })

        # MCQ, TF, Fill-in-the-Blank
        else:
            selected_text = ""
            correct_text = ""

            if response.question.question_type in ["mcq", "tf"]:
                selected_text = response.selected_choice.text if response.selected_choice else "No answer"
                correct_choices = response.question.choices.filter(is_correct=True)
                correct_text = ", ".join([c.text for c in correct_choices])

            elif response.question.question_type == "fill":
                selected_text = response.text_answer or "No answer"
                # Get all blanks for this question
                correct_blanks = response.question.blank_answers.all()
                correct_text = ", ".join([b.correct_text for b in correct_blanks])

            # Update score if correct
            if response.is_correct:
                student_results[student]["score"] += 1

            student_results[student]["answers"].append({
                "question": response.question.text,
                "type": response.question.question_type,
                "selected": selected_text,
                "correct_answer": correct_text,
                "correct": response.is_correct
            })

    return render(request, "proctor/teacher_quiz_results.html", {
        "quiz": quiz,
        "student_results": student_results
    })


@login_required
def delete_quiz(request, quiz_id):
    if request.method == 'POST':
        quiz = get_object_or_404(Quiz, id=quiz_id, teacher=request.user)
        quiz_title = quiz.title
        quiz.delete()
        messages.success(request, f'Quiz "{quiz_title}" has been deleted.')
        return redirect('teacher_dashboard')  # 🔥 Redirect back to dashboard
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

from django.contrib.auth.decorators import login_required
from django.shortcuts import render, get_object_or_404, redirect
from django.contrib import messages
from .models import Quiz, StudentResponse
from .utils import is_teacher

@login_required
def quiz_results(request, quiz_id):
    if not is_teacher(request.user):
        messages.error(request, 'Access restricted to teachers.')
        return redirect('home')

    quiz = get_object_or_404(Quiz, id=quiz_id, teacher=request.user)

    # Group results by student
    student_ids = (
        StudentResponse.objects.filter(quiz=quiz)
        .values_list("student", flat=True)
        .distinct()
    )

    student_results = []
    for sid in student_ids:
        responses = StudentResponse.objects.filter(student_id=sid, quiz=quiz)
        correct_answers = responses.filter(is_correct=True).count()
        total_questions = quiz.questions.count()
        percentage = round((correct_answers / total_questions) * 100, 2) if total_questions > 0 else 0

        student_results.append({
            "student": responses.first().student,  # get user object
            "correct_answers": correct_answers,
            "total_questions": total_questions,
            "percentage": percentage,
        })

    return render(request, "proctor/quiz_results.html", {
        "quiz": quiz,
        "results": student_results,
    })

@login_required
def results_overview(request):
    if not is_teacher(request.user):
        messages.error(request, 'Access restricted to teachers.')
        return redirect('home')

    # Get all quizzes for the teacher
    quizzes = Quiz.objects.filter(teacher=request.user).order_by('-created_at')
    
    # Calculate comprehensive statistics
    total_quizzes = quizzes.count()
    published_quizzes = quizzes.filter(status='published').count()
    draft_quizzes = quizzes.filter(status='draft').count()
    
    # Get all student responses for teacher's quizzes
    all_responses = StudentResponse.objects.filter(quiz__teacher=request.user)
    total_responses = all_responses.count()
    
    # Calculate average scores
    quiz_stats = []
    for quiz in quizzes:
        responses = StudentResponse.objects.filter(quiz=quiz)
        if responses.exists():
            # Group by student to get individual scores
            student_scores = {}
            for response in responses:
                student = response.student
                if student not in student_scores:
                    student_scores[student] = {'correct': 0, 'total': 0}
                student_scores[student]['total'] += 1
                if response.is_correct:
                    student_scores[student]['correct'] += 1
            
            # Calculate statistics
            scores = []
            for student, data in student_scores.items():
                if data['total'] > 0:
                    percentage = (data['correct'] / data['total']) * 100
                    scores.append(percentage)
            
            if scores:
                avg_score = sum(scores) / len(scores)
                max_score = max(scores)
                min_score = min(scores)
                students_count = len(scores)
            else:
                avg_score = max_score = min_score = 0
                students_count = 0
        else:
            avg_score = max_score = min_score = 0
            students_count = 0
        
        quiz_stats.append({
            'quiz': quiz,
            'students_count': students_count,
            'avg_score': round(avg_score, 1),
            'max_score': round(max_score, 1),
            'min_score': round(min_score, 1),
            'total_questions': quiz.questions.count(),
        })
    
    # Recent activity - last 10 responses
    recent_responses = (StudentResponse.objects
                       .filter(quiz__teacher=request.user)
                       .select_related('student', 'quiz', 'question')
                       .order_by('-id')[:10])
    
    # Performance trends (last 5 quizzes)
    recent_quizzes = quizzes[:5]
    performance_data = []
    for quiz in recent_quizzes:
        responses = StudentResponse.objects.filter(quiz=quiz)
        if responses.exists():
            correct_responses = responses.filter(is_correct=True).count()
            total_responses = responses.count()
            if total_responses > 0:
                success_rate = (correct_responses / total_responses) * 100
            else:
                success_rate = 0
        else:
            success_rate = 0
        
        performance_data.append({
            'quiz_title': quiz.title,
            'success_rate': round(success_rate, 1),
            'created_date': quiz.created_at
        })
    
    context = {
        'total_quizzes': total_quizzes,
        'published_quizzes': published_quizzes,
        'draft_quizzes': draft_quizzes,
        'total_responses': total_responses,
        'quiz_stats': quiz_stats,
        'recent_responses': recent_responses,
        'performance_data': performance_data,
        'now': timezone.now(),
    }
    
    return render(request, 'proctor/results_overview.html', context)

from django.utils.dateparse import parse_datetime
from django.http import HttpResponse
from .models import ProctorEvent

from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.views.decorators.cache import never_cache
from django.http import HttpResponse
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages

@login_required
@never_cache
def proctor_logs(request, quiz_id):
    if not is_teacher(request.user):
        messages.error(request, 'Access restricted to teachers.')
        return redirect('home')

    quiz = get_object_or_404(Quiz, id=quiz_id, teacher=request.user)
    qs = ProctorEvent.objects.filter(quiz=quiz).select_related("student").order_by("-created_at")

    # filters (optional)
    student = request.GET.get('student')
    if student:
        qs = qs.filter(student__username=student)

    evt_type = request.GET.get('type')
    if evt_type:
        qs = qs.filter(event_type=evt_type)

    since = request.GET.get('since')  # ISO-8601
    if since:
        dt = parse_datetime(since)
        if dt:
            if timezone.is_naive(dt):
                dt = timezone.make_aware(dt, timezone.get_current_timezone())
            qs = qs.filter(created_at__gte=dt)

    fmt = request.GET.get('format')
    if fmt == 'csv':
        import csv
        resp = HttpResponse(content_type='text/csv')
        resp['Content-Disposition'] = f'attachment; filename="proctor_logs_{quiz.id}.csv"'
        w = csv.writer(resp)
        w.writerow(['created_at','student','event_type','severity','message','metadata'])
        for e in qs.iterator():
            w.writerow([e.created_at.isoformat(), e.student.username, e.event_type, e.severity, e.message, e.metadata])
        return resp

    # initial render caps to keep page snappy
    return render(request, 'proctor/proctor_logs.html', {
        'quiz': quiz,
        'events': qs[:1000],
    })

@login_required
@never_cache
def proctor_logs_partial(request, quiz_id):
    """Returns just the <tbody> rows so HTMX can poll for fresh items."""
    if not is_teacher(request.user):
        return HttpResponse(status=403)

    quiz = get_object_or_404(Quiz, id=quiz_id, teacher=request.user)
    qs = ProctorEvent.objects.filter(quiz=quiz).select_related("student").order_by("-created_at")

    # if client passes latest_ts, only send newer rows
    latest_ts = request.GET.get("latest_ts")
    if latest_ts:
        dt = parse_datetime(latest_ts)
        if dt:
            if timezone.is_naive(dt):
                dt = timezone.make_aware(dt, timezone.get_current_timezone())
            qs = qs.filter(created_at__gt=dt)

    events = qs[:200]  # small batch for polling
    return render(request, 'proctor/_proctor_rows.html', {'events': events})

@login_required
@never_cache
def proctor_logs_all(request):
    if not is_teacher(request.user):
        messages.error(request, 'Access restricted to teachers.')
        return redirect('home')

    qs = ProctorEvent.objects.filter(quiz__teacher=request.user).select_related("student","quiz").order_by("-created_at")

    # optional filters
    quiz_id = request.GET.get("quiz")
    if quiz_id and quiz_id != "all":
        qs = qs.filter(quiz_id=quiz_id)

    student = request.GET.get('student')
    if student:
        qs = qs.filter(student__username=student)

    evt_type = request.GET.get('type')
    if evt_type:
        qs = qs.filter(event_type=evt_type)

    return render(request, "proctor/proctor_logs_all.html", {
        "events": qs[:1000],
        "quizzes": Quiz.objects.filter(teacher=request.user).only("id","title").order_by("title"),
        "selected_quiz": quiz_id or "all",
    })


from .models import Quiz, ProctorEvent  # adjust import paths if different


def _quiz_ids_student_attended(user):
    """
    Try to collect quiz IDs that `user` actually attended.
    We probe a few common schemas and fall back to ProctorEvent existence.
    """
    quiz_ids = set()

    # 1) Submission model (common)
    try:
        from .models import Submission  # noqa
        quiz_ids.update(
            Submission.objects.filter(student=user).values_list("quiz_id", flat=True)
        )
    except Exception:
        pass

    # 2) Attempt model (alternative)
    try:
        from .models import Attempt  # noqa
        quiz_ids.update(
            Attempt.objects.filter(student=user).values_list("quiz_id", flat=True)
        )
    except Exception:
        pass

    # 3) Many-to-many (quiz.students)
    try:
        quiz_ids.update(
            Quiz.objects.filter(students=user).values_list("id", flat=True)
        )
    except Exception:
        pass

    # 4) Fallback: any ProctorEvent for this student (means they participated)
    if not quiz_ids:
        quiz_ids.update(
            ProctorEvent.objects.filter(student=user).values_list("quiz_id", flat=True).distinct()
        )

    return list(quiz_ids)

@login_required
def student_logs(request):
    student = request.user

    attended_quiz_ids = _quiz_ids_student_attended(student)
    quizzes = Quiz.objects.filter(id__in=attended_quiz_ids).only("id", "title").order_by("title")

    qs = (
        ProctorEvent.objects
        .filter(student=student, quiz_id__in=attended_quiz_ids)
        .select_related("quiz")  # and student if you show it
        .order_by("-created_at")
    )

    # Optional filters
    quiz_id = request.GET.get("quiz")
    if quiz_id and quiz_id != "all":
        qs = qs.filter(quiz_id=quiz_id)

    evt_type = request.GET.get("type")
    if evt_type:
        qs = qs.filter(event_type=evt_type)

    since = request.GET.get("since")
    if since:
        dt = parse_datetime(since)
        if dt:
            qs = qs.filter(created_at__gte=dt)

    # CSV export
    if request.GET.get("format") == "csv":
        import csv
        from django.http import HttpResponse
        resp = HttpResponse(content_type="text/csv")
        resp["Content-Disposition"] = 'attachment; filename="my_proctor_logs.csv"'
        w = csv.writer(resp)
        w.writerow(["time", "exam", "type", "severity", "message"])
        for e in qs.iterator():
            w.writerow([e.created_at.isoformat(), getattr(e.quiz, "title", e.quiz_id), e.event_type, e.severity, e.message])
        return resp

    # Page render (cap to keep snappy)
    return render(request, "proctor/student_logs.html", {
        "logs": qs[:1000],
        "quizzes": quizzes,
        "selected_quiz": quiz_id or "all",
    })
# views.py
from django.contrib import messages
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.urls import reverse
from django.utils.http import urlencode
def _parse_local_dt(s):
    if not s:
        return None
    dt = parse_datetime(s)
    if not dt:
        return None
    if timezone.is_naive(dt):
        dt = timezone.make_aware(dt, timezone.get_current_timezone())
    return dt

@login_required
def edit_quiz_view(request, quiz_id):
    if not is_teacher(request.user):
        messages.error(request, "Access restricted to teachers.")
        return redirect("home")

    quiz = get_object_or_404(Quiz, id=quiz_id, teacher=request.user)

    if request.method == "POST":
        title = request.POST.get("title", "").strip()
        question_count = int(request.POST.get("question_count", 0) or 0)
        time_limit = int(request.POST.get("time_limit", 0) or 0)
        open_time = _parse_local_dt(request.POST.get("open_date"))
        close_time = _parse_local_dt(request.POST.get("close_date"))

        if not title or not open_time or not close_time or time_limit <= 0 or question_count <= 0 or close_time < open_time:
            messages.error(request, "Please check your inputs.")
            return render(request, "proctor/edit_quiz.html", {
                "quiz": quiz,
                "open_value": request.POST.get("open_date", ""),
                "close_value": request.POST.get("close_date", ""),
            })

        quiz.title = title
        quiz.question_count = question_count
        quiz.time_limit = time_limit
        quiz.open_time = open_time
        quiz.close_time = close_time
        quiz.save()

        messages.success(request, "Quiz updated.")
        url = reverse("profile") + "?" + urlencode({"tab": "quizzes"})
        return redirect(url)
    # GET: prefill
    def to_local_input(dt):
        return timezone.localtime(dt).strftime("%Y-%m-%dT%H:%M") if dt else ""
    return render(request, "proctor/edit_quiz.html", {
        "quiz": quiz,
        "open_value": to_local_input(quiz.open_time),
        "close_value": to_local_input(quiz.close_time),
    })
