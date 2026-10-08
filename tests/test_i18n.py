import re
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from markupsafe import Markup
from starlette.applications import Starlette
from starlette.requests import Request

from adminsite import Admin, ModelView, Permission
from adminsite.auth import PasswordAuth, hash_password
from adminsite.database import Database
from adminsite.exceptions import PermissionDeniedError
from adminsite.i18n import (
    activate,
    canonical,
    direction,
    format_number,
    gettext,
    in_sentence,
    listed,
    lower,
    native_name,
    negotiate,
    ngettext,
    plural_categories,
    plural_category,
    plural_forms,
    shipped_languages,
    upper,
)
from tests.messages import catalog, missing, plural_texts
from tests.models import Customer, Order
from tests.support import counted

PLACEHOLDER = re.compile(r"\{(\w+)\}")


@pytest.fixture(autouse=True)
def english_afterwards() -> Iterator[None]:
    yield
    activate("en")


class TestGettext:
    def test_english_is_the_text_itself(self) -> None:
        activate("en")

        assert gettext("Save") == "Save"
        assert gettext("Page {number}", number=3) == "Page 3"

    def test_persian_comes_from_the_catalog(self) -> None:
        activate("fa")

        assert gettext("Save") == "ذخیره"
        assert gettext("Page {number}", number=3) == "صفحهٔ 3"

    def test_text_without_a_translation_stays_english(self) -> None:
        activate("fa")

        assert gettext("Something nobody translated") == "Something nobody translated"

    def test_a_language_without_a_catalog_is_english(self) -> None:
        activate("xx")

        assert gettext("Save") == "Save"

    def test_a_projects_own_translations_win(self) -> None:
        activate("fa", {"fa": {"Save": "ثبت"}, "de": {"Save": "Speichern"}})

        assert gettext("Save") == "ثبت"
        assert gettext("Cancel") == "انصراف"

    def test_a_project_can_reword_the_english_too(self) -> None:
        activate("en", {"en": {"Username": "Email", "Page {number}": "Sheet {number}"}})

        assert gettext("Username") == "Email"
        assert gettext("Page {number}", number=3) == "Sheet 3"
        assert gettext("Password") == "Password"

    def test_right_to_left_languages(self) -> None:
        assert direction("fa") == "rtl"
        assert direction("ar-EG") == "rtl"
        assert direction("en") == "ltr"

    def test_shipped_languages(self) -> None:
        assert {"en", "fa"} <= set(shipped_languages())


class TestNegotiation:
    @pytest.mark.parametrize(
        ("header", "expected"),
        [
            ("fa-IR,fa;q=0.9,en;q=0.8", "fa"),
            ("de-DE,en;q=0.5", "en"),
            ("en;q=0.3,fa;q=0.7", "fa"),
            ("de", None),
            ("", None),
            ("fa;q=abc,en", "en"),
            ("fa;q=0,en;q=0.1", "en"),
            ("*", None),
        ],
    )
    def test_the_best_offered_language(self, header: str, expected: str | None) -> None:
        assert negotiate(header, ["en", "fa"]) == expected

    @pytest.mark.parametrize(
        ("header", "expected"),
        [
            ("pt-BR", "pt-BR"),
            ("pt", "pt-BR"),
            ("pt-PT", "pt-BR"),
            ("es-419", "es"),
            ("es-MX,es;q=0.9", "es"),
            ("ES-es", "es"),
        ],
    )
    def test_a_region_finds_its_language(self, header: str, expected: str) -> None:
        assert negotiate(header, ["en", "es", "pt-BR"]) == expected

    @pytest.mark.parametrize(
        ("header", "expected"),
        [
            ("zh-CN", "zh-Hans"),
            ("zh-SG", "zh-Hans"),
            ("zh", "zh-Hans"),
            ("zh-Hans-CN", "zh-Hans"),
            ("zh-TW", "zh-Hant"),
            ("zh-HK", "zh-Hant"),
            ("zh-MO", "zh-Hant"),
            ("zh-Hant-TW", "zh-Hant"),
        ],
    )
    def test_chinese_is_matched_by_its_script(self, header: str, expected: str) -> None:
        assert negotiate(header, ["en", "zh-Hans", "zh-Hant"]) == expected

    def test_one_script_is_never_offered_for_the_other(self) -> None:
        assert negotiate("zh-TW,zh;q=0.9", ["en", "zh-Hans"]) == "zh-Hans"
        assert negotiate("zh-TW", ["en", "zh-Hans"]) is None
        assert negotiate("zh-HK", ["en", "zh-Hans"]) is None
        assert negotiate("zh-CN", ["en", "zh-Hant"]) is None

    def test_tags_are_written_the_usual_way(self) -> None:
        assert canonical("pt-br") == "pt-BR"
        assert canonical("ZH_hant") == "zh-Hant"
        assert canonical("es-419") == "es-419"
        assert canonical("sr-latn-rs") == "sr-Latn-RS"


