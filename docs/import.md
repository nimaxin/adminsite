# Importing

People can add and change records from a CSV or Excel file. Nothing is written until they have seen
a preview of every row: what will be added, what will change, and what is wrong.

Importing is off until you switch it on for a view, since it writes many records at once:

```python
class CustomerView(ModelView, model=Customer):
    form_fields = ("name", "email", "region", "is_active")
    can_import = True
```

The list gets an **Import** button. Excel files need openpyxl:

```bash
pip install "adminsite[excel]"
```

## The file

The first row names the columns. A header is a field's name or its label, in any case, so `email`,
`Email` and `E-mail address` (if that is the label) all work. Columns that match nothing are listed
and left out. The import page lists the columns and offers an empty file to fill in.

The columns are the primary key and the form's fields. Read only fields, file fields and links to
many records are left out.

- **A row with a key** that exists changes that record. Only the columns in the file are touched.
- **A row without a key** adds a record. Every required field has to be in the file.
- **A row with a key that does not exist** is a problem, not a new record, so a typo never
  creates a stray record.

Values are checked by the same fields as the form, and a few spreadsheet habits are accepted: a
choice may be written as its label ("Shipped"), numbers may group thousands ("1,250.50"), yes or no
columns take yes, no, true, false, 1 and 0, and a list takes its values separated by commas. A link
to another record takes that record's key.

CSV files may be separated by commas, semicolons or tabs, and saved as UTF-8 or in the Windows code
page older Excel versions use.

## The preview

The preview shows the first fifty rows and every row with a problem, each problem next to its cell,
with counts of what is new, what changes and what is wrong. **Import** saves the good rows and skips
the rest.

Each row is saved through the view like a form, so permissions, hooks and the
[audit log](audit.md) all apply. A hook that refuses a row stops only that row, and the message
after the import says which rows were refused and why.

Between the preview and the import the file waits in the server's temporary folder for up to an
hour. It belongs to the person who uploaded it and can be imported once.

| Setting | What it does |
|---|---|
| `can_import` | Switches importing on. `Permission.IMPORT` in `allows` decides per user. |
| `import_limit` | The most rows one file may hold. 10,000 unless you say. |
