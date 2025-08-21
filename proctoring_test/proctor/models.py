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
    quiz = models.ForeignKey(
        Quiz, 
        related_name='questions', 
        on_delete=models.CASCADE,
        db_index=True
    )
    text = models.TextField()
    points = models.PositiveIntegerField(
        default=1,
        validators=[MinValueValidator(1)]
    )
    order = models.PositiveIntegerField(default=0)

    def __str__(self):
        return f"Q{self.order}: {self.text[:50]}..." 

    class Meta:
        ordering = ['order']
        unique_together = [('quiz', 'order')]  # Ensure order is unique per quiz

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
        User, 
        on_delete=models.CASCADE, 
        related_name='quiz_responses',
        limit_choices_to={'groups__name': 'Students'}  # Only students can have responses
    )
    quiz = models.ForeignKey(Quiz, on_delete=models.CASCADE, related_name='responses')
    question = models.ForeignKey(Question, on_delete=models.CASCADE)
    selected_choice = models.ForeignKey(Choice, on_delete=models.CASCADE)
    submitted_at = models.DateTimeField(auto_now_add=True)
    is_correct = models.BooleanField(default=False)  # Cache whether answer was correct

    class Meta:
        unique_together = [['student', 'question']]
        indexes = [
            models.Index(fields=['student', 'quiz']),  # Better query performance
        ]

    def save(self, *args, **kwargs):
        # Automatically set is_correct based on choice
        self.is_correct = self.selected_choice.is_correct
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.student.username}'s answer to Q{self.question.order} ({'Correct' if self.is_correct else 'Incorrect'})"


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
