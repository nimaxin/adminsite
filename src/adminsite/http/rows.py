"""The rows a form is drawn from, one for each field."""

import dataclasses
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from adminsite.fields import Field
from adminsite.i18n import gettext as _


@dataclass
class Choice:
    """One option in a select or a relation picker."""

    value: str
    label: str


@dataclass
class FormRow:
    """One field as the form template needs it."""

    path: str
    field: Field
    value: str = ""
    display: str = ""
    error: str = ""
    readonly: bool = False
    choices: Sequence[Choice] = dataclasses.field(default_factory=tuple)
    selected: Sequence[str] = dataclasses.field(default_factory=tuple)
    searchable: bool = False
    picked_label: str = ""
    # The records a searchable link already holds, each with its name, so a
    # relationship holding many of them can be edited one chip at a time.
    picked: Sequence[Choice] = dataclasses.field(default_factory=tuple)
    # The path the lookup searches by, when it differs from the input name,
    # as it does for a link inside an inline row.
    lookup_path: str = ""
    # False where an empty value is allowed in the browser even for a
    # required field, as in a blank inline row that may be left unused.
    browser_required: bool = True
    # True where leaving it empty keeps what the record has, as for a
    # password: then it is never required, and the form says so.
    keeps_when_blank: bool = False
    # Put before the path in the input's id, so two dialogs on one page that
    # both ask for a "product" do not share one.
    id_prefix: str = ""
    # Where a searchable link looks records up, when it is not the form's
    # own lookup, as for a link an action asks for.
    lookup_url: str = ""
    # A JSON column drawn as a form from its schema: the parts of that form,
    # a DocumentEntry.
    document: Any = None
    # Where the field is drawn again when the rest of the form changes, for
    # a new record whose schema follows what the form holds.
    redraw_url: str = ""

    @property
    def input_id(self) -> str:
        """The id of the input itself."""
        return f"field-{self.id_prefix}{self.path}"

    @property
    def problems(self) -> list[tuple[str, str, str]]:
        """Each message on this field, as the id it links to, a label and the text.

        A document drawn as a form has one for each part that needs another
        look, labelled with where it sits: Settings, Channels, row 2, Id.
        """
        found = [(self.input_id, self.label, self.error)] if self.error else []
        if self.document is not None:
            found.extend(self.document.problems([self.label]))
        return found

    @property
    def picked_pairs(self) -> list[dict[str, str]]:
        """The records already held, as the picker's script reads them."""
        return [{"value": one.value, "label": one.label} for one in self.picked]

    @property
    def widget(self) -> str:
        """Which template renders this field."""
        return self.field.widget

    @property
    def label(self) -> str:
        """The text above the input."""
        return self.field.label

    @property
    def required(self) -> bool:
        """Whether a value has to be given."""
        return self.field.required and not self.keeps_when_blank

    @property
    def note(self) -> str:
        """The help under the input: the field's own, or how to fill it in."""
        if self.field.help_text:
            return self.field.help_text
        if self.keeps_when_blank:
            return _("Leave it empty to keep the current one.")
        # How to fill it in means nothing where it cannot be changed.
        return "" if self.readonly else self.field.hint()


def chosen_in_order(choices: Sequence[Choice], selected: Sequence[str]) -> list[Choice]:
    """The options held, each with its label, in the order they are held.

    One that is not on offer, such as a record this user may not see, is
    left out, as the list would leave it out.
    """
    labels = {choice.value: choice.label for choice in choices}
    return [Choice(value, labels[value]) for value in selected if value in labels]
