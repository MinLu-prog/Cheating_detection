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
            # Check if face dataset exists
            base_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "faces")
            user_face_dir = os.path.join(base_path, user.username)

            if not os.path.exists(user_face_dir) or not os.listdir(user_face_dir):
                # No folder or empty folder → need to capture
                logger.info(f"No face data found for {user.username}, redirecting to capture.")
                request.session['pending_face_capture'] = True
                return redirect('capture_face')
            else:
                return redirect('student_dashboard')

        elif user.is_superuser:
            return redirect('/admin/')

        return redirect('home')

    return render(request, 'proctor/login.html')

from django.core.paginator import Paginator
# views.py
@login_required
def profile(request):
    role = "User"
    base_template = "base.html"

    # Default fallbacks
    user_quizzes_qs = Quiz.objects.none()
    published_count = 0

    # Role routing
    if request.user.groups.filter(name="Teachers").exists():
        role = "Teacher"
        base_template = "base.html"
        user_quizzes_qs = Quiz.objects.filter(teacher=request.user).order_by('-created_at')
        published_count = user_quizzes_qs.filter(status='published').count()

    elif request.user.groups.filter(name="Students").exists():
        role = "Student"
        base_template = "base_student.html"
        # Students don't own quizzes in this view; keep empty queryset

    elif request.user.groups.filter(name="Admins").exists():
        role = "Admin"
        base_template = "base.html"
        user_quizzes_qs = Quiz.objects.all().order_by('-created_at')
        published_count = user_quizzes_qs.filter(status='published').count()

    # Paginate (table only)
    paginator = Paginator(user_quizzes_qs, 10)  # 10 per page
    qpage = request.GET.get("qpage")
    quizzes_page = paginator.get_page(qpage)

    context = {
        "user_quizzes": user_quizzes_qs,   # for counts
        "quizzes_page": quizzes_page,      # paginated table
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

    # ✅ Quizzes currently open that student has NOT taken yet
    available_quizzes = Quiz.objects.filter(
        open_time__lte=now,
        close_time__gte=now,
        status='published'
    ).exclude(
        responses__student=request.user
    )

    # ✅ Only quizzes the student has taken (distinct)
    submitted_quizzes = (
        StudentResponse.objects
        .filter(student=request.user)
        .values('quiz')
        .distinct()
    )

    quiz_results = []
    for item in submitted_quizzes:
        quiz_id = item['quiz']
        quiz = Quiz.objects.get(id=quiz_id)
        responses = StudentResponse.objects.filter(student=request.user, quiz=quiz)
        correct_answers = responses.filter(is_correct=True).count()
        total_questions = quiz.questions.count()
        percentage = round((correct_answers / total_questions) * 100, 2) if total_questions > 0 else 0

        quiz_results.append({
            'quiz_id': quiz.id,
            'quiz_title': quiz.title,
            'correct_answers': correct_answers,
            'total_questions': total_questions,
            'percentage': percentage,
        })

    context = {
        'available_quizzes': available_quizzes,
        'results': quiz_results,
        'now': now,
    }

    # --- Pagination ---
    paginator = Paginator(quiz_results, 5)  # show 5 results per page
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)

    return render(request, 'proctor/student_dashboard.html', {
        'available_quizzes': available_quizzes,
        'page_obj': page_obj,
        'now': now,
    })


    return render(request, 'proctor/student_dashboard.html', context)
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

import os, signal, json, logging
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.shortcuts import render, redirect, get_object_or_404
from django.utils.timezone import now

from .models import Quiz, Choice, StudentResponse  # adjust paths if needed

logger = logging.getLogger(__name__)

# If you manage a separate face process per user, ensure this dict exists somewhere central.
face_process = {}  # {username: pid}