# Every language adminsite ships, besides the English its texts are written in.
TRANSLATED = [language for language in shipped_languages() if language != "en"]

# The name of the records a text is about, which a language whose grammar
# cannot fit it in may leave out.
NAMES = {"thing", "things"}


def single_numbers(language: str) -> set[str]:
    """The plural categories that hold one number only, such as Arabic's two."""
    found: dict[str, set[int]] = {}
    for number in range(1000):
        found.setdefault(plural_category(language, number), set()).add(number)
    return {category for category, numbers in found.items() if len(numbers) == 1}


class TestTheCatalog:
    @pytest.mark.parametrize("language", TRANSLATED)
    def test_it_translates_everything(self, language: str) -> None:
        assert missing(language) == []

    @pytest.mark.parametrize("language", TRANSLATED)
    def test_placeholders_survive_translation(self, language: str) -> None:
        plurals = plural_texts()
        for english, translation in catalog(language).items():
            wanted = set(PLACEHOLDER.findall(english + plurals.get(english, "")))
            forms = translation if isinstance(translation, dict) else {"": translation}
            for category, text in forms.items():
                found = set(PLACEHOLDER.findall(text))
                kept = wanted - NAMES
                # "One row" needs no number where one is the only number it is for.
                if category in single_numbers(language):
                    kept -= {"count"}
                assert found <= wanted, (english, category)
                assert kept <= found, (english, category)


class TestPlurals:
    @pytest.mark.parametrize(
        ("language", "numbers"),
        [
            ("en", {0: "other", 1: "one", 2: "other", 21: "other"}),
            ("fr", {0: "one", 1: "one", 2: "other"}),
            ("pt-BR", {0: "one", 1: "one", 2: "other"}),
            ("ru", {1: "one", 2: "few", 5: "many", 11: "many", 21: "one", 22: "few"}),
            ("ru", {12: "many", 111: "many", 104: "few"}),
            ("ar", {0: "zero", 1: "one", 2: "two", 3: "few", 10: "few", 11: "many"}),
            ("ar", {99: "many", 100: "other", 102: "other", 103: "few", 111: "many"}),
            ("ja", {0: "other", 1: "other", 2: "other"}),
            ("zh-Hant", {1: "other"}),
        ],
    )
    def test_the_category_of_a_number(
        self, language: str, numbers: dict[int, str]
    ) -> None:
        for number, category in numbers.items():
            assert plural_category(language, number) == category, number

    def test_the_categories_a_language_uses(self) -> None:
        assert plural_categories("en") == ["one", "other"]
        assert plural_categories("ru") == ["one", "few", "many"]
        assert plural_categories("ar") == ["zero", "one", "two", "few", "many", "other"]
        assert plural_categories("zh-Hans") == ["other"]

    def test_english_has_one_and_many(self) -> None:
        activate("en")

        assert ngettext("{count} row", "{count} rows", 1) == "1 row"
        assert ngettext("{count} row", "{count} rows", 0) == "0 rows"
        assert ngettext("{count} row", "{count} rows", 1234) == "1,234 rows"

    def test_each_form_comes_from_the_translation(self) -> None:
        rows = {
            "one": "{count} строка",
            "few": "{count} строки",
            "many": "{count} строк",
        }
        activate("ru", {"ru": {"{count} row": rows}})

        shown = [ngettext("{count} row", "{count} rows", n) for n in (1, 2, 5, 21)]

        assert shown == ["1 строка", "2 строки", "5 строк", "21 строка"]

    def test_one_text_serves_every_number(self) -> None:
        activate("ja", {"ja": {"{count} row": "{count} 行"}})

        assert ngettext("{count} row", "{count} rows", 1) == "1 行"
        assert ngettext("{count} row", "{count} rows", 7) == "7 行"

    def test_a_count_without_a_translation_stays_english(self) -> None:
        activate("ru")

        assert ngettext("{count} cat", "{count} cats", 5) == "5 cats"

    def test_other_values_go_in_too(self) -> None:
        activate("en")

        text = ngettext(
            "Import {count} row, skip {skipped}",
            "Import {count} rows, skip {skipped}",
            3,
            skipped=2,
        )

        assert text == "Import 3 rows, skip 2"

    def test_the_browser_gets_every_form(self) -> None:
        activate("ru", {"ru": {"{count} row": {"one": "a", "few": "b", "many": "c"}}})

        assert plural_forms("{count} row", "{count} rows") == {
            "one": "a",
            "few": "b",
            "many": "c",
            "other": "c",
        }
        activate("en")
        assert plural_forms("{count} row", "{count} rows") == {
            "one": "{count} row",
            "other": "{count} rows",
        }


