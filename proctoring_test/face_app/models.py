# face_app/models.py
from django.db import models
from django.contrib.auth.models import User
import pickle

class UserFace(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE)
    descriptor = models.BinaryField()  # store face descriptor as bytes

    def set_descriptor(self, arr):
        self.descriptor = pickle.dumps(arr)

    def get_descriptor(self):
        return pickle.loads(self.descriptor)
