from .course import Course
from .edition import Edition
from .rubric import Rubric
from .student import Student
from .submission import AnsweredQuestion, Submission, SubmissionStatus

DOCUMENT_MODELS = [Student, Course, Edition, Rubric, Submission]

__all__ = [
    "DOCUMENT_MODELS",
    "AnsweredQuestion",
    "Course",
    "Edition",
    "Rubric",
    "Student",
    "Submission",
    "SubmissionStatus",
]
