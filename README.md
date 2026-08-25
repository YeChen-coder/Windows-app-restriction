Technical installation details are in [INSTALL.md](INSTALL.md). This README mainly records the author's personal motivation and design thinking.

<img width="1035" height="667" alt="image" src="https://github.com/user-attachments/assets/4dfd330d-c053-4d2d-bc88-7f75d926af29" />

The image above shows the app running. This is a Windows project built around a simple, direct, practical idea.

The original reason I built this project is that my mind is very active, I have a strong urge to express and make things, and I am not always good at stopping my hands. Whether I am writing, using ChatGPT, or doing vibe coding with Codex, it is very easy for me to get pulled in. I have ADHD, and once I enter a hyperfocus state, it can become very hard to reverse course through willpower alone. The result can be a kind of involuntary time investment.

So the core principle behind this project is:

> Do not try to make the person better. Design an environment where the person does not need to be that good.

That principle is what led to this tool.

The overall usage model is intentionally straightforward. Everything is visible on the main screen, with no hidden second-level menus:

1. Add an application:
   - If you know the program name, add it directly.
   - If you do not know the name, use `Add from running` to choose from currently running processes.
2. Manage rules:
   - Each rule can be edited independently, including normal create/read/update/delete operations and Enable / Disable switching.
   - `Daily Schedule` is supported, so specific applications can be restricted only during work hours or daytime windows.
   - `One-shot Timer` is for temporary, non-routine hyperfocus situations. You can set a one-time countdown, let it run once, and then let it disappear without creating a long-term rule.

For different kinds of programs, the timing logic can be different. For games, the application is often full-screen, and if it is open, that usually means I am playing it. But for programs like ChatGPT or Codex, I might send a prompt and then let it run in the background. Those cases should be treated more gently. Their status can become `Background`; when ChatGPT is not in the foreground, it does not appear as active and the countdown does not continue.

<img width="960" height="34" alt="image" src="https://github.com/user-attachments/assets/60b9aca6-02ac-4d64-80c2-cd649c42cea8" />

These settings are editable. For example:

<img width="403" height="367" alt="image" src="https://github.com/user-attachments/assets/8ed28db3-618c-48d7-bc28-197dfa06ae19" />

In the example above, the `time mode` is set to `foreground`, meaning the timer only continues while the application is in the foreground.

If it is changed to `runtime`, it behaves more like a game rule: once the app is open, the countdown starts.

The `action` is also configurable. It can be set to `close` or `overlay`:

<img width="212" height="73" alt="image" src="https://github.com/user-attachments/assets/5e5adf0a-4587-471d-bc4d-e4b950aef8af" />

1. `close`

   For example, for a game like Genshin Impact, once it is opened, the countdown starts. After 10 minutes, the app closes it automatically.

2. `overlay`

   For something like ChatGPT, closing the process directly is usually not acceptable, because it might be doing background work. In that case, the action can be changed to `overlay`. Instead of killing the process, the app places a blocking overlay over the interface. The overlay duration corresponds to the cooldown minutes, currently 5 minutes in my setup. After 5 minutes, the overlay disappears and the program can be used again.

Because many applications have a top title bar with minimize, maximize, and close buttons, the overlay can sometimes cover controls that should remain reachable. For that reason, there is also an `overlay top inset px` setting.

The cooldown duration is configurable as well. The general idea is: if a setting helps adapt the tool to real use, make it configurable.

When a rule is active, a small countdown window appears. It can be dragged anywhere on screen and placed wherever it is least disruptive.

<img width="1012" height="673" alt="image" src="https://github.com/user-attachments/assets/eae76400-0a14-45e2-8662-339e8016f2a6" />

## Emergency Controls

- If there is a real emergency and I genuinely need to continue using something that is being blocked, I can pause or disable all restrictions, including one-shot timers and daily rules.
- `Pause Daily Rules` pauses only the recurring daily rules.

There is also a small piece of design that I personally like.

In many situations, especially when I am not in a good state and my mind is foggy, simple app blockers are not enough. I think this is one reason why there are so many self-discipline apps on the market, but many of them are not actually that effective in practice.

So I added a "foolproofing" step. It contains a sentence that I personally like. If you want to change it, it is easy to modify in the code, or you can ask Codex to change it for you.

The requirement is:

1. The user must type the required text. It can be typed manually or entered through voice input.
2. It does not need to match perfectly. The current threshold is 66%, so approximate matching is enough.
3. Only after entering that text does the system allow rules and restrictions to be stopped.

In practice, this may not be a perfect solution. But having one more deliberate friction point can remind me of what I am doing, and that is still useful.

<img width="713" height="430" alt="image" src="https://github.com/user-attachments/assets/331d4040-430f-4b10-8814-0b4d2a64ee40" />

Overall, the feature set is simple and direct.

There are still some potential issues, mostly caused by how Windows itself works. Some applications are hosted through shared system-level frameworks and cannot be cleanly selected as independent targets. Restricting those lower-level frameworks can be dangerous. The app includes some checks to prevent certain Windows system components from being selected as restriction targets, but because I do not yet have enough real-world cases and testing coverage, I cannot guarantee absolute safety. Use it carefully.

There is also an overlay issue. If your computer uses multiple virtual desktops, not multiple monitors, an overlay may sometimes follow you when switching desktops, even though the restricted program is not on the desktop you are currently viewing. This is a bug. I acknowledge it, and because it does not currently affect my own usage much, I have not prioritized fixing it yet.
