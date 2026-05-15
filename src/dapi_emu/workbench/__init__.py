"""Discord Interaction Workbench.

A layer on top of the API emulator that lets you:
- Pick a registered slash command, fill in its arguments via a Discord-style UI
- Send button / modal / select-menu interactions
- Capture the bot's response (callback message + followups)
- Save the whole round-trip as a JSON testcase under data/testcases/
- Replay any saved case via CLI for regression testing
"""