class TestNumbers:
    @pytest.mark.parametrize(
        ("language", "written"),
        [
            ("en", "1,234,567"),
            ("fa", "1,234,567"),
            ("de", "1.234.567"),
            ("pt-BR", "1.234.567"),
            ("tr", "1.234.567"),
            ("es", "1.234.567"),
            ("fr", "1\u202f234\u202f567"),
            ("ru", "1\u00a0234\u00a0567"),
            ("ja", "1,234,567"),
        ],
    )
    def test_a_language_sets_its_thousands_apart(
        self, language: str, written: str
    ) -> None:
        activate(language)

        assert format_number(1_234_567) == written

    def test_spanish_leaves_four_digits_whole(self) -> None:
        activate("es")

        assert format_number(1234) == "1234"
        assert format_number(12345) == "12.345"

    def test_a_count_is_grouped_as_its_language_does(self) -> None:
        activate("de")

        assert ngettext("{count} cat", "{count} cats", 1234) == "1.234 cats"

    def test_a_count_may_read_as_a_phrase(self) -> None:
        activate("en")

        one = ngettext(
            "{count} {thing}",
            "{count} {things}",
            1,
            count="1",
            thing="order",
            things="orders",
        )
        many = ngettext(
            "{count} {thing}",
            "{count} {things}",
            1200,
            count="about 1,200",
            thing="order",
            things="orders",
        )

        assert one == "1 order"
        assert many == "about 1,200 orders"


class TestLists:
    def test_english_sets_them_apart_with_a_comma(self) -> None:
        activate("en")

        assert listed(["Ann", "Bob", "Cy"]) == "Ann, Bob, Cy"

    def test_another_language_writes_its_own(self) -> None:
        activate("ja", {"ja": {", ": "、"}})

        assert listed(["Ann", "Bob"]) == "Ann、Bob"


class TestRefusals:
    def test_a_record_page_refused_is_one_not_opened(self) -> None:
        activate("en")

        refused = PermissionDeniedError("detail", "Orders")

        assert str(refused) == "You cannot open Orders."
        assert refused.action == "detail"


class TestMarkup:
    def test_a_link_goes_in_as_it_is(self) -> None:
        activate("en")
        link = Markup('<a href="#name">Name</a>')

        text = gettext("{field}: {problem}", field=link, problem="Use <b> less.")

        assert isinstance(text, Markup)
        assert text == '<a href="#name">Name</a>: Use &lt;b&gt; less.'

    def test_plain_values_stay_plain_text(self) -> None:
        activate("en")

        text = gettext("{field}: {problem}", field="<i>Name</i>", problem="Too long.")

        assert not isinstance(text, Markup)
        assert text == "<i>Name</i>: Too long."


class TestCase:
    def test_a_label_goes_into_small_letters_inside_a_sentence(self) -> None:
        activate("en")

        assert in_sentence("Order items") == "order items"
        assert in_sentence("API keys") == "API keys"
        assert in_sentence("A") == "a"

    def test_turkish_has_a_dotted_and_a_dotless_i(self) -> None:
        activate("tr")

        assert in_sentence("İndirimler") == "indirimler"
        assert in_sentence("Iade Talepleri") == "ıade talepleri"
        assert in_sentence("IBAN") == "IBAN"
        assert lower("İSTANBUL") == "istanbul"
        assert upper("istanbul ılık") == "İSTANBUL ILIK"

    def test_german_keeps_the_capitals_of_its_nouns(self) -> None:
        activate("de")

        assert in_sentence("Bestellungen") == "Bestellungen"

    def test_other_languages_follow_the_usual_rules(self) -> None:
        activate("fr")

        assert in_sentence("Commandes") == "commandes"
        assert upper("istanbul") == "ISTANBUL"