@login_required
def take_quiz(request, quiz_id):
    if not is_student(request.user):
        messages.error(request, "Access restricted to students.")
        return redirect('home')

    quiz = get_object_or_404(Quiz, id=quiz_id)

    # Check availability
    now_ts = now()
    if now_ts < quiz.open_time or now_ts > quiz.close_time:
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
                    posted_values = request.POST.getlist(field_name)  # [] if none (for checkboxes)
                    single_value = request.POST.get(field_name)       # None if none (for radios/text)

                    # normalize type
                    qtype = (question.question_type or "").lower().strip()

                    # ---- MULTIPLE CHOICE ----
                    if qtype in ("multiple", "mcq", "multiplechoice", "multiple_choice"):
                        if posted_values:
                            # multi-select (checkboxes) or repeated radios
                            for val in posted_values:
                                try:
                                    cid = int(val)
                                    choice = Choice.objects.get(id=cid, question=question)
                                    StudentResponse.objects.create(
                                        student=request.user,
                                        quiz=quiz,
                                        question=question,
                                        selected_choice=choice
                                    )
                                    saved_any = True
                                except (ValueError, Choice.DoesNotExist):
                                    logger.warning(f"[MCQ] Invalid choice '{val}' for question {question.id}")
                        elif single_value:
                            # single-selection (radio)
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
                            except (ValueError, Choice.DoesNotExist):
                                logger.warning(f"[MCQ] Invalid single value '{single_value}' for question {question.id}")
                        else:
                            logger.info(f"[MCQ] No answer posted for question {question.id}")

                    # ---- TRUE / FALSE ----
                    elif qtype in ("truefalse", "tf", "true_false"):
                        # many setups also send a choice id for TF; we accept either raw text or id
                        answer = single_value or (posted_values[0] if posted_values else None)
                        if answer is None:
                            logger.info(f"[TF] No answer posted for question {question.id}")
                        else:
                            # If it's a choice id from your TF choices, store selected_choice;
                            # otherwise store text_answer for safety.
                            try:
                                cid = int(answer)
                                choice = Choice.objects.get(id=cid, question=question)
                                StudentResponse.objects.create(
                                    student=request.user,
                                    quiz=quiz,
                                    question=question,
                                    selected_choice=choice
                                )
                            except (ValueError, Choice.DoesNotExist):
                                StudentResponse.objects.create(
                                    student=request.user,
                                    quiz=quiz,
                                    question=question,
                                    text_answer=str(answer).strip()
                                )
                            saved_any = True

                    # ---- FILL IN THE BLANK ----
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
                            logger.info(f"[FILL] No answer for question {question.id}")

                    # ---- MATCHING ----
                    elif qtype in ("match", "matching"):
                        # Look for individual matching pair fields
                        map_by_left_label = {}
                        
                        # Extract all fields that start with the question ID pattern
                        matching_prefix = f"question_{question.id}_"
                        for key, value in request.POST.items():
                            if key.startswith(matching_prefix):
                                left_text = key[len(matching_prefix):].strip()
                                right_text = value.strip()
                                if left_text and right_text:
                                    map_by_left_label[left_text] = right_text
                        
                        # Also check for JSON payload
                        raw_answer = single_value or (posted_values[0] if posted_values else "")
                        if raw_answer and raw_answer.strip():
                            try:
                                parsed_json = json.loads(raw_answer)
                                if isinstance(parsed_json, dict):
                                    # Merge with JSON data
                                    for k, v in parsed_json.items():
                                        if str(k).strip() and str(v).strip():
                                            map_by_left_label[str(k).strip()] = str(v).strip()
                            except Exception:
                                logger.warning(f"[MATCH] Invalid matching JSON for q {question.id}: {raw_answer}")

                        # Create normalized payload
                        normalized_payload = {
                            "mapByLeftLabel": map_by_left_label,
                            "pairs": [{"left": k, "right": v} for k, v in map_by_left_label.items()],
                        }

                        StudentResponse.objects.create(
                            student=request.user,
                            quiz=quiz,
                            question=question,
                            matched_pairs=normalized_payload
                        )
                        saved_any = True

                    # ---- UNKNOWN/OTHER ----
                    else:
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
                            logger.info(f"[OTHER] No answer posted for question {question.id}")

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

    # GET: render quiz page with randomized matching questions
    import random
    
    questions_list = []
    for question in quiz.questions.all().order_by("order"):
        # Create a copy of the question with shuffled pairs for matching questions
        if question.question_type in ("match", "matching", "matching_pairs"):
            matching_pairs = list(question.matching_pairs.all())
            random.shuffle(matching_pairs)  # Randomize the order
            # Add shuffled_pairs as an attribute to the question object
            question.shuffled_pairs = matching_pairs
        else:
            question.shuffled_pairs = None
            
        questions_list.append(question)

    return render(request, "proctor/take_quiz.html", {
        "quiz": quiz,
        "questions": questions_list,
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

# Toggle face recognition on/off  # Set to True to enable recognition again

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

    # Path to this user's dataset file
    # Skip if already exists

    if request.method == 'POST':
        # Absolute path to face_data.py

        try:
            # Launch script in a new console window (Windows)
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


# General views
def homepage(request):
    return render(request, 'proctor/home.html')

def about(request):
    return render(request, 'proctor/about.html')
from django.shortcuts import render, get_object_or_404
from .models import Quiz, StudentResponse
from collections import defaultdict
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, render
from django.db.models import Prefetch

# helper: normalize type names to a canonical token
def _norm_type(s: str) -> str:
    if not s:
        return ""
    t = s.strip().lower().replace("_", "").replace("-", "").replace(" ", "")
    # map common variants
    if t in {"multiple", "mcq", "multiplechoice"}:
        return "mcq"
    if t in {"truefalse", "tf", "truef", "truefalsequestion"}:
        return "tf"
    if t in {"fill", "blank", "fillintheblank", "fillintheblanks", "fillintheblankquestion", "fillintheblankqs"}:
        return "fill"
    if t in {"match", "matching"}:
        return "match"
    return t

from collections import defaultdict
from django.shortcuts import get_object_or_404, render
from django.db.models import Prefetch

@login_required
def teacher_quiz_results(request, quiz_id):
    """
    Shows per-student results for a quiz.
    Aggregates responses per (student, question) to handle multi-select MCQ
    and matching questions with partial scoring.
    """
    quiz = get_object_or_404(Quiz, id=quiz_id)

    # Prefetch related objects to reduce queries
    questions_qs = quiz.questions.all().prefetch_related(
        Prefetch("choices", queryset=Choice.objects.all()),
        Prefetch("blank_answers", queryset=BlankAnswer.objects.all()),
        Prefetch("matching_pairs", queryset=MatchingPair.objects.all()),
    )

    # Fetch all student responses for this quiz
    responses = (
        StudentResponse.objects
        .filter(quiz=quiz)
        .select_related("student", "question", "selected_choice")
        .order_by("student_id", "question_id", "id")
    )

    # Organize responses per student and per question
    by_student = defaultdict(lambda: defaultdict(list))
    for r in responses:
        by_student[r.student][r.question].append(r)

    student_results = {}
    total_questions = questions_qs.count()

    for student, qmap in by_student.items():
        student_results[student] = {
            "score": 0.0,
            "total": total_questions,
            "answers": []
        }

        for question in questions_qs:
            qtype = _norm_type(question.question_type)
            rs = qmap.get(question, [])

            selected_display = "No answer"
            correct_display = ""
            correct_bool = False
            partial_credit = 0.0
            correct_pairs_list = []

            # --- MCQ or True/False ---
            if qtype in {"mcq", "tf"}:
                correct_choices = list(question.choices.filter(is_correct=True))
                correct_display = ", ".join(c.text for c in correct_choices)
                correct_ids = {c.id for c in correct_choices}

                selected_ids = {r.selected_choice_id for r in rs if r.selected_choice_id}
                if selected_ids:
                    sel_texts = [r.selected_choice.text for r in rs if r.selected_choice]
                    selected_display = ", ".join(sel_texts) if sel_texts else "No answer"
                    correct_bool = (selected_ids == correct_ids)

            # --- Fill-in ---
            elif qtype == "fill":
                correct_texts = [b.correct_text for b in question.blank_answers.all()]
                correct_display = ", ".join(correct_texts)

                text_answers = [(r.text_answer or "").strip() for r in rs if (r.text_answer or "").strip()]
                if text_answers:
                    selected_display = ", ".join(text_answers)
                    norm = lambda s: s.strip().lower()
                    corr_set = {norm(t) for t in correct_texts if t is not None}
                    sel_set = {norm(t) for t in text_answers}
                    correct_bool = bool(corr_set & sel_set)

            # --- Matching Pairs ---
            elif qtype == "match":
                correct_pairs = {mp.left_text: mp.right_text for mp in question.matching_pairs.all()}
                correct_display = ", ".join([f"{l} → {r}" for l, r in correct_pairs.items()]) if correct_pairs else ""
                correct_pairs_list = [{"left": l, "right": r} for l, r in correct_pairs.items()]


                student_map = {}
                for r in rs:
                    mp = r.matched_pairs or {}
                    if isinstance(mp, dict):
                        if "mapByLeftLabel" in mp and isinstance(mp["mapByLeftLabel"], dict):
                            student_map.update({str(k): str(v) for k, v in mp["mapByLeftLabel"].items()})
                        elif "pairs" in mp and isinstance(mp["pairs"], list):
                            for p in mp["pairs"]:
                                ltxt = str(p.get("lText", "")).strip()
                                rtxt = str(p.get("rText", "")).strip()
                                if ltxt:
                                    student_map[ltxt] = rtxt
                        else:
                            for k, v in mp.items():
                                if isinstance(k, str):
                                    student_map[str(k)] = str(v)

                if student_map:
                    pairs_list = []
                    correct_count = 0
                    total_left = max(len(correct_pairs), 1)
                    for l, r in student_map.items():
                        is_correct = correct_pairs.get(l) == r
                        if is_correct:
                            correct_count += 1
                        pairs_list.append({"left": l, "right": r, "correct": is_correct})
                    selected_display = pairs_list
                    partial_credit = correct_count / total_left
                    correct_bool = (partial_credit == 1.0)

            # --- Unknown types ---
            else:
                text_answers = [(r.text_answer or "").strip() for r in rs if (r.text_answer or "").strip()]
                if text_answers:
                    selected_display = ", ".join(text_answers)

            # --- Scoring ---
            if qtype == "match":
                student_results[student]["score"] += partial_credit
            else:
                student_results[student]["score"] += 1.0 if correct_bool else 0.0

            # --- Record answer for template ---
            student_results[student]["answers"].append({
                "question": question.text,
                "type": qtype,
                "selected": selected_display,
                "correct_answer": correct_display,
                "correct_pairs": correct_pairs_list,
                "correct": correct_bool,
                "partial": partial_credit if qtype == "matching_pairs" else None,
            })

    return render(request, "proctor/teacher_quiz_results.html", {
        "quiz": quiz,
        "student_results": student_results
    })


# views.py
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpResponseForbidden, HttpResponseNotAllowed
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse

from .models import Quiz


@login_required
def delete_quiz(request, quiz_id: int):
    """
    Delete a quiz.
    Teachers can delete only their own quizzes.
    Admins (superuser or in 'Admins' group) can delete any quiz.
    """
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])

    user = request.user
    is_admin = user.is_superuser or user.groups.filter(name__in=["Admins"]).exists()
    is_teacher = user.groups.filter(name="Teachers").exists()

    if not (is_teacher or is_admin):
        return HttpResponseForbidden("You don't have permission to delete this quiz.")

    # 1) Fetch regardless of owner to give a precise error
    quiz = get_object_or_404(Quiz, pk=quiz_id)

    # 2) Ownership/permission check
    owner_id = getattr(quiz, "teacher_id", None)  # adjust if your field is named differently
    if not is_admin and owner_id != user.id:
        # You found the quiz, but you aren't allowed to delete it
        return HttpResponseForbidden("You do not own this quiz, so you cannot delete it.")

    # 3) Delete and redirect
    title = getattr(quiz, "title", f"Quiz #{quiz.id}")
    quiz.delete()
    messages.success(request, f'Quiz "{title}" has been deleted.')

    next_url = request.POST.get("next")
    if next_url:
        return redirect(next_url)

    referer = request.META.get("HTTP_REFERER")
    if referer:
        # keep #quizzes tab if you use tabs
        if "#quizzes" not in referer:
            referer += "#quizzes"
        return redirect(referer)

    try:
        return redirect(reverse("teacher_dashboard") + "#quizzes")
    except Exception:
        return redirect("/#quizzes")
    
