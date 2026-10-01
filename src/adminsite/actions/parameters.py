"""What an action's method is handed and what it asks for, read from its parameters.

A parameter typed `Request`, `AsyncSession` or `SessionAdapter` is handed
over, as is the selection or the record the action runs on. Every other
parameter is a value the dialog asks for: its type picks the input, and
`Annotated[str, Input(label="Reason")]` words it.
"""

import dataclasses
import inspect
import types
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, time
from decimal import Decimal
from enum import Enum
from typing import (
    Annotated,
    Any,
    Final,
    Literal,
    TypeAlias,
    TypeVar,
    Union,
    get_args,
    get_origin,
    get_type_hints,
)
from uuid import UUID

from sqlalchemy import inspect as sqlalchemy_inspect
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapper
from starlette.datastructures import UploadFile
from starlette.requests import HTTPConnection

from adminsite.actions.action import ON_RECORD, ON_SELECTION, ON_VIEW, RESERVED_INPUTS
from adminsite.actions.selection import Selection
from adminsite.backends.sqlalchemy.session import AsyncSessionAdapter, SessionAdapter
from adminsite.exceptions import AdminSiteError
from adminsite.fields import (
    BaseField,
    BooleanField,
    DateField,
    DateTimeField,
    DecimalField,
    EnumField,
    Field,
    FloatField,
    IntegerField,
    RelationField,
    StringField,
    TextAreaField,
    TimeField,
    UUIDField,
)
from adminsite.fields.files import UploadField
from adminsite.text import choice_label, humanize

__all__ = [
    "ASKS_FOR",
    "ASYNC_SESSION",
    "NO_DEFAULT",
    "REQUEST",
    "SCALARS",
    "SESSION",
    "SUBJECT",
    "ActionCall",
    "Handed",
    "HandedKind",
    "Input",
    "InputGroup",
    "async_session_refused",
    "read_call",
    "type_name",
]

# What adminsite hands a parameter, rather than asking for it.
SUBJECT: Final = "subject"
REQUEST: Final = "request"
SESSION: Final = "session"
ASYNC_SESSION: Final = "async session"
HandedKind: TypeAlias = Literal["subject", "request", "session", "async session"]

# The input each plain type is asked for with. A str is a line of text, or a
# box with Input(multiline=True).
SCALARS: dict[object, type[Field[Any]]] = {
    str: StringField,
    int: IntegerField,
    float: FloatField,
    Decimal: DecimalField,
    date: DateField,
    datetime: DateTimeField,
    time: TimeField,
    UUID: UUIDField,
}

# No default, as inspect and dataclasses write it.
NO_DEFAULT: Final = inspect.Parameter.empty

ASKS_FOR = (
    "It asks for a str, int, float, Decimal, bool, date, datetime, time, UUID, "
    "Enum, Literal of strings, model, list of an Enum or of a model, UploadFile, "
    "or a dataclass of these, and hands over Request, AsyncSession and "
    "SessionAdapter."
)


@dataclass(frozen=True, kw_only=True, slots=True)
class Input:
    """How an action asks for one value, written in the parameter's annotation.

    ```python
    from typing import Annotated

    from adminsite.actions import Input

    reason: Annotated[str, Input(label="Reason", multiline=True)]
    ```
    """

    # Left empty, the parameter's name written as words.
    label: str = ""
    help_text: str = ""
    # A box of several lines, for a str.
    multiline: bool = False
    # For an UploadFile: the types a browser offers to pick, as its accept
    # attribute takes them, and the largest file taken, in bytes.
    accept: str = ""
    max_size: int | None = None
    # Whether the audit log keeps "***" instead of the value. Left as None, a
    # name such as "password" or "api_key" decides.
    secret: bool | None = None


@dataclass(frozen=True, slots=True)
class Handed:
    """A parameter adminsite fills in itself: the record, the request, a session."""

    name: str
    kind: HandedKind
    # Written before a /, so it is passed by position.
    positional: bool = False


