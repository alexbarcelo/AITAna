from .batch import Batch, BatchType
from .course import Course
from .edition import Edition
from .rubric import Rubric
from .student import Student
from .submission import AnsweredQuestion, Submission, SubmissionStatus

DOCUMENT_MODELS = [Student, Course, Edition, Rubric, Batch, Submission]

__all__ = [
    "DOCUMENT_MODELS",
    "AnsweredQuestion",
    "Batch",
    "BatchType",
    "Course",
    "Edition",
    "Rubric",
    "Student",
    "Submission",
    "SubmissionStatus",
]
