# Spell Timer

A Windows overlay that tracks enemy summoner spell cooldowns for League of Legends.

![Screenshot](screenshot/screenshot.png)

Forked from **[lovelybbq/lol-spell-timer](https://github.com/lovelybbq/lol-spell-timer)**.
The original overlay is their work. This fork adds duo sync, global hotkeys and
rune detection.

## What it does

Click an enemy's summoner spell when they use it, and a countdown runs until it is
back up. The enemy team is read from the running League client, so there is nothing
to set up per match. Start a game and five rows appear.

- **Duo sync.** Share timers with a friend over a room code.
- **Global hotkeys.** Arm a timer from the keyboard instead of clicking.
- **Chat call-outs.** Middle-click a spell to send a timer to team chat.
- **Haste handled.** Lucidity boots and Cosmic Insight are accounted for, including on timers already counting down.
- **Scroll to correct.** Nudge a running timer by 5s when you clicked late.
- **Sizeable.** Pick an overlay size in the tray menu if 4K makes it tiny.

## Install

Download `Spell Timer.exe` from the [Releases page](https://github.com/Nrentzilas/lol-spell-timer/releases)
and run it. No installer, no Python. The app lives in your system tray.

Windows SmartScreen will warn about an unsigned app. Choose **More info > Run anyway**.

<details>
<summary>Run from source instead</summary>

Requires Python 3.10+ on Windows.

```cmd
git clone https://github.com/Nrentzilas/lol-spell-timer.git
cd lol-spell-timer
setup.bat
python download_assets.py
python main.py
```

Build an exe with `build_exe.bat`. Run tests with `python -m pytest tests -q`
(they also run on every push, and `build_exe.bat` refuses to build if they fail).

The code is split so the parts worth testing have no UI attached:

| | |
|---|---|
| `main.py` | wiring, and the two poll loops |
| `cooldown.py` | the countdown and haste arithmetic |
| `widgets.py` | the portrait and spell-timer canvases |
| `gamedata.py` | the live client endpoint, DDragon, the poller |
| `sync.py` | duo sync over MQTT |
| `runes.py` | optional Cosmic Insight lookup |
| `hotkeys.py` / `clipboardtyper.py` | the two Win32 input paths |
| `settings.py` / `tray.py` / `dialogs.py` / `win32util.py` | configuration and window plumbing |

</details>

## Controls

| Action | Result |
|---|---|
| **Left-click** a spell | Start its cooldown |
| **Right-click** a spell | Reset it |
| **Middle-click** a spell | Send a call-out to team chat (or just copy it) |
| **Scroll** over a timer | Correct it by 5s |
| **Ctrl+click** a portrait | Flag Cosmic Insight |
| **Drag** the handle | Move the overlay |
| **Right-click** the handle | Pin / unpin |

Timers turn gold at 15 seconds left.

Too small on a big screen? **Overlay size** in the tray menu goes up to 2x, and
running timers survive the change.

Not in a game and want to place it first? Turn on **Show demo rows** in the tray
menu, drag it where you want it, and turn it off again.

## Hotkeys

`Alt`+`1`..`5` arms the first spell of each enemy row, `Alt`+`Shift`+`1`..`5` the
second. Rows are numbered top to bottom.

These are system-wide, so `Alt`+`1` is taken from other apps while the timer runs.
Change the modifier or turn them off in the tray menu under **Hotkeys...**.

## Chat call-outs

Middle-click any spell for a call-out:

```
MissFortune Flash up at 14:32     more than a minute away
MissFortune Flash up in 47s       under a minute
MissFortune Flash is up           not on cooldown
```

Long waits are given as a game clock time, so the message is still correct if you
send it ten seconds later. Under a minute it switches to a countdown, because at
that range what your team needs to know is whether to go now, not to work out the
difference from the clock themselves.

### Getting it into chat

League refuses clipboard text that came from outside the game, so Ctrl+V on its
own does nothing. The app types the text out character by character instead.

Turn on **Send call-outs to chat (middle-click)** in the tray menu and that is
the whole call-out: middle-click the spell, and the message is in team chat. The
app opens chat, types the line and sends it. No macro software, no programmable
mouse, nothing to bind -- a wheel click on any mouse does it.

The same toggle adds a keyboard route for anyone who would rather not middle-click:

| Keys in game | What happens |
|---|---|
| **Ctrl+Shift+V** | Sends whatever is on the clipboard to team chat |
| **Ctrl+V** | Types the clipboard where the cursor is, without sending<br>(separate toggle: **Type clipboard on Ctrl+V**) |

Both only fire while League is the focused window, so a stray Ctrl+V in a browser
is left alone, and a middle-click while you are alt-tabbed copies without typing
anything. Messages are flattened to one line, and anything starting with `/` is
refused so a stray clipboard cannot run a chat command.

Both are off by default.

> **Worth knowing before you turn these on.** Everything else in this app is
> passive: it reads a local endpoint League provides and listens for hotkeys.
> These type into the game and send chat messages on a single click, which is a
> category of behaviour anti-cheat systems look for, and nobody can tell you how
> Vanguard treats it. Your account, your call.

The technique is from **[Zeunig/lol_clipboard](https://github.com/Zeunig/lol_clipboard)**
(MIT), reimplemented here so this stays a single executable. If you would rather
run it as a separate tool, theirs does the same job and works alongside any
overlay.

## Duo sync

Share timers with a friend. Both of you open the tray menu, choose
**Duo Sync / Room Code...**, and type the same code. The drag handle turns teal
once connected.

Clicks, resets and Cosmic Insight flags all carry across. Join mid-game and you
pull down whatever timers are already running. Leave the code empty to turn it off.

> Uses a public MQTT broker, so nothing to host. The room code is hashed into
> the channel name rather than used as-is, so picking `duo` does not put you in
> with everyone else who picked `duo`. It is still not a password -- anyone who
> guesses your code derives the same channel -- and the default broker speaks
> plain MQTT, so treat the traffic as public. Only champion, spell and seconds
> remaining are sent.
>
> To encrypt it, point `broker` and `broker_port` in `config.json` at a broker
> that does TLS; port 8883 turns it on. `broker.hivemq.com` does not offer TLS.
>
> Both of you need 1.3.0 or newer. Older versions use a different channel and
> will not see you.

## Cosmic Insight

The League API does not expose enemy minor runes, so **Ctrl+click a portrait** to
flag the rune by hand. A purple `CI` badge appears and any running timer is
corrected on the spot.

Optionally, add a [Riot API key](https://developer.riotgames.com) in the tray menu
and the app detects it automatically at match start. Note that free keys expire
every 24 hours and only work in matchmade games.

## Set League to Borderless

**The overlay cannot draw over League in Fullscreen.** In that mode League marks
its own window topmost, and Windows will not let an ordinary app draw above it.
Getting into the z-band above a fullscreen game needs uiAccess, which requires a
code-signed binary installed in a trusted location, so no overlay of this kind
can do it. Blitz and friends have the same limit.

Set League to **Borderless** in Settings > Video and everything works. The app
detects Fullscreen and tells you rather than leaving you staring at an empty
screen.

## Something broken?

The app writes `spell_timer.log` next to the exe -- **Open log file** in the
tray menu gets you there. Check it first. The first line records the version,
which is the thing to quote in a bug report.

**No overlay during a game.** Almost always Fullscreen. See above.

If the overlay vanishes it has probably restored onto a monitor you unplugged. Use
**Reset Position** in the tray menu.

## License

The original project is published without a license, so the upstream code remains
under copyright by [lovelybbq](https://github.com/lovelybbq). This fork is
distributed on the same terms.

The Ctrl+V typing technique is reimplemented from
[Zeunig/lol_clipboard](https://github.com/Zeunig/lol_clipboard), MIT licensed.

Not endorsed by Riot Games. League of Legends is a trademark of Riot Games, Inc.
