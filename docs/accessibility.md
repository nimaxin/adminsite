# Accessibility

An admin is somebody's workplace for hours a day, so it has to work with a keyboard, a screen
reader and a zoomed in screen. This is what adminsite does for that, and what to keep in mind in
templates of your own.

## What the pages do

- **Keyboard.** Everything is a link, a button or a form control, reached with Tab in reading
  order. The first Tab shows a "Skip to the content" link. Dialogs are native `<dialog>` elements,
  so focus stays inside them and Escape closes them.
- **Landmarks.** The sidebar is a named navigation, the page content is `<main>`, and the open page
  is marked with `aria-current="page"`.
- **Tables.** Every list has a caption naming what it lists, a sorted column says which way with
  `aria-sort`, and each row's checkbox is named after its record, such as "Select Order #12".
- **Forms.** Every input is named by its field's label, and an error or a help text is tied to it
  with `aria-describedby`.
- **Messages.** News such as "Saved" is announced politely. A problem is announced at once and
  stays until it is closed.
- **Language and direction.** Every page states its language and direction, so a screen reader
  pronounces Persian as Persian. See [Translations](translations.md).
- **Contrast.** Secondary text stays readable in both themes.

The test suite checks that every control on the main pages has an accessible name, and that the
pieces above stay in place.

## In your own templates

When you write a [page](pages.md) or a [card](dashboard.md) of your own:

- Give every input a `<label for="...">` or an `aria-label`. A placeholder is not a label.
- Use `<button>` for things that do something and `<a href>` for things that go somewhere.
- Give buttons that only show an icon an `aria-label`.
- When a chart or a picture carries information, write it as text too.
- Use `sr-only` for text only screen readers need, and the `ms-`, `me-`, `ps-`, `pe-`, `start-`
  and `end-` classes rather than left and right, so the page mirrors for right to left languages.
