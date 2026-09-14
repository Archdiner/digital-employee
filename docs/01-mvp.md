# Summer Analyst for Investcorp

One AI employee. One job: the quarterly portfolio review. One user: the CFO.

## What we build

**1. A fact store.** Numbers and facts pulled from their documents, each tagged with which file it came from and the date. The AI can only use facts from here. It can never make up a number.

**2. Skills.** Plain text files, one per task. Each starts with a one-line description of when to use it. Two kinds: general finance skills we write once and reuse everywhere; Investcorp skills, learned on the job. These never leave Investcorp.

**3. Firm notes.** One file about how Investcorp works. Names of funds and companies, their terms, and where things live ("quarterly numbers are in this folder, the file ending _FINAL, the Summary tab").

**4. A work log.** Every file the AI opened and every skill it used, written to a log. The CFO never sees this. The AI reads it so that when he asks "where did this number come from," it answers from the log instead of guessing.

**5. The document.** The review comes back as a document he opens and edits. His edits are the feedback. No review screen, no buttons, no approval step. If he stops editing, it's approved.

## How it gets trained

**Step 1.** Give it past reviews. It learns the layout, length, tone, and what tables appear.
**Step 2.** Give it old drafts with comments and tracked changes. Ask for these early.
**Step 3.** It writes a first draft. His edits on that draft are the real training.

It must say what it doesn't know. A rough draft with three honest questions beats a smooth draft that's quietly wrong.

## What the CFO sees

- It lives in Teams. He asks for a review there.
- If it hits something it doesn't know mid-task, it asks him in Teams and waits.
- It sends back the document. Nothing else.
- If he asks where a number came from, it explains in plain words.

## How it gets better

- From the first run, save every edit he makes, tagged with which skills were used.
- Do not change any skill until we have about 20 edits saved.
- A skill only changes when the same correction happens two or three times.
- The AI can suggest a new skill. It cannot create one. We approve.
- Once five reviews come back clean, freeze them as a test set. Run the test set before accepting any skill change.

## Not building

Review/approval screens, visible logs, automatic skill updates in v1, frameworks, Kubernetes, more than one role/firm/user, scheduling.

## Build order

1. Fact store, filled from their past reviews.
2. Document output. Make it look right before it's smart.
3. Skills for the review, plus the firm notes file.
4. Work log.
5. Teams, with ask-and-wait.
6. Save his edits.
7. Test set.
8. Skill updates. Last.

Show him something after step 3. Don't touch 6 to 8 until he's used 1 to 5.

## How we know it worked

- He asks for a second review on his own.
- His edits per review go down over four or more reviews.
- Check who actually edits the draft.
