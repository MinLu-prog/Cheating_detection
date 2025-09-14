# proctor/forms.py - FULL ENHANCED VERSION
from django import forms
from django.forms import inlineformset_factory, BaseInlineFormSet
from .models import Quiz, Question, Choice

# -------------------- Quiz Form --------------------
class QuizForm(forms.ModelForm):
    class Meta:
        model = Quiz
        fields = ['title', 'open_time', 'close_time', 'time_limit', 'question_count']
        widgets = {
            'title': forms.TextInput(attrs={'class': 'form-control'}),
            'open_time': forms.DateTimeInput(attrs={'class': 'form-control', 'type': 'datetime-local'}),
            'close_time': forms.DateTimeInput(attrs={'class': 'form-control', 'type': 'datetime-local'}),
            'time_limit': forms.NumberInput(attrs={'class': 'form-control', 'min': 1}),
            'question_count': forms.NumberInput(attrs={'class': 'form-control', 'min': 1}),
        }

# -------------------- Question Form --------------------
class QuestionForm(forms.ModelForm):
    QUESTION_TYPES = [
        ('MCQ', 'Multiple Choice'),
        ('TF', 'True / False'),
        ('FILL', 'Fill in the Blank'),
    ]

    question_type = forms.ChoiceField(
        choices=QUESTION_TYPES,
        widget=forms.Select(attrs={'class': 'form-select'}),
        required=True,
        label="Question Type"
    )

    class Meta:
        model = Question
        fields = ['question_type', 'text', 'points', 'order', 'section']
        widgets = {
            'text': forms.Textarea(attrs={'class': 'form-control', 'rows': 5}),
            'points': forms.NumberInput(attrs={'class': 'form-control', 'min': 1}),
            'order': forms.NumberInput(attrs={'class': 'form-control', 'min': 0}),
            'section': forms.Select(attrs={'class': 'form-select'}, choices=[
                ('Default', 'Default'),
                ('Section 1', 'Section 1'),
                ('Section 2', 'Section 2'),
            ]),
        }
        labels = {
            'text': 'Question Body',
            'points': 'Points',
            'order': 'Order',
            'section': 'Section'
        }

# -------------------- Answer / Choice Form --------------------
class AnswerForm(forms.ModelForm):
    class Meta:
        model = Choice
        fields = ['text', 'is_correct', 'order']
        widgets = {
            'text': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Option text'}),
            'is_correct': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
            'order': forms.HiddenInput(),
        }
        labels = {
            'text': '',
            'is_correct': 'Mark as correct',
        }

# -------------------- Base Choice Formset with Validation --------------------
class BaseChoiceFormSet(BaseInlineFormSet):
    def clean(self):
        super().clean()
        if any(self.errors):
            return

        parent_question = self.instance
        qtype = parent_question.question_type

        # --- MCQ / TF must have exactly one correct answer ---
        if qtype in ('MCQ', 'TF'):
            correct_count = sum(
                1 for form in self.forms
                if form.cleaned_data.get('is_correct') and not form.cleaned_data.get('DELETE', False)
            )
            if correct_count != 1:
                raise forms.ValidationError("MCQ/TF must have exactly one correct answer.")

        # --- FILL must have at least one non-empty answer text ---
        elif qtype == 'FILL':
            valid_answers = [
                form.cleaned_data.get('text', '').strip()
                for form in self.forms
                if not form.cleaned_data.get('DELETE', False)
            ]
            if not any(valid_answers):
                raise forms.ValidationError("Fill-in-the-blank must have at least one correct answer.")

# -------------------- Choice Formset --------------------
ChoiceFormSet = inlineformset_factory(
    parent_model=Question,
    model=Choice,
    form=AnswerForm,
    formset=BaseChoiceFormSet,
    extra=4,             # Default 4 choices per MCQ
    can_delete=True,
    min_num=0,           # allow 0 if it's FILL
    max_num=6,
    validate_min=False,  # don't enforce for FILL
    validate_max=True,
)

# -------------------- Simple Question Form --------------------
class SimpleQuestionForm(forms.ModelForm):
    """For backward compatibility with older code that expects only the 'text' field"""
    class Meta:
        model = Question
        fields = ['text']
