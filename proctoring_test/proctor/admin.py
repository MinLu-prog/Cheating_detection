from django.contrib import admin
from .models import Quiz, Question, Choice, StudentResponse, MatchingPair

# Inline for matching pairs
class MatchingPairInline(admin.TabularInline):
    model = MatchingPair
    extra = 1  # Number of empty rows to show

# Admin for Question
class QuestionAdmin(admin.ModelAdmin):
    inlines = [MatchingPairInline]
    list_display = ('text', 'quiz', 'question_type', 'order')

# Register models
admin.site.register(Quiz)
admin.site.register(Question, QuestionAdmin)  # Use QuestionAdmin here
admin.site.register(Choice)
admin.site.register(StudentResponse)

from .models import ProctorEvent

@admin.register(ProctorEvent)
class ProctorEventAdmin(admin.ModelAdmin):
    list_display = ('created_at','quiz','student','event_type','severity')
    list_filter  = ('quiz','event_type','severity','created_at')
    search_fields = ('student__username','message')