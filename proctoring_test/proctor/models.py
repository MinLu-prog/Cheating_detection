from django.db import models 
from django.contrib.auth.models import User
from django.core.validators import MinValueValidator, MaxValueValidator
from django.utils import timezone

class Quiz(models.Model):
    question_count = models.IntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    title = models.CharField(max_length=200)
    description = models.TextField(blank=True, null=True)  # Added description field
    teacher = models.ForeignKey(
        User, 
        on_delete=models.CASCADE, 
        related_name='quizzes_created',
        limit_choices_to={'groups__name': 'Teachers'}  # Ensures only teachers can be assigned
    )
    open_time = models.DateTimeField()
    close_time = models.DateTimeField()
    time_limit = models.PositiveIntegerField(
        help_text="Time limit in minutes",
        validators=[MinValueValidator(1), MaxValueValidator(300)]  # Max 5 hours
    )
    updated_at = models.DateTimeField(auto_now=True)
    
    STATUS_CHOICES = [
        ('draft', 'Draft'),
        ('published', 'Published'),
        ('archived', 'Archived'),
    ]
    status = models.CharField(
        max_length=10, 
        choices=STATUS_CHOICES, 
        default='draft',
        db_index=True  # Better for filtering
    )

    def __str__(self):
        return f"{self.title} (by {self.teacher.username})"

    class Meta:
        verbose_name_plural = "Quizzes"
        ordering = ['-created_at']  # Newest first by default
        constraints = [
            models.CheckConstraint(
                check=models.Q(close_time__gt=models.F('open_time')),
                name='close_time_after_open_time'
            )
        ]

class Question(models.Model):
    QUESTION_TYPES = [
        ('mcq', 'Multiple Choice'),
        ('tf', 'True/False'),
        ('blank', 'Fill in the Blank'),
        ('text', 'Written Answer'),
        ('match', 'Matching'),
        ('file', 'File Upload'),
    ]

    quiz = models.ForeignKey(
        Quiz, related_name='questions', on_delete=models.CASCADE, db_index=True
    )
    text = models.TextField()
    points = models.PositiveIntegerField(default=1, validators=[MinValueValidator(1)])
    order = models.PositiveIntegerField(default=0)
    section = models.CharField(max_length=100, default='Default', blank=True)
    question_type = models.CharField(max_length=10, choices=QUESTION_TYPES, default='mcq')

    def __str__(self):
        return f"[{self.get_question_type_display()}] Q{self.order}: {self.text[:50]}..."

    class Meta:
        ordering = ['order']
        unique_together = [('quiz', 'order')]

class Choice(models.Model):
    question = models.ForeignKey(
        Question, 
        related_name='choices', 
        on_delete=models.CASCADE,
        db_index=True
    )
    text = models.CharField(max_length=255)
    is_correct = models.BooleanField(default=False)
    order = models.PositiveIntegerField(default=0)

    def __str__(self):
        return f"{self.text} ({'✓' if self.is_correct else '✗'})"

    class Meta:
        ordering = ['order']
        unique_together = [('question', 'order')]  # Ensure order is unique per question

class StudentResponse(models.Model):
    student = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name='quiz_responses',
        limit_choices_to={'groups__name': 'Students'}
    )
    quiz = models.ForeignKey(Quiz, on_delete=models.CASCADE, related_name='responses')
    question = models.ForeignKey(Question, on_delete=models.CASCADE)

    # Different possible answer types:
    selected_choice = models.ForeignKey(Choice, null=True, blank=True, on_delete=models.SET_NULL)
    text_answer = models.TextField(blank=True, null=True)
    matched_pairs = models.JSONField(blank=True, null=True)  # e.g. {"left1":"right2", "left2":"right1"}
    uploaded_file = models.FileField(upload_to='quiz_uploads/', blank=True, null=True)

    submitted_at = models.DateTimeField(auto_now_add=True)
    is_correct = models.BooleanField(default=False)

    class Meta:
        unique_together = [['student', 'question']]
        indexes = [models.Index(fields=['student', 'quiz'])]

    def save(self, *args, **kwargs):
        # Auto-grade simple cases
        if self.question.question_type in ['mcq', 'tf'] and self.selected_choice:
            self.is_correct = self.selected_choice.is_correct
        elif self.question.question_type == 'blank' and self.text_answer:
            corrects = [ans.correct_text.lower().strip() for ans in self.question.blank_answers.all()]
            self.is_correct = self.text_answer.lower().strip() in corrects
        elif self.question.question_type == 'match' and self.matched_pairs:
            correct_pairs = {pair.left_text: pair.right_text for pair in self.question.matching_pairs.all()}
            self.is_correct = self.matched_pairs == correct_pairs
        else:
            # text/file = manual grading
            self.is_correct = False
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.student.username} → Q{self.question.order} ({self.question.get_question_type_display()})"

# --------------------- NEW: QuizResult / QuizAttempt ---------------------
class QuizResult(models.Model):
    """
    Aggregate result/attempt for a student on a quiz.
    We keep one QuizResult per (quiz, student) — this prevents retakes.
    If you want to support retakes later, add an `attempt_number` or `attempt` FK and
    remove the unique_together constraint.
    """
    quiz = models.ForeignKey(Quiz, on_delete=models.CASCADE, related_name='results')
    student = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name='quiz_results',
        limit_choices_to={'groups__name': 'Students'}
    )
    score = models.IntegerField(default=0)
    total = models.IntegerField(default=0)
    percent = models.FloatField(default=0.0)
    submitted_at = models.DateTimeField(auto_now_add=True)
    # optionally store a snapshot of answers (question_id -> selected_choice_id)
    answers = models.JSONField(null=True, blank=True)

    class Meta:
        unique_together = ('quiz', 'student')
        ordering = ['-submitted_at']

    def __str__(self):
        return f"{self.student.username} - {self.quiz.title} - {self.percent:.1f}%"

class BlankAnswer(models.Model):
    question = models.ForeignKey(Question, related_name='blank_answers', on_delete=models.CASCADE)
    correct_text = models.CharField(max_length=255)

    def __str__(self):
        return f"Blank answer for Q{self.question.id}: {self.correct_text}"


class MatchingPair(models.Model):
    question = models.ForeignKey(Question, related_name='matching_pairs', on_delete=models.CASCADE)
    left_text = models.CharField(max_length=255)
    right_text = models.CharField(max_length=255)

    def __str__(self):
        return f"{self.left_text} ↔ {self.right_text}"

from django.conf import settings 
class ProctorEvent(models.Model):
    SEVERITY = (('info','Info'), ('warn','Warn'), ('error','Error'))

    quiz      = models.ForeignKey('Quiz', on_delete=models.CASCADE, related_name='proctor_events')
    student   = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='proctor_events')
    event_type = models.CharField(max_length=50, db_index=True)  # e.g. multi_face, no_face, phone, head_pose, eye_gaze, terminated, stream_error
    severity   = models.CharField(max_length=10, choices=SEVERITY, default='warn')
    message    = models.TextField(blank=True)
    metadata   = models.JSONField(default=dict, blank=True)
    # Optional snapshot (needs Pillow + MEDIA_* configured)
    frame      = models.ImageField(upload_to='proctor_snaps/%Y/%m/%d', null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['quiz', 'student', 'event_type', 'created_at']),
        ]

    def __str__(self):
        return f"{self.created_at:%Y-%m-%d %H:%M:%S} · {self.student} · {self.event_type}"