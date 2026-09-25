# Customizing the look

The interface is built on [daisyUI](https://daisyui.com) and Tailwind, rendered on the server with
Jinja, with HTMX for partial updates and Alpine for the few pieces that keep state. The built CSS
and JavaScript ship inside the package, so nothing needs Node to install or run.

## Overriding a template

Point the admin at a folder of your own. Any template found there is used instead of the one that
ships:

```python
admin = Admin(engine, views=[OrderView], template_dirs=["templates/admin"])
```

Keep the same relative path. To replace the sign in page, create
`templates/admin/adminsite/login.html`.

| Template | What it draws |
|---|---|
| `adminsite/base.html` | The shell: sidebar, header, messages |
| `adminsite/index.html` | The front page |
| `adminsite/list.html`, `_toolbar.html`, `_table.html` | The list, its search and filters, and the table |
| `adminsite/_saved_tabs.html`, `_row_actions.html` | The saved views above a list, and a row's menu |
| `adminsite/detail.html`, `_history.html` | One record, and its history |
| `adminsite/form.html`, `_field.html`, `_inlines.html` | The create and edit form, and its child rows |
| `adminsite/dashboard/*.html` | The overview's cards |
| `adminsite/_icons.html`, `_values.html` | The icons, and how a status or a yes and no is drawn |
| `adminsite/widgets/*.html` | One form control each |
| `adminsite/activity.html` | The activity page |
| `adminsite/login.html` | Signing in |

## A line above every page

`banner` puts one line of text above every page, the sign in page included. Use it to say which
copy of the admin people are looking at:

```python
admin = Admin(engine, banner="Staging: changes here are not real.")
```

The text is escaped. Give it as `Html(...)` to include a link.

## One widget

Each form control is its own small template under `adminsite/widgets/`, named after the field's
`widget`: `input.html`, `textarea.html`, `checkbox.html`, `select.html`, `relation.html` and
`readonly.html`. A field whose widget has no template of its own uses `input.html`.

To draw a widget your way, add a file with that name. To add a new one, give your field a new
widget name and add the template:

```python
class ColourField(StringField):
    widget = "colour"
```

```html
{# templates/admin/adminsite/widgets/colour.html #}
<input type="color" class="input w-24" id="field-{{ row.path }}"
       name="{{ row.path }}" value="{{ row.value or '#3ecf8e' }}">
```

A widget template receives `row`, which has `path`, `value`, `field`, `choices`, `error` and
`required`.

## Colours and theme

The stylesheet has two themes, a light one and a dark one, and follows the browser unless the user
picks one with the button in the sidebar. To change the colours, edit the theme in
`frontend/input.css` and rebuild:

```
cd frontend
npm install
npm run build
```

Beside daisyUI's own colours the themes set a few more, as CSS variables on the page:
`--subtle` for the surface behind cards, `--line` and `--line-strong` for the lines between rows,
`--muted` for text that steps back, `--link`, `--selected` for ticked rows, and `--chart` and
`--chart-strong` for bars. A project's own stylesheet can set any of them.

A choice such as a status is drawn as a badge in one of six tones, `tone-0` to `tone-5`: amber,
blue, green, grey, violet and rose. A field's `tones` say which value gets which, by name; without
them the choice's place in the list decides. See [Badge colours](fields.md#badge-colours).

The typeface is Geist, which ships inside the package in the two subsets European languages need.
Any other script, Persian among them, is drawn in the system's own font.

The build writes `src/adminsite/static/adminsite.css`. If your own templates use Tailwind or
daisyUI classes the shipped stylesheet does not have, build your own stylesheet from the same input
with your template folder added as a `@source`, and serve it in your overridden `base.html`.