@dataclass(frozen=True, slots=True)
class InputGroup:
    """A dataclass parameter, asked for as its fields under one heading."""

    name: str
    label: str
    kind: type[Any]
    # The dataclass's fields, each asked for as "name.field".
    fields: tuple[str, ...]

    def input_name(self, field: str) -> str:
        """The name one of its fields is asked for under."""
        return f"{self.name}.{field}"


@dataclass(frozen=True, slots=True)
class ActionCall:
    """How an action's method is called: what it is handed, and what it asks for."""

    handed: tuple[Handed, ...] = ()
    # What its typed parameters ask for, in order, a group's fields included.
    inputs: tuple[BaseField, ...] = ()
    groups: tuple[InputGroup, ...] = ()
    # The inputs that fall back to the parameter's default when left empty.
    defaulted: frozenset[str] = frozenset()

    def handed_as(self, kind: HandedKind) -> Handed | None:
        """The parameter handed this, if the method has one."""
        return next((one for one in self.handed if one.kind == kind), None)

    def arguments(
        self,
        *,
        subject: Any,
        request: Any,
        session: SessionAdapter,
        values: Mapping[str, Any],
    ) -> tuple[list[Any], dict[str, Any]]:
        """The arguments the method is called with, by position and by name.

        `values` holds what the dialog asked for, by input name; a group's
        fields become the dataclass they belong to.
        """
        positional: list[Any] = []
        named: dict[str, Any] = {}
        for one in self.handed:
            value = self._value_of(one, subject, request, session)
            if one.positional:
                positional.append(value)
            else:
                named[one.name] = value
        rest = dict(values)
        for group in self.groups:
            parts = {
                part: rest.pop(group.input_name(part), None) for part in group.fields
            }
            named[group.name] = group.kind(
                **{
                    part: value
                    for part, value in parts.items()
                    if value is not None or group.input_name(part) not in self.defaulted
                }
            )
        named.update(
            (name, value)
            for name, value in rest.items()
            if value is not None or name not in self.defaulted
        )
        return positional, named

    def _value_of(
        self, one: Handed, subject: Any, request: Any, session: SessionAdapter
    ) -> Any:
        if one.kind == SUBJECT:
            return subject
        if one.kind == REQUEST:
            return request
        if one.kind == SESSION:
            return session
        if not isinstance(session, AsyncSessionAdapter):
            raise AdminSiteError(async_session_refused(one.name))
        return session.session


def async_session_refused(name: str) -> str:
    """Why a parameter asking for an AsyncSession cannot have one."""
    return (
        f"{name} is an AsyncSession, and the admin's database is not async. "
        "Ask for a SessionAdapter, which works with both."
    )


def read_call(
    method: Callable[..., Any],
    *,
    where: str,
    on: str,
    model: type[Any],
    asked: Sequence[BaseField] = (),
) -> ActionCall:
    """Read what a method is handed and what it asks for, or say why it cannot be.

    `where` names the method in a message, such as "OrderView.ship". `asked`
    holds the inputs given with `inputs=`, whose values reach the method by
    name as they are.
    """
    reader = _Reader(where, on, model)
    try:
        hints = get_type_hints(method, include_extras=True)
    except NameError as error:
        raise reader.unreadable(error) from None
    given = {item.name for item in asked}
    takes_any = False
    parameters = inspect.signature(method).parameters
    for parameter in parameters.values():
        if parameter.kind is parameter.VAR_KEYWORD:
            takes_any = True
        elif parameter.kind is parameter.VAR_POSITIONAL:
            raise AdminSiteError(
                f"{where}: *{parameter.name} cannot be filled in. "
                "Name each parameter instead."
            )
        elif parameter.name not in given:
            reader.read(parameter, hints.get(parameter.name, Any))
    for item in asked:
        if item.name not in parameters and not takes_any:
            raise AdminSiteError(
                f"{where}: inputs asks for {item.name!r}, and the method has no "
                f"parameter by that name. Add {item.name} to its parameters."
            )
    return ActionCall(
        handed=tuple(reader.handed),
        inputs=tuple(reader.inputs),
        groups=tuple(reader.groups),
        defaulted=frozenset(reader.defaulted),
    )