from django.shortcuts import render, get_object_or_404, redirect
from django.contrib.auth.decorators import login_required
from django.http import HttpResponseForbidden, HttpResponseNotAllowed
from django.contrib import messages
from django.urls import reverse
from .models import Quiz

@login_required
def edit_quiz(request, quiz_id: int):
    user = request.user
    is_admin = user.is_superuser or user.groups.filter(name__in=["Admins"]).exists()
    is_teacher = user.groups.filter(name="Teachers").exists()

    if not (is_teacher or is_admin):
        return HttpResponseForbidden("You don't have permission to edit quizzes.")

    # Fetch the quiz
    quiz = get_object_or_404(Quiz, pk=quiz_id)

    # Check ownership if not admin
    if not is_admin and quiz.teacher_id != user.id:
        return HttpResponseForbidden("You do not own this quiz.")

    if request.method == "POST":
        # Update quiz fields from form
        quiz.title = request.POST.get("title", quiz.title)
        quiz.time_limit = int(request.POST.get("time_limit", quiz.time_limit))
        quiz.question_count = int(request.POST.get("question_count", quiz.question_count))

        # Parse datetime-local input
        from django.utils.dateparse import parse_datetime
        open_val = request.POST.get("open_date")
        close_val = request.POST.get("close_date")
        if open_val:
            dt = parse_datetime(open_val)
            if dt:
                from django.utils import timezone
                if timezone.is_naive(dt):
                    dt = timezone.make_aware(dt, timezone.get_current_timezone())
                quiz.open_time = dt
        if close_val:
            dt = parse_datetime(close_val)
            if dt:
                from django.utils import timezone
                if timezone.is_naive(dt):
                    dt = timezone.make_aware(dt, timezone.get_current_timezone())
                quiz.close_time = dt

        quiz.save()
        messages.success(request, f'Quiz "{quiz.title}" updated successfully.')
        return redirect(reverse("edit_quiz", args=[quiz.id]))

    # For GET: convert datetime for datetime-local input
    open_value = quiz.open_time.strftime("%Y-%m-%dT%H:%M") if quiz.open_time else ""
    close_value = quiz.close_time.strftime("%Y-%m-%dT%H:%M") if quiz.close_time else ""

    return render(request, "proctor/edit_quiz.html", {
        "quiz": quiz,
        "open_value": open_value,
        "close_value": close_value,
    })

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

