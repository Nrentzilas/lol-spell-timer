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

Build an exe with `build_exe.bat`. Run tests with `python -m pytest tests -q`.

</details>

## Controls

| Action | Result |
|---|---|
| **Left-click** a spell | Start its cooldown |
| **Right-click** a spell | Reset it |
| **Middle-click** a spell | Copy a call-out for team chat |
| **Scroll** over a timer | Correct it by 5s |
| **Ctrl+click** a portrait | Flag Cosmic Insight |
| **Drag** the handle | Move the overlay |
| **Right-click** the handle | Pin / unpin |

Timers turn gold at 15 seconds left.

## Hotkeys

`Alt`+`1`..`5` arms the first spell of each enemy row, `Alt`+`Shift`+`1`..`5` the
second. Rows are numbered top to bottom.

These are system-wide, so `Alt`+`1` is taken from other apps while the timer runs.
Change the modifier or turn them off in the tray menu under **Hotkeys...**.

## Chat call-outs

Middle-click any spell to copy a call-out to your clipboard:

```
MissFortune Flash up at 14:32
```

It uses the game clock rather than a countdown, so the message is still correct
if you send it ten seconds later. Middle-clicking a spell that is not on cooldown
copies `MissFortune Flash is up` instead.

### Getting it into chat

League refuses clipboard text that came from outside the game, so Ctrl+V on its
own does nothing. Turn on **Type clipboard on Ctrl+V (in game)** in the tray menu
and the app catches your Ctrl+V and types the text instead.

Then a call-out is: middle-click the spell, Enter, Ctrl+V, Enter. A mouse macro
bound to Enter, Ctrl+V, Enter collapses that to two actions.

This only fires while League is the focused window, so a stray Ctrl+V in a
browser is left alone. It is off by default.

> **Worth knowing before you turn it on.** Everything else in this app is
> passive: it reads a local endpoint League provides and listens for hotkeys.
> This types into the game, which is a category of behaviour anti-cheat systems
> look for, and nobody can tell you how Vanguard treats it. Your account, your
> call.

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

> Uses a public MQTT broker, so nothing to host. The room code is a namespace, not
> a password, so do not treat it as secret. Only champion, spell and seconds
> remaining are sent.

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

The app writes `spell_timer.log` next to the exe. Check it first.

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
