# Customizing the look

The interface is built on [daisyUI](https://daisyui.com) and Tailwind, drawn on the server with
Jinja, with HTMX for partial updates and Alpine for the few pieces that keep state. The CSS and
JavaScript ship inside the package, so nothing needs Node to install or run.

## Override a template

Point the admin at a folder of your own. A template found there is used instead of the one that
ships:

```python
admin = Admin(engine, views=[OrderView], template_dirs=["templates/admin"])
```

Keep the same relative path, so `templates/admin/adminsite/login.html` replaces the sign in page.

| Template | What it draws |
|---|---|
| `adminsite/base.html` | The shell: sidebar, header, messages |
| `adminsite/index.html` | The front page |
| `adminsite/list.html`, `_toolbar.html`, `_table.html` | The list, its search and filters, and the table |
| `adminsite/_filters.html`, `_filter_chips.html` | The filters drawer, and the chips of the filters in use |
| `adminsite/_bulk_actions.html` | The Actions menu for the ticked rows |
| `adminsite/_saved_tabs.html`, `_row_actions.html` | The saved views above a list, and a row's menu |
| `adminsite/detail.html`, `_history.html` | One record, and its history |
| `adminsite/form.html`, `_field.html`, `_inlines.html` | The create and edit form, and its child rows |
| `adminsite/dashboard/*.html` | The overview's cards |
| `adminsite/_icons.html`, `_values.html` | The icons, and how a status or a yes and no is drawn |
| `adminsite/_empty.html` | An empty value |
| `adminsite/widgets/*.html` | One form control each |
| `adminsite/activity.html` | The activity page |
| `adminsite/login.html` | Signing in |

## A banner on every page

`banner` puts a line of text above every page, the sign in page included, such as which copy of
the admin people are looking at:

```python
admin = Admin(engine, banner="Staging: changes here are not real.")
```

The text is escaped. Give it as `Html(...)` to include a link.

## Draw a widget your way

Each form control is a small template under `adminsite/widgets/`, named after the field's `widget`:
`input.html`, `textarea.html`, `checkbox.html`, `select.html`, `relation.html` and `readonly.html`.
To draw one your way, add a file with that name. To add a new one, give your field a new widget
name and add its template:

```python
class ColourField(StringField):
    widget = "colour"
```

```html
{# templates/admin/adminsite/widgets/colour.html #}
<input type="color" class="input w-24" id="field-{{ row.path }}"
       name="{{ row.path }}" value="{{ row.value or '#3ecf8e' }}">
```

A widget template gets `row`, which has `path`, `value`, `field`, `choices`, `error` and
`required`. A widget with no template of its own uses `input.html`.

## Colours and theme

There is a light theme and a dark one, and the admin follows the browser unless the user picks one.
To change the colours, edit the theme in `frontend/input.css` and rebuild:

```
cd frontend
npm install
npm run build
```

Beside daisyUI's own colours, the themes set CSS variables a stylesheet of yours can change:
`--subtle` for the band that holds a card's title, `--line` and `--line-strong` between rows,
`--muted` for text that steps back, `--link`, `--selected` for ticked rows, `--chart` and
`--chart-strong` for bars, and `--field`, `--field-border` and `--field-disabled` for inputs. Text
comes in four sizes, `--text-caption`, `--text-note`, `--text-label` and `--text-body`, in the
system's own typeface. Badges use six tones, as [Badge colours](fields.md#badge-colours) shows.

The build writes `src/adminsite/static/adminsite.css`. If your own templates use Tailwind or
daisyUI classes the shipped stylesheet does not have, build a stylesheet of your own from the same
input, with your template folder added as a `@source`, and serve it from your own `base.html`.
