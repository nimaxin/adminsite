# Importing

People can add and change records from a CSV or Excel file. Nothing is written until they have seen
a preview of every row: what will be added, what will change, and what is wrong.

The examples on this page use the [example models](models.md).

Importing is off until you switch it on for a view, since it writes many records at once:

```python
class CustomerView(ModelView[Customer]):
    fields = [Customer.name, Customer.email, Customer.region, Customer.is_active]
    can_import = True
```

The list gets an **Import** button. Excel files need openpyxl:

```bash
pip install "adminsite[excel]"
```

## The file

The first row names the columns, by a field's name or its label. The import page offers an empty
file to fill in. The columns are the primary key and the fields the forms write.

- **A row with a key** changes that record, as its edit form would. Only the columns in the file
  are touched.
- **A row without a key** adds a record, as the form for a new record would. Every required field
  has to be in the file.
- **A row with a key that does not exist** is a problem, so a typo never creates a stray record.
- **A key people type**, such as a country's code, works the other way: a row with a key no record
  has adds a record with that key.

A column this user may not change has to hold what the record already has, so a file that was
exported and is imported again still goes through.

Values are checked by the same fields as the form, and a few spreadsheet habits are accepted: a
choice may be written as its label ("Shipped"), numbers may group thousands ("1,250.50"), yes or no
columns take yes, no, true, false, 1 and 0, and a list takes its values separated by commas. A link
to another record takes that record's key. CSV files may be separated by commas, semicolons or
tabs, and saved as UTF-8 or in the code page older Excel versions use.

## The preview

The preview shows the first fifty rows and every row with a problem, each problem next to its cell.
**Import** saves the good rows and skips the rest. Each row is saved through the view, as a form
would be, so permissions, hooks and the [audit log](audit.md) all apply.

Between the preview and the import, the file waits in the server's temporary folder for up to an
hour.

`Permission.IMPORT` in `allows` decides per user, and `import_limit` is the most rows one file may
hold, 10,000 unless you say.
