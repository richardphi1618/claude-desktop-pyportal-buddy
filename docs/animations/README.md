# Buddy animations

Rendered from `device/claude_buddy.py` by `tools/render_gifs.py` (regenerate after changing animations). The font is an approximation of the PyPortal's built-in one.

## One session

| | |
|---|---|
| **Hello: session start**<br>![hello](hello.gif) | **Thinking, then the light bulb as it starts a tool**<br>![thinking](thinking.gif) |
| **Reading: Read / Grep / Glob**<br>![read](read.gif) | **Editing: Edit / Write**<br>![edit](edit.gif) |
| **Planning: TodoWrite**<br>![plan](plan.gif) | **Running: Bash, typing the real command**<br>![bash](bash.gif) |
| **Running: a long command wraps**<br>![bash_long](bash_long.gif) | **Browsing: WebFetch / WebSearch**<br>![web](web.gif) |
| **Delegating: Agent**<br>![agent](agent.gif) | **Using a tool: anything else**<br>![tool](tool.gif) |
| **Needs you!: permission prompt**<br>![permission](permission.gif) | **Your turn: idle notification**<br>![waiting](waiting.gif) |
| **All done: Stop**<br>![done](done.gif) | **Compacting: PreCompact**<br>![compact](compact.gif) |
| **Goodnight: session end**<br>![bye](bye.gif) | **Listening: Spotify playing, Claude idle**<br>![music](music.gif) |
| **Headphones on while Claude works**<br>![music_working](music_working.gif) |  |

## Multiple sessions

With two or more live sessions, the header shows one dot per session (yellow = needs you, green = done, white = your turn, blue = working, grey = idle); the bigger dot is the session on screen. Tap the screen to show the next session for 30 s. When every session is idle, they take a break at the water cooler.

| | |
|---|---|
| **Three sessions: dots show each one, the big dot is on screen**<br>![sessions](sessions.gif) | **Tapped to another session: manual for 30 s, yellow dot still waiting**<br>![tap](tap.gif) |
| **Water cooler: every session is idle**<br>![cooler](cooler.gif) |  |
