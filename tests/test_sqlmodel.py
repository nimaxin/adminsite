"""A SQLModel model is a SQLAlchemy model, so the admin reads it as it is.

Its columns are named through SQLModel's col(), which a type checker reads as
a column; tests/reference/library.py holds the views.
"""

from adminsite.inspector import SQLAlchemyInspector
from adminsite.query import Sort
from tests.reference.library import AuthorView, BookView
from tests.support import request_from


class TestSettings:
    def test_col_names_a_column_as_its_attribute_does(self) -> None:
        books = BookView(SQLAlchemyInspector())
        request = request_from()

        assert books._pages.list_fields(request) == (
            "id",
            "title",
            "author",
            "author.born",
            "genre",
            "price",
            "pages",
            "in_print",
            "published",
        )
        assert books._pages.search_paths(request) == ("title", "author.name")
        assert books._pages.default_sort(request) == (
            Sort("published", descending=True),
        )
        assert [item.name for item in books._pages.list_filters(request)] == [
            "genre",
            "in_print",
            "published",
        ]

    def test_an_inline_takes_col_too(self) -> None:
        authors = AuthorView(SQLAlchemyInspector())
        books = authors._inline_views["books"]

        assert books._pages.form_fields(request_from()) == ("title", "genre", "pages")