# views.py
from collections import defaultdict
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.db.models import Prefetch
from django.shortcuts import redirect, render
from django.utils import timezone

from .models import Quiz, Choice, BlankAnswer, MatchingPair, StudentResponse
from .utils import is_teacher  # if you have this helper; else inline the group check


def _norm_type(s: str) -> str:
    """Normalize question_type into canonical tokens: mcq, tf, fill, match."""
    if not s:
        return ""
    t = s.strip().lower().replace("_", "").replace("-", "").replace(" ", "")
    if t in {"multiple", "mcq", "multiplechoice"}:
        return "mcq"
    if t in {"truefalse", "tf", "truef", "truefalsequestion"}:
        return "tf"
    if t in {"fill", "blank", "fillintheblank", "fillintheblanks", "fib"}:
        return "fill"
    if t in {"match", "matching"}:
        return "match"
    return t


@login_required
def results_overview(request):
    if not is_teacher(request.user):
        messages.error(request, 'Access restricted to teachers.')
        return redirect('home')

    # All quizzes for this teacher
    quizzes = (
        Quiz.objects.filter(teacher=request.user)
        .order_by('-created_at')
        .prefetch_related(
            Prefetch("questions__choices", queryset=Choice.objects.all()),
            Prefetch("questions__blank_answers", queryset=BlankAnswer.objects.all()),
            Prefetch("questions__matching_pairs", queryset=MatchingPair.objects.all()),
        )
    )

    # Top-line counts
    total_quizzes = quizzes.count()
    published_quizzes = quizzes.filter(status='published').count()
    draft_quizzes = quizzes.filter(status='draft').count()

    # We’ll compute totals from StudentResponse per quiz as needed
    all_responses_qs = StudentResponse.objects.filter(quiz__teacher=request.user)
    total_responses = all_responses_qs.count()

    quiz_stats = []
    performance_data = []  # last 5 by created_at (already ordered)

    def score_for_student_question(question, responses_for_q):
        """
        Return a float in [0,1] representing the score for this question
        for a given student (aggregate all StudentResponse rows).
        """
        qtype = _norm_type(question.question_type)

        if qtype in {"mcq", "tf"}:
            correct_ids = set(question.choices.filter(is_correct=True).values_list("id", flat=True))
            selected_ids = {r.selected_choice_id for r in responses_for_q if r.selected_choice_id}
            # exact set match → 1; else 0
            return 1.0 if selected_ids == correct_ids else 0.0

        if qtype == "fill":
            correct_texts = [b.correct_text for b in question.blank_answers.all()]
            if not correct_texts:
                return 0.0
            norm = lambda s: (s or "").strip().lower()
            corr_set = {norm(t) for t in correct_texts}
            answers = [norm(r.text_answer) for r in responses_for_q if (r.text_answer or "").strip()]
            sel_set = set(a for a in answers if a)
            return 1.0 if (corr_set & sel_set) else 0.0  # simple policy: any one matches

        if qtype == "match":
            # Build correct mapping
            correct_map = {mp.left_text: mp.right_text for mp in question.matching_pairs.all()}
            if not correct_map:
                return 0.0

            # Extract student's map (merge if multiple rows)
            student_map = {}
            for r in responses_for_q:
                mp = r.matched_pairs or {}
                if not isinstance(mp, dict):
                    continue
                if "mapByLeftLabel" in mp and isinstance(mp["mapByLeftLabel"], dict):
                    for k, v in mp["mapByLeftLabel"].items():
                        student_map[str(k)] = str(v)
                elif "pairs" in mp and isinstance(mp["pairs"], list):
                    for p in mp["pairs"]:
                        ltxt = str(p.get("lText", "")).strip()
                        rtxt = str(p.get("rText", "")).strip()
                        if ltxt:
                            student_map[ltxt] = rtxt
                else:
                    # legacy: flat dict
                    for k, v in mp.items():
                        if isinstance(k, str):
                            student_map[str(k)] = str(v)

            # Fractional credit: correct matches / total left sides
            total_left = max(len(correct_map), 1)
            correct_count = sum(1 for l, r in student_map.items() if correct_map.get(l) == r)
            return correct_count / total_left

        # Unknown: 0 credit by default (or change to len(text_answer)>0 ? 1 : 0)
        return 0.0

    # Build stats per quiz
    for idx, quiz in enumerate(quizzes):
        questions = list(quiz.questions.all())
        total_questions = len(questions) if questions else 0

        # Collect this quiz's responses and group by student, then by question
        rs = (
            StudentResponse.objects
            .filter(quiz=quiz)
            .select_related("student", "question", "selected_choice")
            .order_by("student_id", "question_id", "id")
        )

        per_student = defaultdict(lambda: defaultdict(list))  # student -> question -> list[responses]
        for r in rs:
            per_student[r.student][r.question].append(r)

        # Compute per-student percentages
        percentages = []
        for student, qmap in per_student.items():
            if total_questions == 0:
                continue
            total_points = 0.0
            # ensure every question gets evaluated (even if unanswered)
            for q in questions:
                total_points += score_for_student_question(q, qmap.get(q, []))
            pct = (total_points / total_questions) * 100.0
            percentages.append(pct)

        if percentages:
            avg_score = sum(percentages) / len(percentages)
            max_score = max(percentages)
            min_score = min(percentages)
            students_count = len(percentages)
        else:
            avg_score = max_score = min_score = 0.0
            students_count = 0

        quiz_stats.append({
            'quiz': quiz,
            'students_count': students_count,
            'avg_score': round(avg_score, 1),
            'max_score': round(max_score, 1),
            'min_score': round(min_score, 1),
            'total_questions': total_questions,
        })

        # Build performance trend entry for first 5 quizzes
        if idx < 5:
            performance_data.append({
                'quiz_title': quiz.title,
                'success_rate': round(avg_score, 1),  # use avg_score as success_rate proxy
                'created_date': quiz.created_at,
            })

    # Recent activity: last 10 responses, with computed correctness
    recent_qs = (
        StudentResponse.objects
        .filter(quiz__teacher=request.user)
        .select_related('student', 'quiz', 'question', 'selected_choice')
        .order_by('-id')[:10]
    )

    recent_items = []
    for r in recent_qs:
        # compute correctness for THIS single response within its question context
        # For MCQ multi-select, we consider all responses for this student+question near this id window
        siblings = (
            StudentResponse.objects
            .filter(quiz=r.quiz, student=r.student, question=r.question)
            .select_related("selected_choice")
        )
        correct = score_for_student_question(r.question, list(siblings)) >= 1.0
        recent_items.append({
            "student_name": r.student.get_full_name() or r.student.username,
            "quiz_title": r.quiz.title,
            "question_text": getattr(r.question, "text", ""),
            "correct": correct,
            "created_at": getattr(r, "created_at", None) or timezone.now(),
        })

    context = {
        'total_quizzes': total_quizzes,
        'published_quizzes': published_quizzes,
        'draft_quizzes': draft_quizzes,
        'total_responses': total_responses,
        'quiz_stats': quiz_stats,
        'performance_data': performance_data,
        'recent_items': recent_items,   # <-- use this in template (see below)
        'now': timezone.now(),
    }
    return render(request, 'proctor/results_overview.html', context)