class _Reader:
    """Reads a method's parameters one at a time, in order."""

    def __init__(self, where: str, on: str, model: type[Any]) -> None:
        self.where = where
        self.on = on
        self.model = model
        self.handed: list[Handed] = []
        self.inputs: list[BaseField] = []
        self.groups: list[InputGroup] = []
        self.defaulted: set[str] = set()
        # An action on the view runs on nothing in particular.
        self.subject_handed = on == ON_VIEW

    def read(self, parameter: inspect.Parameter, hint: Any) -> None:
        """Hand the parameter over, or ask for it."""
        name = parameter.name
        base, _options = _unwrapped(hint)
        positional = parameter.kind is parameter.POSITIONAL_ONLY
        by_position = parameter.kind is not parameter.KEYWORD_ONLY
        if base is Any:
            self._hand(name, self._untyped(name, by_position), positional)
            return
        kind = self._handed_kind(name, base, by_position)
        if kind is not None:
            self._hand(name, kind, positional)
            return
        if positional:
            raise AdminSiteError(
                f"{self.where}: {name} comes before a /, so its value cannot be "
                "passed by name. Move the / before it."
            )
        if name in RESERVED_INPUTS:
            raise AdminSiteError(
                f"{self.where}: an input cannot be called {name!r}, the action "
                "form already uses that name."
            )
        if _is_group(_read_type(hint)[0]):
            self._read_group(name, hint)
        else:
            self._ask(name, hint, parameter.default)

    def unreadable(self, error: NameError) -> AdminSiteError:
        """Say which name in the parameters' types could not be found."""
        return AdminSiteError(
            f"{self.where}: its parameters are typed with {error.name}, which "
            "cannot be found when the admin starts. Import it where the view is "
            "written, outside if TYPE_CHECKING."
        )

    def _hand(self, name: str, kind: HandedKind, positional: bool) -> None:
        if kind == SUBJECT:
            self.subject_handed = True
        self.handed.append(Handed(name, kind, positional))

    def _untyped(self, name: str, by_position: bool) -> HandedKind:
        """What an untyped parameter is handed: the record, then the session.

        That was the order before parameters were typed: (record, session)
        on a record, (session) on the view, (selection) on the selection.
        """
        if by_position and not self.subject_handed:
            return SUBJECT
        if by_position and self.on != ON_SELECTION and not self._has(SESSION):
            return SESSION
        raise AdminSiteError(
            f"{self.where}: give {name} a type, such as {name}: str, so the "
            "dialog knows what to ask for."
        )

    def _has(self, kind: HandedKind) -> bool:
        return any(one.kind == kind for one in self.handed)

    def _handed_kind(
        self, name: str, base: Any, by_position: bool
    ) -> HandedKind | None:
        """What a typed parameter is handed, or None when it is asked for.

        What is handed is always there, so `Request | None` is handed too.
        The record goes to the first parameter typed with the view's model,
        before the `*` or after it.
        """
        base = _optional(base)[0]
        if isinstance(base, type) and issubclass(base, HTTPConnection):
            return REQUEST
        if isinstance(base, type) and issubclass(base, SessionAdapter):
            return SESSION
        if isinstance(base, type) and issubclass(base, AsyncSession):
            return ASYNC_SESSION
        if base is Selection or get_origin(base) is Selection:
            self._check_selection(name, base)
            return SUBJECT
        if self.subject_handed:
            return None
        if self.on == ON_RECORD and (
            isinstance(base, TypeVar)
            or (isinstance(base, type) and issubclass(self.model, base))
        ):
            return SUBJECT
        if not by_position:
            return None
        if self.on == ON_SELECTION and base is self.model:
            model = self.model.__name__
            raise AdminSiteError(
                f"{self.where}: {name} is typed {model}, and an action on the "
                f"selection is handed a Selection[{model}]. Write {name}: "
                f'Selection[{model}], or on="record" to run on one record.'
            )
        return None

    def _check_selection(self, name: str, base: Any) -> None:
        if self.on != ON_SELECTION:
            raise AdminSiteError(
                f"{self.where}: {name} is typed Selection, which only an action "
                f'on the selection is handed. This one runs on="{self.on}".'
            )
        wanted = get_args(base)
        model = self.model
        if wanted and isinstance(wanted[0], type) and not issubclass(model, wanted[0]):
            raise AdminSiteError(
                f"{self.where}: {name} is typed Selection[{wanted[0].__name__}], "
                f"and the view shows {model.__name__}. Write "
                f"Selection[{model.__name__}]."
            )

    def _ask(self, name: str, hint: Any, default: Any) -> None:
        field, falls_back = self._field_for(name, hint, default)
        self.inputs.append(field)
        if falls_back:
            self.defaulted.add(name)

    def _read_group(self, name: str, hint: Any) -> None:
        """Ask for each field of a dataclass, under one heading."""
        kind, options, optional = _read_type(hint)
        if optional:
            raise AdminSiteError(
                f"{self.where}: {name} is typed {type_name(hint)}, and a group "
                "of inputs is always asked for. Let its fields be None instead."
            )
        self._check_options(name, options, kind, takes={"label"})
        try:
            hints = get_type_hints(kind, include_extras=True)
        except NameError as error:
            raise self.unreadable(error) from None
        declared = {part.name: part for part in dataclasses.fields(kind)}
        parts = []
        # What __init__ takes, which holds an InitVar that fields() leaves out.
        for part_name, parameter in inspect.signature(kind).parameters.items():
            written = f"{name}.{part_name}"
            part_hint = hints.get(part_name, Any)
            if isinstance(part_hint, dataclasses.InitVar):
                part_hint = part_hint.type
            if _is_group(_read_type(part_hint)[0]):
                raise AdminSiteError(
                    f"{self.where}: {written} is a dataclass inside a dataclass. "
                    "Ask for it with a parameter of its own."
                )
            part = declared.get(part_name)
            default = parameter.default if part is None else _default_of(part)
            self._ask(written, part_hint, default)
            parts.append(part_name)
        self.groups.append(
            InputGroup(name, options.label or humanize(name), kind, tuple(parts))
        )

    def _field_for(self, name: str, hint: Any, default: Any) -> tuple[BaseField, bool]:
        """The input a value is asked for with, and whether empty means the default.

        A value is required unless it may be None or has a default. A
        default fills the input when the dialog opens.
        """
        base, options, optional = _read_type(hint)
        has_default = default is not NO_DEFAULT
        shared: dict[str, Any] = {
            "label": options.label,
            "help_text": options.help_text,
            "secret": options.secret,
            "required": not optional and not has_default,
        }
        if has_default and default is not None:
            shared["default"] = default
        falls_back = has_default and not optional
        takes = {"label", "help_text", "secret"}

        field: BaseField
        if base is bool:
            self._check_options(name, options, base, takes=takes)
            shared["required"] = False
            field = BooleanField(name, **shared)
        elif base is str:
            self._check_options(name, options, base, takes={*takes, "multiline"})
            kind = TextAreaField if options.multiline else StringField
            field = kind(name, **shared)
        elif base in SCALARS:
            self._check_options(name, options, base, takes=takes)
            field = SCALARS[base](name, **shared)
        elif isinstance(base, type) and issubclass(base, UploadFile):
            self._check_options(
                name, options, base, takes={*takes, "accept", "max_size"}
            )
            shared.pop("default", None)
            if options.max_size is not None:
                shared["max_size"] = options.max_size
            field = UploadField(name, accept=options.accept, **shared)
        elif _is_model(base):
            self._check_options(name, options, base, takes=takes)
            shared.pop("default", None)
            field = RelationField(name, target=base, **shared)
        elif get_origin(base) is list and len(get_args(base)) == 1:
            self._check_options(name, options, base, takes=takes)
            field = self._many(name, base, get_args(base)[0], shared)
        else:
            self._check_options(name, options, base, takes=takes)
            field = self._one_of(name, base, base, shared, multiple=False)
        return field, falls_back

    def _many(
        self, name: str, hint: Any, item: Any, shared: dict[str, Any]
    ) -> BaseField:
        """A list: several records of a model, or several options."""
        if _is_model(item):
            shared.pop("default", None)
            return RelationField(name, target=item, collection=True, **shared)
        return self._one_of(name, hint, item, shared, multiple=True)

    def _one_of(
        self,
        name: str,
        hint: Any,
        kind: Any,
        shared: dict[str, Any],
        *,
        multiple: bool,
    ) -> BaseField:
        """An Enum or a Literal: one option picked, or several."""
        if isinstance(kind, type) and issubclass(kind, Enum):
            return EnumField(name, enum=kind, multiple=multiple, **shared)
        values = get_args(kind) if get_origin(kind) is Literal else ()
        if values and all(isinstance(value, str) for value in values):
            choices = [(value, choice_label(value)) for value in values]
            return EnumField(name, choices=choices, multiple=multiple, **shared)
        raise AdminSiteError(
            f"{self.where}: the parameter {name} is typed {type_name(hint)}, "
            f"which an action cannot ask for. {ASKS_FOR}"
        )

    def _check_options(
        self, name: str, options: Input, kind: Any, *, takes: set[str]
    ) -> None:
        """Refuse an Input option this kind of value does not use."""
        unused = Input()
        for option in (
            "label",
            "help_text",
            "multiline",
            "accept",
            "max_size",
            "secret",
        ):
            if option in takes:
                continue
            if getattr(options, option) != getattr(unused, option):
                raise AdminSiteError(
                    f"{self.where}: {name} is typed {type_name(kind)}, which has no "
                    f"use for Input({option}=...)."
                )


