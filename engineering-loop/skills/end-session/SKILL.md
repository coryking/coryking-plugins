---
name: end-session
description: >-
  Close out this conversation so the user can leave it behind with the work preserved
  and everything put away. Use for end-session requests, including questions such as
  whether this chat is ready to close and requests to get it ready. Also applies when
  the user asks to finish up, mop up, and close this particular session. Stopping for
  the day across ongoing work is a separate request.
---

# End session

The user wants to close this particular chat and trust that nothing will be left
lying around. They may have lost track of what you were doing. You have the context;
take responsibility for figuring out whether this session has accomplished its
purpose and whether you can handle the remaining closeout yourself.

Tell them promptly if they can leave you to it, then follow through: preserve the
work, mop up, and leave everything you're responsible for in a clean, settled state.
If you can't, explain concretely what remains and why.

Questions like “can I end-session?” invoke this whole intent. Don't answer with a
status report and an offer to do the cleanup if they ask again. An explicit request
for assessment only still means assessment only.

## Release their attention, then do the work

Use the assignment, the conversation, and relevant observable state to judge whether
you can close this out. The boundary is this session's contribution: it may be one
component of a larger effort, or research with no code changes at all. A clean Git
tree alone doesn't establish completion.

As soon as you can responsibly make the commitment, say so: for example, “Yes—the
research is delivered. You can leave this with me; I'm putting everything away.”
Don't make them wait for the entire cleanup before hearing that. If you need to
check first, acknowledge that briefly and check. A promise to finish cleanup isn't
a claim that cleanup has already been verified.

Finish the remaining work that reasonably belongs to closing this session. Use
judgment about a small outstanding piece of the assignment; don't turn closeout
into another major phase or an opportunity for improvements. Honor the task's
existing scope, permissions, and division of responsibility.

## Put everything where it belongs

Apply these obligations to what this session actually touched. The intended result
is that the improvements remain and the temporary mess is gone.

- **Deliver the contribution.** Complete applicable verification and the project's
  delivery process. If landing the change belongs to this session, merge it and
  close its completed issue; an open PR isn't a substitute for delivery. If another
  session owns integration, leave it the work it needs and don't close the larger
  issue or remove its branch. Research is delivered when the requested findings
  have reached their intended destination; it doesn't need a ceremonial commit.
- **Preserve the work.** Commit the session's intended repository changes, push them
  to the appropriate remote, and verify the push succeeded. Account for any results
  outside Git as well. Stage deliberately; preserve unrelated work and exclude
  credentials and disposable output. An unpublished commit isn't an off-machine
  backup. If backup or delivery fails, report the failure plainly.
- **Remove temporary scaffolding.** Clean up scratch files, downloads, test fixtures,
  temporary resources, and disposable copies created for this work. Remove the
  session's worktree and branch once their results are preserved and no continuing
  work needs them. Check what would be lost, including untracked files and commits
  added after a merge. Squash-merged history may differ even when the work landed.
  Move outside a worktree before removing it. Don't sweep other sessions' work.
- **Settle activity you started.** Account for subordinate agents and background
  commands, test servers, watchers, tunnels, and remote jobs. Collect needed results
  and stop temporary activity that has served its purpose. Keep intentional services
  running. Reap disposable conversation-conversion artifacts through the available
  conversion tools after preserving any useful results; interviewed copies may
  require explicit deletion.
- **Leave external systems settled.** Inspect the relevant machines, services,
  deployments, databases, devices, or other systems changed during the session.
  Remove temporary settings and leave the intended operating state verified to the
  extent the task requires. Committing configuration isn't proof that the running
  system is in that state. Name any outstanding physical or external verification.
- **Account for what remains.** Put necessary follow-on work or continuation context
  where the project already tracks it. Keep the record proportional and point to
  authoritative artifacts; don't create a handoff document by default or assign
  work to another agent without an actual arrangement. Recording an unfinished
  obligation doesn't make this session's assignment complete.

## If closure isn't possible

Say what remains, why it prevents closeout, and what needs to happen next. Preserve
the work and complete whatever independent cleanup is still appropriate. If a
problem appears after you said you could handle it, correct that assurance promptly.

Proceed on ordinary closeout decisions. Ask only for a decision or authority you
actually need. Substantive unfinished work warrants a concrete answer, not a false
“all clean” or an endless attempt to finish the whole project.

## Leave a receipt

Verify the resulting state and leave a short final confirmation: what was delivered
and backed up, what was cleaned up, and anything intentionally remaining or blocked.
Report observed results, including whether changes were pushed. The user can read
this later; they shouldn't have to supervise it. Don't end with another offer of
work. Use any requested harness archive/exit action only after closeout, never as a
substitute for it.
