import ttkbootstrap as tb
from ttkbootstrap.constants import *
from tkinter import scrolledtext
from main import chat_with_gpt

current_theme = "dark"

THEMES = {
    "dark": {
        "bg": "#1e1e1e",
        "fg": "#ffffff",
        "user": "#4fc3f7",
        "bot": "#a5d6a7"
    },
    "light": {
        "bg": "#ffffff",
        "fg": "#000000",
        "user": "#0d47a1",
        "bot": "#1b5e20"
    }
}

def hex_to_rgb(h):
    h = h.lstrip("#")
    return tuple(int(h[i:i+2], 16) for i in (0, 2, 4))

def rgb_to_hex(rgb):
    return f"#{rgb[0]:02x}{rgb[1]:02x}{rgb[2]:02x}"

def send_message(event=None):
    user_text = entry.get().strip()
    if not user_text:
        return

    entry.delete(0, "end")

    chat_area.config(state="normal")
    chat_area.insert("end", f"You:\n{user_text}\n\n", "user")
    chat_area.config(state="disabled")
    chat_area.see("end")

    bot_reply = chat_with_gpt(user_text)

    chat_area.config(state="normal")
    chat_area.insert("end", f"Bot:\n{bot_reply}\n\n", "bot")
    chat_area.config(state="disabled")
    chat_area.see("end")

def apply_theme(theme):
    global current_theme
    current_theme = theme

    app.style.theme_use("flatly" if theme == "light" else "darkly")

    colors = THEMES[theme]
    chat_area.config(bg=colors["bg"], fg=colors["fg"])
    chat_area.tag_config("user", foreground=colors["user"])
    chat_area.tag_config("bot", foreground=colors["bot"])

def toggle_theme():
    new_theme = "light" if current_theme == "dark" else "dark"
    steps = 15

    old_bg = hex_to_rgb(THEMES[current_theme]["bg"])
    new_bg = hex_to_rgb(THEMES[new_theme]["bg"])

    def animate(step=0):
        ratio = step / steps
        bg = tuple(
            int(old_bg[i] + (new_bg[i] - old_bg[i]) * ratio)
            for i in range(3)
        )
        chat_area.config(bg=rgb_to_hex(bg))

        if step < steps:
            app.after(15, animate, step + 1)
        else:
            apply_theme(new_theme)

    animate()

def create_ios_toggle(parent):
    canvas = tb.Canvas(parent, width=70, height=36, highlightthickness=0)
    canvas.pack(side="right")

    track_color = "#444"
    canvas.create_oval(2, 2, 36, 34, fill=track_color, outline="")
    canvas.create_oval(34, 2, 68, 34, fill=track_color, outline="")
    canvas.create_rectangle(18, 2, 52, 34, fill=track_color, outline="")

    knob = canvas.create_oval(4, 4, 32, 32, fill="white", outline="")

    moon = canvas.create_oval(10, 10, 26, 26, fill="#b0bec5", outline="")
    moon_cut = canvas.create_oval(14, 8, 28, 24, fill="white", outline="")

    sun = canvas.create_oval(10, 10, 26, 26, fill="#fbc02d", outline="")
    rays = []
    for dx, dy in [(-6,0),(6,0),(0,-6),(0,6),(-4,-4),(4,4),(-4,4),(4,-4)]:
        rays.append(canvas.create_line(
            18, 18, 18+dx, 18+dy, fill="#fbc02d", width=2
        ))

    canvas.itemconfig(sun, state="hidden")
    for r in rays:
        canvas.itemconfig(r, state="hidden")

    def move_knob(target_x):
        current_x = canvas.coords(knob)[0]
        delta = (target_x - current_x) / 8

        if abs(delta) < 1:
            canvas.move(knob, target_x - current_x, 0)
            canvas.move(moon, target_x - current_x, 0)
            canvas.move(moon_cut, target_x - current_x, 0)
            canvas.move(sun, target_x - current_x, 0)
            for r in rays:
                canvas.move(r, target_x - current_x, 0)
            return

        for item in [knob, moon, moon_cut, sun] + rays:
            canvas.move(item, delta, 0)
        canvas.after(15, move_knob, target_x)

    def toggle(event=None):
        if current_theme == "dark":
            canvas.itemconfig(moon, state="hidden")
            canvas.itemconfig(moon_cut, state="hidden")
            canvas.itemconfig(sun, state="normal")
            for r in rays:
                canvas.itemconfig(r, state="normal")
            move_knob(36)
            toggle_theme()
        else:
            canvas.itemconfig(sun, state="hidden")
            for r in rays:
                canvas.itemconfig(r, state="hidden")
            canvas.itemconfig(moon, state="normal")
            canvas.itemconfig(moon_cut, state="normal")
            move_knob(4)
            toggle_theme()

    canvas.bind("<Button-1>", toggle)

app = tb.Window(themename="darkly")
app.title("AI Chatbot")
app.geometry("600x700")
app.minsize(500, 600)

top_bar = tb.Frame(app)
top_bar.pack(fill="x", padx=15, pady=10)

title = tb.Label(
    top_bar,
    text="AI Chatbot",
    font=("Helvetica", 20, "bold"),
    bootstyle=INFO
)
title.pack(side="left")

create_ios_toggle(top_bar)

chat_frame = tb.Frame(app)
chat_frame.pack(padx=15, pady=10, fill="both", expand=True)

chat_area = scrolledtext.ScrolledText(
    chat_frame,
    wrap="word",
    font=("Segoe UI", 12),
    state="disabled",
    bg=THEMES["dark"]["bg"],
    fg=THEMES["dark"]["fg"],
    insertbackground="white"
)
chat_area.pack(fill="both", expand=True)

chat_area.tag_config("user", font=("Segoe UI", 12, "bold"))
chat_area.tag_config("bot", font=("Segoe UI", 12))

input_frame = tb.Frame(app)
input_frame.pack(padx=15, pady=15, fill="x")

entry = tb.Entry(input_frame, font=("Segoe UI", 12))
entry.pack(side="left", fill="x", expand=True, padx=(0, 10))
entry.focus()

send_btn = tb.Button(
    input_frame,
    text="Send",
    bootstyle=SUCCESS,
    command=send_message
)
send_btn.pack(side="right")

entry.bind("<Return>", send_message)

app.mainloop()
