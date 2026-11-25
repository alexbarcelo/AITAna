"""Column types shared by the ORM models."""

from typing import Any

from pydantic import TypeAdapter
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.types import TypeDecorator


class PydanticJSONB(TypeDecorator):
    """A JSONB column holding a Pydantic-typed value (a model, a list of
    models, ...): dumped to JSON on write, validated back into that type on
    read, so ORM attributes are real `Question`/`AnsweredQuestion`/...
    instances, never raw dicts.

    Change tracking: assigning a new value to the attribute always writes it
    (`compare_values` never reports "unchanged"). Mutating the stored value
    in place (`submission.answers[i].grade = ...`) is *not* seen by the ORM
    -- follow it with `sqlalchemy.orm.attributes.flag_modified(obj, "<attr>")`
    or the change is silently lost on commit."""

    impl = JSONB
    cache_ok = True

    def __init__(self, type_: Any) -> None:
        super().__init__()
        self.type_ = type_
        self._adapter = TypeAdapter(type_)

    def process_bind_param(self, value: Any, dialect: Any) -> Any:
        if value is None:
            return None
        return self._adapter.dump_python(value, mode="json")

    def process_result_value(self, value: Any, dialect: Any) -> Any:
        if value is None:
            return None
        return self._adapter.validate_python(value)

    def compare_values(self, x: Any, y: Any) -> bool:
        return False
