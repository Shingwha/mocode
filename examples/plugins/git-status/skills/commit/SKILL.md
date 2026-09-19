---
name: commit
description: Write a commit message for this repository, following its conventions.
---

# Commit

1. Run `git_status` and `git diff --staged` to see what is about to be committed.
2. Read the last twenty commit subjects (`git log --oneline -20`) and match their
   shape: imperative mood, scoped prefix, one line under 72 characters.
3. Write the message; if the change needs a body, say *why* rather than restating
   the diff.
4. Show the message to the user before committing anything.
