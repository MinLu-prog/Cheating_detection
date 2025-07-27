# utils.py inside proctor app

def is_teacher(user):
    return user.groups.filter(name='Teachers').exists()

def is_student(user):
    return user.groups.filter(name='Students').exists()