class OrderView(ModelView[Order]):
    fields = ["id", "status", "total"]
    searchable_fields = ("customer.name",)


class LockedView(ModelView[Customer]):
    async def allows(
        self, action: Permission | str, *, request: Request, record: Any = None
    ) -> bool:
        return action != Permission.CREATE


def serve(admin: Admin) -> httpx.AsyncClient:
    app = Starlette()
    app.mount("/admin", admin)
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    )


@pytest.fixture
async def persian(database: Database) -> AsyncIterator[httpx.AsyncClient]:
    admin = Admin(
        database,
        title="فروشگاه",
        views=[OrderView, LockedView],
        language="fa",
        secret_key="for-the-session",
    )
    async with serve(admin) as client:
        yield client


class TestAPersianAdmin:
    async def test_the_page_is_mirrored(self, persian: httpx.AsyncClient) -> None:
        page = await persian.get("/admin/orders")

        assert '<html lang="fa" dir="rtl">' in page.text
        assert "ویرایش" in page.text
        assert "جست‌وجو در orders" in page.text
        assert "1 تا 7 از 7" in await counted(persian, "/admin/orders")

    async def test_messages_are_translated(self, persian: httpx.AsyncClient) -> None:
        form = await persian.get("/admin/orders/1/edit")
        token = re.search(r'name="_csrf" value="([^"]+)"', form.text)
        assert token is not None

        page = await persian.post(
            "/admin/orders/1/edit",
            data={"_csrf": token.group(1), "total": "abc", "customer": "1"},
        )

        assert "یک مبلغ وارد کنید، مثلاً 12.50." in page.text

    async def test_refusals_are_translated(self, persian: httpx.AsyncClient) -> None:
        page = await persian.get("/admin/customers/new")

        assert page.status_code == 403
        assert "دسترسی ندارید" in page.text
        assert "اجازهٔ افزودن Customers را ندارید." in page.text

    async def test_a_single_language_admin_has_no_menu(
        self, persian: httpx.AsyncClient
    ) -> None:
        page = await persian.get("/admin/")

        assert 'name="language"' not in page.text


@pytest.fixture
async def both(database: Database) -> AsyncIterator[httpx.AsyncClient]:
    admin = Admin(
        database,
        views=[OrderView],
        languages=["fa"],
        secret_key="for-the-session",
    )
    async with serve(admin) as client:
        yield client


class TestRewordingTheEnglish:
    async def test_the_sign_in_page_says_what_the_project_calls_it(
        self, database: Database
    ) -> None:
        admin = Admin(
            database,
            views=[OrderView],
            auth=PasswordAuth({"nima": hash_password("letmein")}),
            secret_key="for-the-session",
            translations={"en": {"Username": "Email"}},
        )
        async with serve(admin) as client:
            page = await client.get("/admin/login")

        assert re.search(r'for="username">Email</label>', page.text)
        assert "Username" not in page.text


class TestSwitchingLanguages:
    async def test_the_browser_language_is_used(self, both: httpx.AsyncClient) -> None:
        english = await both.get("/admin/")
        persian = await both.get("/admin/", headers={"Accept-Language": "fa-IR,fa"})

        assert '<html lang="en" dir="ltr">' in english.text
        assert '<html lang="fa" dir="rtl">' in persian.text

    async def test_the_menu_switches_and_remembers(
        self, both: httpx.AsyncClient
    ) -> None:
        home = await both.get("/admin/")
        token = re.search(r'name="_csrf" value="([^"]+)"', home.text)
        assert token is not None
        assert re.search(
            r'form="language-form" name="language" value="fa" lang="fa"\s*>\s*'
            r'<span class="flex-1">فارسی</span>',
            home.text,
        )

        answer = await both.post(
            "/admin/-/language",
            data={"_csrf": token.group(1), "language": "fa", "next": "/admin/orders"},
        )
        after = await both.get("/admin/orders")

        assert answer.status_code == 303
        assert answer.headers["location"] == "/admin/orders"
        assert '<html lang="fa" dir="rtl">' in after.text

    async def test_the_menu_only_goes_back_inside_the_site(
        self, both: httpx.AsyncClient
    ) -> None:
        home = await both.get("/admin/")
        token = re.search(r'name="_csrf" value="([^"]+)"', home.text)
        assert token is not None

        answer = await both.post(
            "/admin/-/language",
            data={
                "_csrf": token.group(1),
                "language": "fa",
                "next": "//evil.example/steal",
            },
        )

        assert answer.headers["location"] == "/admin/"

    async def test_a_language_not_offered_is_ignored(
        self, both: httpx.AsyncClient
    ) -> None:
        home = await both.get("/admin/")
        token = re.search(r'name="_csrf" value="([^"]+)"', home.text)
        assert token is not None

        answer = await both.post(
            "/admin/-/language",
            data={"_csrf": token.group(1), "language": "de", "next": "/admin/"},
        )

        assert "adminsite_language" not in answer.headers.get("set-cookie", "")


