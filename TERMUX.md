# Working on this repo from Termux

Notes for cloning, editing, and pushing this repository from Android.

## Install Termux

Get it from [F-Droid](https://f-droid.org/packages/com.termux/) or the
[GitHub releases](https://github.com/termux/termux-app/releases). **Not Google
Play** — that build was deprecated in 2020 and `pkg` fails on it.

Then:

```bash
pkg update && pkg upgrade
pkg install git
```

Git is not preinstalled.

## Set your identity

```bash
git config --global user.name "tman686"
git config --global user.email "gilbertsenthomas00@gmail.com"
```

Only needed once per device. It stamps every commit you make.

## Authenticate

**This is the step that stops most people.** GitHub removed password
authentication in August 2021 — `git push` will prompt for a password and
reject your account password no matter what you type. Pick one of the two
below.

### Personal access token — easiest on a phone

Create one at **github.com/settings/tokens**. A fine-grained token scoped to
this repository with **Contents: Read and write** is enough; a classic `repo`
token reaches every repository you own. Set an expiry — 90 days is a
reasonable default.

At the push prompt:

```
Username: tman686
Password: <paste the token>
```

To avoid retyping it every push:

```bash
git config --global credential.helper store
```

That writes the token in **plain text** to `~/.git-credentials`. On a phone,
weigh that against the convenience.

> Never paste a token into a chat, a note, or a screenshot. If you do, revoke
> it at github.com/settings/tokens and generate a new one — deleting the
> message does not help.

### SSH key — better if you push often

The private key stays on the device and is never typed or pasted, which is the
bigger practical win on a phone.

```bash
pkg install openssh
ssh-keygen -t ed25519 -C "gilbertsenthomas00@gmail.com"
cat ~/.ssh/id_ed25519.pub          # add this at github.com/settings/keys
```

Then point the remote at SSH and check it:

```bash
git remote set-url origin git@github.com:tman686/helionnova.git
ssh -T git@github.com              # should greet you by username
```

## Clone and work

```bash
git clone https://github.com/tman686/helionnova.git
cd helionnova
```

The everyday loop:

```bash
nano README.md                     # 1. edit a file
git add .                          # 2. stage the changes
git status                         # 3. check what you are about to commit
git commit -m "Describe the change" # 4. commit
git push -u origin "$(git branch --show-current)"   # 5. push
```

Three things that bite:

- **`nano` is an editor, not a place to run commands.** Type the git commands
  at the shell prompt after you exit nano, not inside the file. To save and
  exit: `Ctrl+O`, `Enter`, then `Ctrl+X`. To discard: `Ctrl+X`, then `N`.
  Use the `CTRL` key on Termux's grey key row.
- **`git push` needs `-u origin <branch>` the first time** on a branch that is
  not yet tracking a remote. Plain `git push` errors with "no upstream branch".
  `git branch --show-current` prints the name to use.
- **`git commit` only records what you staged.** Edit after staging and the
  change is left out. That is what the `git status` in step 3 is for.

## Running the architecture tooling

The generators need Python and one dependency; everything else is stdlib.

```bash
pkg install python
pip install pyyaml                 # if the build fails: pkg install clang

python architecture/scaffold.py --check                        # validate
python -m unittest discover -s architecture -t architecture    # tests
```

To read the architecture on your phone, write the page somewhere you can open
it:

```bash
termux-setup-storage               # once, grants access to phone storage
python architecture/page.py --standalone -o ~/storage/downloads/architecture.html
```

Open it from Downloads. The tier diagram shows as text rather than rendering —
that needs the mermaid library, which the published version has and a local
file does not.
