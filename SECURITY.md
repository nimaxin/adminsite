# Security policy

adminsite guards the data of the applications it is mounted in, so a hole in it is a hole in them.
Please report one privately, never in a public issue.

## Supported versions

While adminsite is in alpha, only the latest release gets fixes. Upgrade to it to get them.

## Reporting a problem

Use **Report a vulnerability** on the [Security tab](https://github.com/nimaxin/adminsite/security),
which only the maintainer can read, or email nimaxin2@gmail.com.

Say which version you found it in, what someone could do with it, and how to reproduce it. A small
app that shows it is enough.

These are the kinds of problem to report:

- reading or changing records that a user's permissions or a view's scope should keep from them;
- signing in without valid credentials, or a session that outlives signing out;
- script that runs in someone else's browser, or a request forged from another site;
- SQL injection, or reading and writing files outside a field's storage.

A weakness in an application's own sign in code or settings belongs to that application, unless
adminsite's docs led it there.

## What happens next

- You get a first reply within a week.
- Once the problem is confirmed, a release fixes it as soon as the fix is ready, and a security
  advisory on GitHub describes it and thanks you by name, unless you would rather not be named.
- Please keep it private until that release is out.