class TestCookiesOfTheSameName:
    """Another app on the same host may have set a cookie by the same name."""

    async def test_the_admins_own_language_wins(self, both: httpx.AsyncClient) -> None:
        # A browser sends the cookie set for the longer path, /admin/, first.
        cookies = "adminsite_language=fa; adminsite_language=en"

        page = await both.get("/admin/", headers={"Cookie": cookies})

        assert '<html lang="fa" dir="rtl">' in page.text

    async def test_a_cookie_set_elsewhere_alone_still_counts(
        self, both: httpx.AsyncClient
    ) -> None:
        page = await both.get("/admin/", headers={"Cookie": "adminsite_language=fa"})

        assert '<html lang="fa" dir="rtl">' in page.text


STYLESHEET = Path(__file__).parent.parent / "src" / "adminsite" / "static"


# Each language adminsite ships: what the menu calls it, browsers that get
# it, browsers that never do, and a text its list of orders shows.
SHIPPED: dict[str, tuple[str, list[str], list[str], str]] = {
    "ru": ("Русский", ["ru", "ru-RU"], ["uk-UA", "be"], "Поиск: orders"),
    "es": (
        "Español",
        ["es", "es-ES", "es-MX", "es-419"],
        ["pt-BR", "ca-ES"],
        "Buscar orders",
    ),
    "pt-BR": (
        "Português (Brasil)",
        ["pt-BR", "pt", "pt-PT"],
        ["es-ES", "gl-ES"],
        "Pesquisar orders",
    ),
    "ja": ("日本語", ["ja", "ja-JP"], ["zh-CN", "ko-KR"], "ordersを検索"),
    "de": (
        "Deutsch",
        ["de", "de-DE", "de-AT", "de-CH"],
        ["nl-NL", "da-DK"],
        "Orders durchsuchen",
    ),
    "fr": (
        "Français",
        ["fr", "fr-FR", "fr-CA", "fr-BE"],
        ["it-IT", "ca-ES"],
        "Rechercher\xa0: orders",
    ),
    "tr": ("Türkçe", ["tr", "tr-TR"], ["az-AZ"], "Ara: orders"),
    "ar": (
        "العربية",
        ["ar", "ar-SA", "ar-EG", "ar-AE"],
        ["fa-IR", "he-IL"],
        "البحث في orders",
    ),
}


class TestShippedLanguages:
    @pytest.mark.parametrize("code", SHIPPED)
    def test_the_menu_names_it(self, code: str) -> None:
        assert code in shipped_languages()
        assert native_name(code) == SHIPPED[code][0]

    @pytest.mark.parametrize(
        ("code", "header"),
        [(code, header) for code, row in SHIPPED.items() for header in row[1]],
    )
    def test_a_browser_set_to_it_gets_it(self, code: str, header: str) -> None:
        assert negotiate(f"{header},en;q=0.5", ["en", code]) == code

    @pytest.mark.parametrize(
        ("code", "header"),
        [(code, header) for code, row in SHIPPED.items() for header in row[2]],
    )
    def test_a_browser_set_to_another_never_does(self, code: str, header: str) -> None:
        assert negotiate(header, ["en", code]) is None

    @pytest.mark.parametrize("code", SHIPPED)
    async def test_a_page_speaks_it(self, database: Database, code: str) -> None:
        admin = Admin(database, views=[OrderView], language=code)
        async with serve(admin) as client:
            page = await client.get("/admin/orders")

        assert f'<html lang="{code}" dir="{direction(code)}">' in page.text
        assert SHIPPED[code][3] in page.text


