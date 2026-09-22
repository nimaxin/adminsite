# Accessibility

An admin is somebody's workplace for hours a day, so it has to work with a keyboard, a screen
reader and a zoomed in screen. This is what adminsite does for that, and what to keep in mind when
you add templates of your own.

## What the pages do

- **Keyboard.** Everything is a link, a button or a form control, reached with Tab in reading
  order. The first Tab shows a "Skip to the content" link. <kbd>Ctrl</kbd>+<kbd>K</kbd> opens the
  command palette; arrows move through it, Enter opens, Escape closes. Dialogs are native
  `<dialog>` elements, so focus stays inside them and Escape closes them.
- **Landmarks.** The sidebar is a named navigation, the page content is `<main>`, and the pager is
  a navigation of its own. The open page is marked with `aria-current="page"`.
- **Tables.** Every list has a caption naming what it lists. A sorted column says which way with
  `aria-sort`, and each row's checkbox is named after its record, such as "Select Order #12",
  rather than "Select this row" fifty times.
- **Forms.** Every input is named by its field's label, and an error or a help text is tied to it
  with `aria-describedby`; an input with an error carries `aria-invalid`. Inputs inside
  [inlines](views.md#related-records-in-the-same-form) are named by their column.
- **Messages.** "Saved" and similar news is announced politely and fades after six seconds. A
  problem is announced at once and stays until it is closed, so nobody misses it for reading
  slowly. After a search or a filter updates the list, the new count is announced.
- **Language and direction.** Every page states its language and direction, so a screen reader
  pronounces Persian as Persian. See [Translations](translations.md).
- **Contrast.** Secondary text is dimmed no further than 60 percent of the text colour, which keeps
  it readable in both themes.

The test suite checks that every input, select, textarea and button on the main pages has an
accessible name, and that the pieces above stay in place.

## In your own templates

When you write a [page](pages.md) or a [card](dashboard.md) of your own:

- Give every input a `<label for="...">` or an `aria-label`. A placeholder is not a label.
- Use `<button>` for things that do something and `<a href>` for things that go somewhere.
- Give icon-only buttons an `aria-label`.
- If a chart or picture carries information, write the same information as text, as the built in
  charts do with a table only screen readers see.
- Use `sr-only` for text that only screen readers need, and the `ms-`, `me-`, `ps-`, `pe-`,
  `start-` and `end-` classes rather than left and right, so the page also mirrors for right to
  left languages.
