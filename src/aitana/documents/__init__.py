from .batch import Batch, BatchType
from .course import Course
from .edition import Edition
from .grading_trace import GradingTrace
from .rubric import Rubric
from .student import Student
from .submission import AnsweredQuestion, Submission, SubmissionStatus

DOCUMENT_MODELS = [Student, Course, Edition, Rubric, Batch, Submission, GradingTrace]

__all__ = [
    "DOCUMENT_MODELS",
    "AnsweredQuestion",
    "Batch",
    "BatchType",
    "Course",
    "Edition",
    "GradingTrace",
    "Rubric",
    "Student",
    "Submission",
    "SubmissionStatus",
]