from django.contrib.auth.decorators import login_required
from django.utils import timezone
from django.shortcuts import render, get_object_or_404, redirect
from django.utils.dateparse import parse_datetime
from django.http import HttpResponse
from django.views.decorators.cache import never_cache
from django.contrib import messages
from .models import Quiz, ProctorAlert
from .utils import is_teacher  # your helper to check teacher role

@login_required
@never_cache
def proctor_logs(request, quiz_id):
    if not is_teacher(request.user):
        messages.error(request, "Access restricted to teachers.")
        return redirect("home")

    quiz = get_object_or_404(Quiz, id=quiz_id, teacher=request.user)
    qs = ProctorAlert.objects.filter(quiz=quiz).select_related("student", "quiz").order_by("-timestamp")

    # filters
    student = request.GET.get("student")
    if student:
        qs = qs.filter(student__username__icontains=student)

    evt_type = request.GET.get("type")
    if evt_type:
        qs = qs.filter(alert_type__icontains=evt_type)

    since = request.GET.get("since")
    if since:
        dt = parse_datetime(since)
        if dt:
            if timezone.is_naive(dt):
                dt = timezone.make_aware(dt, timezone.get_current_timezone())
            qs = qs.filter(timestamp__gte=dt)

    if request.GET.get("format") == "csv":
        import csv
        resp = HttpResponse(content_type="text/csv")
        resp["Content-Disposition"] = f'attachment; filename="proctor_alerts_{quiz.id}.csv"'
        writer = csv.writer(resp)
        writer.writerow(["timestamp", "student", "alert_type", "severity", "message"])
        for e in qs.iterator():
            writer.writerow([e.timestamp.isoformat(), e.student.username, e.alert_type, getattr(e, "severity", ""), getattr(e, "message", "")])
        return resp

    return render(request, "proctor/proctor_logs.html", {
        "quiz": quiz,
        "events": qs[:1000],
    })


