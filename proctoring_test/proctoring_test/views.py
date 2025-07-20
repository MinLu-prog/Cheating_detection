from django.http import HttpResponse
from django.shortcuts import render

def homepage(request):
    return render(request, 'welcome.html')

def about(request):
    return HttpResponse("Welcome to about page.")

def webcam(request):
    return HttpResponse("Welcome to webcam page.")