class TestRussian:
    @pytest.mark.parametrize(
        ("number", "rows"),
        [(1, "1 строка"), (2, "2 строки"), (5, "5 строк"), (21, "21 строка")],
    )
    def test_a_count_reads_right_for_any_number(self, number: int, rows: str) -> None:
        activate("ru")

        assert ngettext("{count} row", "{count} rows", number) == rows

    def test_its_letters_are_drawn_in_geist(self) -> None:
        css = (STYLESHEET / "adminsite.css").read_text(encoding="utf-8")

        assert "fonts/geist-cyrillic.woff2" in css
        assert "U+400-45F" in css
        assert (STYLESHEET / "fonts" / "geist-cyrillic.woff2").exists()


class TestJapanese:
    def test_its_kanji_start_from_fonts_made_for_it(self) -> None:
        css = (STYLESHEET / "adminsite.css").read_text(encoding="utf-8")

        rule = css[css.index(":lang(ja){") :]
        rule = rule[: rule.index("}")]
        assert '"Hiragino Sans"' in rule
        assert '"Yu Gothic UI"' in rule
        assert rule.index("Geist") < rule.index("Hiragino")
        assert "[lang]{font-family:var(--font-sans)}" in css


class TestGerman:
    def test_the_shortcut_names_the_key_german_keyboards_print(self) -> None:
        activate("de")

        assert gettext("Ctrl K") == "Strg K"

    def test_a_long_word_breaks_with_a_hyphen(self) -> None:
        css = (STYLESHEET / "adminsite.css").read_text(encoding="utf-8")

        rule = css[css.index(":lang(de){") :]
        assert "hyphens:auto" in rule[: rule.index("}")]

    def test_a_label_keeps_the_capitals_of_its_nouns(self) -> None:
        activate("de")

        assert gettext("Search {things}", things=in_sentence("Bestellungen")) == (
            "Bestellungen durchsuchen"
        )


class TestFrench:
    def test_a_label_and_its_value_are_set_apart_as_french_does(self) -> None:
        activate("fr")

        said = gettext("{field}: {problem}", field="Nom", problem="Saisissez du texte.")

        assert said == "Nom\N{NO-BREAK SPACE}: Saisissez du texte."

    def test_no_mark_can_start_a_line(self) -> None:
        for english, translation in catalog("fr").items():
            forms = (
                translation.values() if isinstance(translation, dict) else [translation]
            )
            for text in forms:
                for found in re.finditer(r"[:;?!]", text):
                    before = text[found.start() - 1] if found.start() else ""
                    after = text[found.end() : found.end() + 1]
                    # A time such as 14:30 or an address such as https://.
                    if found.group() == ":" and (after.isdigit() or after == "/"):
                        continue
                    assert before in (
                        "\N{NO-BREAK SPACE}",
                        "\N{NARROW NO-BREAK SPACE}",
                    ), (english, text)


class Discounts(ModelView[Order]):
    """A view named in Turkish, with the capital dotted İ."""

    name = "discounts"
    label = "İndirim"
    label_plural = "İndirimler"
    fields = ["id", "total"]
    searchable_fields = ("id",)


class TestTurkish:
    async def test_a_view_called_indirimler_reads_indirimler_in_a_sentence(
        self, database: Database
    ) -> None:
        admin = Admin(database, views=[Discounts], language="tr")
        async with serve(admin) as client:
            page = await client.get("/admin/discounts")

        assert "Ara: indirimler" in page.text
        assert "i̇ndirimler" not in page.text

    def test_its_capitals_follow_its_dotted_and_dotless_i(self) -> None:
        activate("tr")

        assert lower("HAYIR") == "hayır"
        assert upper("evet") == "EVET"
        assert upper("istanbul") == "İSTANBUL"


class TestArabic:
    @pytest.mark.parametrize(
        ("number", "rows"),
        [
            (0, "0 صف"),
            (1, "صف واحد"),
            (2, "صفان"),
            (3, "3 صفوف"),
            (11, "11 صفًا"),
            (100, "100 صف"),
        ],
    )
    def test_a_count_reads_right_for_any_number(self, number: int, rows: str) -> None:
        activate("ar")

        assert ngettext("{count} row", "{count} rows", number) == rows

    def test_its_pages_read_right_to_left(self) -> None:
        assert direction("ar") == "rtl"