def _read_type(hint: Any) -> tuple[Any, Input, bool]:
    """The type a parameter holds, its Input, and whether it may be None.

    The Input may be written around the whole type, as in
    Annotated[str | None, Input()], or inside it, Annotated[str, Input()] | None.
    """
    kind, options = _unwrapped(hint)
    kind, optional = _optional(kind)
    if get_origin(kind) is Annotated:
        kind, options = _unwrapped(kind)
    return kind, options, optional


def _unwrapped(hint: Any) -> tuple[Any, Input]:
    """The type inside Annotated, and the Input written with it."""
    if get_origin(hint) is not Annotated:
        return hint, Input()
    kind, *extras = get_args(hint)
    found = [extra for extra in extras if isinstance(extra, Input)]
    return kind, found[0] if found else Input()


def _optional(hint: Any) -> tuple[Any, bool]:
    """The type inside `X | None`, and whether None was allowed."""
    if get_origin(hint) not in (Union, types.UnionType):
        return hint, False
    kinds = [kind for kind in get_args(hint) if kind is not type(None)]
    if len(kinds) == 1 and len(get_args(hint)) == 2:
        return kinds[0], True
    return hint, False


def _is_group(kind: Any) -> bool:
    return isinstance(kind, type) and dataclasses.is_dataclass(kind)


def _is_model(kind: Any) -> bool:
    if not isinstance(kind, type):
        return False
    return isinstance(sqlalchemy_inspect(kind, raiseerr=False), Mapper)


def _default_of(part: dataclasses.Field[Any]) -> Any:
    """A dataclass field's default, made fresh where it has a factory."""
    if part.default is not dataclasses.MISSING:
        return part.default
    if part.default_factory is not dataclasses.MISSING:
        return part.default_factory()
    return NO_DEFAULT


def type_name(hint: Any) -> str:
    """A type as code writes it, for a message: list[Supplier], str | None."""
    origin = get_origin(hint)
    if origin is Annotated:
        return type_name(get_args(hint)[0])
    if origin is Literal:
        return f"Literal[{', '.join(repr(value) for value in get_args(hint))}]"
    if origin in (Union, types.UnionType):
        return " | ".join(type_name(kind) for kind in get_args(hint))
    if origin is not None:
        written = ", ".join(type_name(kind) for kind in get_args(hint))
        return f"{type_name(origin)}[{written}]"
    if hint is type(None):
        return "None"
    return str(getattr(hint, "__name__", hint))
