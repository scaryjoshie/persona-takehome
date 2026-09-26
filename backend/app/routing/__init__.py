"""Routing: where a submitted event goes and what happens to it.

Glossary, since these words are ours:

- **floor**: which medium currently has the right to respond. Voice while a call is
  connected, text otherwise. Stored on the user.
- **responder**: the per-medium object that answers events. One text, one voice, per user.
- **run**: one response in progress by the floor holder. The thing that can be interrupted.
- **verb**: what to do with an event that arrives during a run: interrupt it, absorb the
  event as context, or defer the event until the run ends. START is logged when no run
  existed and the responder simply began one.
- **filter**: picks the verb. Fixed by the event kind when it can be; otherwise a decider.
- **decision**: the logged record of one routing pass, for the debug panel and tests.
"""