@login_required
@never_cache
def proctor_logs_partial(request, quiz_id):
    if not is_teacher(request.user):
        return HttpResponse(status=403)

    quiz = get_object_or_404(Quiz, id=quiz_id, teacher=request.user)
    qs = ProctorAlert.objects.filter(quiz=quiz).select_related("student", "quiz").order_by("-timestamp")

    latest_ts = request.GET.get("latest_ts")
    if latest_ts:
        dt = parse_datetime(latest_ts)
        if dt:
            if timezone.is_naive(dt):
                dt = timezone.make_aware(dt, timezone.get_current_timezone())
            qs = qs.filter(timestamp__gt=dt)

    events = qs[:200]
    return render(request, "proctor/_proctor_rows.html", {"events": events})


@login_required
@never_cache
def proctor_logs_all(request):
    if not is_teacher(request.user):
        messages.error(request, "Access restricted to teachers.")
        return redirect("home")

    qs = ProctorAlert.objects.select_related("student", "quiz").order_by("-timestamp")

    quiz_id = request.GET.get("quiz")
    if quiz_id and quiz_id != "all":
        qs = qs.filter(quiz_id=quiz_id)

    student = request.GET.get("student")
    if student:
        qs = qs.filter(student__username__icontains=student)

    evt_type = request.GET.get("type")
    if evt_type:
        qs = qs.filter(alert_type__icontains=evt_type)

    # CSV export
    if request.GET.get("format") == "csv":
        import csv
        resp = HttpResponse(content_type="text/csv")
        resp["Content-Disposition"] = 'attachment; filename="proctor_alerts_all.csv"'
        writer = csv.writer(resp)
        writer.writerow(["timestamp", "quiz", "student", "alert_type", "message"])
        for e in qs.iterator():
            writer.writerow([e.timestamp.isoformat(), e.quiz.title, e.student.username, e.alert_type, e.message or ""])
        return resp

    quizzes = Quiz.objects.filter(teacher=request.user).only("id", "title").order_by("title")

    return render(request, "proctor/proctor_logs_all.html", {
        "events": qs[:1000],
        "quizzes": quizzes,
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

from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from django.contrib.auth.decorators import login_required

@login_required
def snapshot_view(request, alert_id):
    alert = get_object_or_404(ProctorAlert, id=alert_id)

    if not alert.snapshot_data:
        return HttpResponse("No snapshot available", status=404)

    return HttpResponse(alert.snapshot_data, content_type=alert.snapshot_mime)
