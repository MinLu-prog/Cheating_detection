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

logger = logging.getLogger(__name__)

# Check functions
def is_teacher(user):
    return user.groups.filter(name='Teachers').exists()

def is_student(user):
    return user.groups.filter(name='Students').exists()

# Login view
def login_view(request):
    logger.debug(f"Request method: {request.method}, user: {request.user}, authenticated: {request.user.is_authenticated}")
    
    if request.user.is_authenticated:
        logger.debug("User is authenticated, redirecting based on role...")
        if is_teacher(request.user):
            logger.debug("Redirecting to teacher_dashboard")
            return redirect('teacher_dashboard')
        elif is_student(request.user):
            logger.debug("Redirecting to student_dashboard")
            return redirect('student_dashboard')
        elif request.user.is_superuser:
            logger.debug("Redirecting to admin")
            return redirect('/admin/')
        logger.debug("Redirecting to home")
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

        if is_teacher(user):
            return redirect('teacher_dashboard')
        elif is_student(user):
            return redirect('student_dashboard')
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

    student_responses = StudentResponse.objects.filter(student=request.user).select_related('quiz', 'question', 'selected_choice')

    return render(request, 'proctor/student_dashboard.html', {
        'available_quizzes': available_quizzes,
        'results': student_responses,
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

@login_required
def take_quiz(request, quiz_id):
    if not is_student(request.user):
        messages.error(request, 'Access restricted to students.')
        return redirect('home')
        
    quiz = get_object_or_404(Quiz, id=quiz_id)
    now = timezone.now()
    
    if now < quiz.open_time or now > quiz.close_time:
        messages.error(request, 'Quiz is not currently available')
        return redirect('available_quizzes')
    
    if StudentResponse.objects.filter(student=request.user, quiz=quiz).exists():
        messages.warning(request, 'You have already taken this quiz')
        return redirect('student_dashboard')

    if request.method == 'POST':
        try:
            for question in quiz.questions.all():
                selected_choice_id = request.POST.get(f'question_{question.id}')
                if selected_choice_id:
                    selected_choice = get_object_or_404(Choice, id=selected_choice_id)
                    StudentResponse.objects.create(
                        student=request.user,
                        quiz=quiz,
                        question=question,
                        selected_choice=selected_choice,
                        is_correct=selected_choice.is_correct
                    )
            messages.success(request, 'Quiz submitted successfully!')
            return redirect('student_dashboard')
        except Exception as e:
            logger.error(f"Quiz submission error: {str(e)}")
            messages.error(request, 'Error submitting quiz')
            return redirect('available_quizzes')

    return render(request, 'proctor/take_quiz.html', {
        'quiz': quiz,
        'questions': quiz.questions.all().order_by('order'),
        'time_limit': quiz.time_limit * 60
    })

# General views
def homepage(request):
    return render(request, 'proctor/home.html')

def about(request):
    return render(request, 'proctor/about.html')