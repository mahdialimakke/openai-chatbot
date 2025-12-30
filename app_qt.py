import sys
import json
import re
from pathlib import Path
from datetime import datetime, timedelta, timezone

from mevzuat_kb import MevzuatKB

import requests
from bs4 import BeautifulSoup
from unstructured.partition.auto import partition

from PySide6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout,
    QLineEdit, QPushButton, QLabel,
    QFileDialog, QDialog, QFormLayout, QDialogButtonBox,
    QMessageBox, QListWidget, QListWidgetItem,
    QFrame, QScrollArea, QProgressBar
)
from PySide6.QtCore import (
    Qt, QRectF, Property, QPropertyAnimation, Signal, QThread, QSize, QTimer, QUrl
)
from PySide6.QtGui import QPainter, QColor, QIcon, QPixmap, QDesktopServices

from main import chat_with_gpt


BASE_DIR = Path(__file__).resolve().parent
USERS_FILE = BASE_DIR / "users.json"
HISTORY_DIR = BASE_DIR / "history"
HISTORY_DIR.mkdir(exist_ok=True)
KB = MevzuatKB(BASE_DIR)


def _find_first_url(text: str) -> str | None:
    if not text:
        return None
    m = re.search(r"https?://[^\s]+", text)
    return m.group(0).rstrip(").,]}>\"'") if m else None


def _extract_text_from_html(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "nav", "footer", "header", "aside"]):
        tag.decompose()
    text = soup.get_text(separator="\n")
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    return "\n".join(lines)


def fetch_url_text(url: str, max_chars: int = 40_000) -> str:
    headers = {"User-Agent": "Mozilla/5.0"}
    r = requests.get(url, headers=headers, timeout=25)
    r.raise_for_status()

    content_type = (r.headers.get("content-type") or "").lower()

    if "pdf" in content_type or url.lower().endswith(".pdf"):
        import fitz
        doc = fitz.open(stream=r.content, filetype="pdf")
        text = "\n".join(page.get_text() for page in doc)
        doc.close()
    else:
        text = _extract_text_from_html(r.text)

    return text[:max_chars] if len(text) > max_chars else text


def extract_text_with_unstructured(path: str) -> str:
    elements = partition(filename=path)
    return "\n".join(el.text for el in elements if getattr(el, "text", None))


def load_users():
    if USERS_FILE.exists():
        try:
            return json.loads(USERS_FILE.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def save_users(users):
    USERS_FILE.write_text(json.dumps(users, indent=2), encoding="utf-8")


def history_file_for_user(username: str) -> Path:
    safe_name = username.replace(" ", "_")
    return HISTORY_DIR / f"history_{safe_name}.json"


def load_history(username: str, is_guest: bool):
    f = history_file_for_user(username)
    if not f.exists():
        return []

    try:
        data = json.loads(f.read_text(encoding="utf-8"))
    except Exception:
        return []

    if not data:
        return []

    if isinstance(data, list) and isinstance(data[0], dict) and "messages" not in data[0]:
        chat = {"id": datetime.now(timezone.utc).isoformat(), "summary": "", "messages": data}
        data = [chat]

    if is_guest:
        cutoff = datetime.now(timezone.utc) - timedelta(days=3)
        cleaned = []
        for chat in data:
            try:
                ts = datetime.fromisoformat(chat["id"])
            except Exception:
                continue
            if ts >= cutoff:
                cleaned.append(chat)
        data = cleaned
        f.write_text(json.dumps(data, indent=2), encoding="utf-8")

    return data


def save_history(username: str, chats):
    f = history_file_for_user(username)
    f.write_text(json.dumps(chats, indent=2), encoding="utf-8")


class ToggleSwitch(QWidget):
    toggled = Signal(bool)

    def __init__(self, checked=True, parent=None):
        super().__init__(parent)
        self.setFixedSize(56, 30)
        self._checked = checked
        self._offset = 26 if checked else 2
        self._anim = QPropertyAnimation(self, b"offset", self)
        self._anim.setDuration(180)

    def mousePressEvent(self, event):
        self.setChecked(not self._checked)

    def setChecked(self, checked):
        if self._checked == checked:
            return
        self._checked = checked
        self._anim.stop()
        self._anim.setEndValue(26 if checked else 2)
        self._anim.start()
        self.toggled.emit(self._checked)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        bg = QColor("#2C2C2E") if self._checked else QColor("#D1D1D6")
        painter.setBrush(bg)
        painter.setPen(Qt.NoPen)
        painter.drawRoundedRect(QRectF(0, 0, 56, 30), 15, 15)

        knob_rect = QRectF(self._offset, 2, 26, 26)
        painter.setBrush(QColor("white"))
        painter.drawEllipse(knob_rect)

        painter.setPen(Qt.black if not self._checked else Qt.white)
        icon = "🌙" if self._checked else "☀️"
        painter.drawText(knob_rect, Qt.AlignCenter, icon)

    def getOffset(self):
        return self._offset

    def setOffset(self, value):
        self._offset = value
        self.update()

    offset = Property(float, getOffset, setOffset)


class LoginDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Login / Sign Up")
        self.username = None
        self.is_guest = False

        layout = QVBoxLayout(self)

        form = QFormLayout()
        self.username_edit = QLineEdit()
        self.password_edit = QLineEdit()
        self.password_edit.setEchoMode(QLineEdit.Password)
        form.addRow("Username:", self.username_edit)
        form.addRow("Password:", self.password_edit)
        layout.addLayout(form)

        btn_box = QDialogButtonBox()
        self.login_btn = btn_box.addButton("Login", QDialogButtonBox.AcceptRole)
        self.signup_btn = btn_box.addButton("Sign Up", QDialogButtonBox.ActionRole)
        self.guest_btn = btn_box.addButton("Continue as Guest", QDialogButtonBox.DestructiveRole)
        layout.addWidget(btn_box)

        self.login_btn.clicked.connect(self.handle_login)
        self.signup_btn.clicked.connect(self.handle_signup)
        self.guest_btn.clicked.connect(self.handle_guest)

        self.users = load_users()

    def handle_login(self):
        u = self.username_edit.text().strip()
        p = self.password_edit.text().strip()
        if not u or not p:
            QMessageBox.warning(self, "Error", "Enter username and password.")
            return
        if u not in self.users or self.users[u] != p:
            QMessageBox.warning(self, "Error", "Invalid username or password.")
            return
        self.username = u
        self.is_guest = False
        self.accept()

    def handle_signup(self):
        u = self.username_edit.text().strip()
        p = self.password_edit.text().strip()
        if not u or not p:
            QMessageBox.warning(self, "Error", "Enter username and password.")
            return
        if u in self.users:
            QMessageBox.warning(self, "Error", "Username already exists.")
            return
        self.users[u] = p
        save_users(self.users)
        QMessageBox.information(self, "Success", "Account created. You are now logged in.")
        self.username = u
        self.is_guest = False
        self.accept()

    def handle_guest(self):
        self.username = "guest"
        self.is_guest = True
        self.accept()

class KBInitWorker(QThread):
    status = Signal(str)
    def run(self):
        try:
            self.status.emit("Indexing EMU mevzuat...")
            KB.ensure_index(refresh_days=14)
            self.status.emit("EMU mevzuat index ready.")
        except Exception as e:
            self.status.emit(f"EMU mevzuat index failed: {e}")

class ChatWorker(QThread):
    finished = Signal(str)
    status = Signal(str)
    busy = Signal(bool)

    def __init__(self, prompt: str, image_path: str | None = None, file_path: str | None = None, parent=None):
        super().__init__(parent)
        self.prompt = prompt
        self.image_path = image_path
        self.file_path = file_path

    def run(self):
        self.busy.emit(True)

        prompt = self.prompt or ""

        if self.file_path:
            try:
                self.status.emit("Reading file...")
                file_text = extract_text_with_unstructured(self.file_path)
                if len(file_text) > 60_000:
                    file_text = file_text[:60_000]
                prompt = (
                    f"{prompt}\n\n"
                    f"FILE: {Path(self.file_path).name}\n"
                    "CONTENT:\n"
                    f"{file_text}"
                )
            except Exception as e:
                prompt = f"{prompt}\n\nI couldn't read the attached file: {e}"

        url = _find_first_url(prompt)
        if url:
            try:
                self.status.emit("Fetching link...")
                extracted = fetch_url_text(url)
                prompt = (
                    "Summarize this publication clearly:\n\n"
                    f"URL: {url}\n\n"
                    "CONTENT:\n"
                    f"{extracted}\n\n"
                    "Give:\n"
                    "1) 5-bullet TL;DR\n"
                    "2) Key contributions\n"
                    "3) Methods/data\n"
                    "4) Results\n"
                    "5) Limitations\n"
                    "6) Who should read it\n"
                )
            except Exception as e:
                prompt = (
                    f"I couldn't fetch the link due to an error: {e}\n\n"
                    "Please paste the text or upload the PDF content, and I will summarize it."
                )

        self.status.emit("Thinking...")
        reply = chat_with_gpt(prompt, self.image_path)

        self.busy.emit(False)
        self.finished.emit(reply)


class HistoryDialog(QDialog):
    def __init__(self, chats, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Previous Chats")
        self.resize(400, 500)

        self.chats = chats
        self.parent_app = parent

        layout = QVBoxLayout(self)

        self.list_widget = QListWidget()
        layout.addWidget(self.list_widget)

        btn_layout = QHBoxLayout()
        self.delete_btn = QPushButton("Delete Chat")
        btn_layout.addStretch()
        btn_layout.addWidget(self.delete_btn)
        layout.addLayout(btn_layout)

        for chat in chats:
            item = QListWidgetItem(chat.get("summary") or "New Chat")
            item.setData(Qt.UserRole, chat)
            self.list_widget.addItem(item)

        self.list_widget.itemDoubleClicked.connect(self._open)
        self.delete_btn.clicked.connect(self._delete_chat)

    def _open(self, item):
        chat = item.data(Qt.UserRole)
        self.parent_app.load_chat(chat)
        self.accept()

    def _delete_chat(self):
        item = self.list_widget.currentItem()
        if not item:
            return

        chat = item.data(Qt.UserRole)
        confirm = QMessageBox.question(
            self,
            "Delete Chat",
            "Are you sure you want to delete this chat?",
            QMessageBox.Yes | QMessageBox.No
        )
        if confirm != QMessageBox.Yes:
            return

        self.chats.remove(chat)
        save_history(self.parent_app.username, self.chats)

        if chat == self.parent_app.active_chat:
            self.parent_app.start_new_chat()

        self.accept()


class ChatBubble(QWidget):
    def __init__(self, text="", is_user=True, image_path=None, file_path=None):
        super().__init__()

        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 2, 4, 2)

        bubble_widget = QWidget()
        bubble_layout = QVBoxLayout(bubble_widget)
        bubble_layout.setSpacing(6)
        bubble_layout.setContentsMargins(10, 8, 10, 8)

        if file_path:
            btn = QPushButton(f"📎 {Path(file_path).name}")
            btn.setCursor(Qt.PointingHandCursor)
            btn.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(file_path)))
            btn.setStyleSheet("""
                QPushButton {
                    text-align: left;
                    padding: 8px 10px;
                    border-radius: 10px;
                    background: rgba(255,255,255,0.10);
                    border: 1px solid rgba(255,255,255,0.15);
                    color: white;
                }
                QPushButton:hover {
                    background: rgba(255,255,255,0.16);
                }
            """)
            bubble_layout.addWidget(btn)

        if image_path:
            img = QLabel()
            pix = QPixmap(image_path).scaledToWidth(280, Qt.SmoothTransformation)
            img.setPixmap(pix)
            img.setAlignment(Qt.AlignCenter)
            bubble_layout.addWidget(img)

        if text:
            lbl = QLabel(text)
            lbl.setWordWrap(True)
            lbl.setTextInteractionFlags(Qt.TextSelectableByMouse)
            bubble_layout.addWidget(lbl)

        bubble_widget.setMaximumWidth(420)

        if is_user:
            bubble_widget.setStyleSheet("""
                QWidget {
                    background: #2b6cff;
                    color: white;
                    border-radius: 14px;
                }
            """)
            layout.addStretch()
            layout.addWidget(bubble_widget)
        else:
            bubble_widget.setStyleSheet("""
                QWidget {
                    background: #2a2a2a;
                    color: white;
                    border-radius: 14px;
                }
            """)
            layout.addWidget(bubble_widget)
            layout.addStretch()


class ChatApp(QWidget):
    def create_new_chat(self):
        return {"id": datetime.now(timezone.utc).isoformat(), "summary": "", "messages": []}

    def __init__(self, username: str, is_guest: bool):
        super().__init__()
        self.setWindowTitle("AI Chatbot")
        self.resize(800, 700)

        self.username = username
        self.is_guest = is_guest

        self.dark_mode = True
        self.chats = load_history(self.username, self.is_guest)
        self.active_chat = self.create_new_chat()
        self.current_worker: ChatWorker | None = None
        self.attached_file_path: str | None = None
        self.attached_image_path: str | None = None

        self.setAcceptDrops(True)

        self.main_layout = QVBoxLayout(self)
        self.main_layout.setContentsMargins(8, 8, 8, 8)
        self.main_layout.setSpacing(6)

        self.top_layout = QHBoxLayout()
        self.top_layout.setContentsMargins(0, 0, 0, 0)

        title = QLabel("AI Chatbot")
        title.setStyleSheet("font-size: 22px; font-weight: bold;")
        self.top_layout.addWidget(title)

        self.top_layout.addStretch()

        prev_btn = QPushButton("Previous Chats")
        prev_btn.clicked.connect(self.show_history_dialog)
        self.top_layout.addWidget(prev_btn)

        new_chat_btn = QPushButton("New Chat")
        new_chat_btn.clicked.connect(self.start_new_chat)
        self.top_layout.addWidget(new_chat_btn)

        self.toggle = ToggleSwitch(checked=True)
        self.toggle.toggled.connect(self.on_theme_toggled)
        self.top_layout.addWidget(self.toggle)

        self.main_layout.addLayout(self.top_layout)

        self.chat_widget = QWidget()
        self.chat_layout = QVBoxLayout(self.chat_widget)
        self.chat_layout.setAlignment(Qt.AlignTop)
        self.chat_layout.setSpacing(8)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setWidget(self.chat_widget)
        self.scroll.setFrameShape(QFrame.NoFrame)
        self.main_layout.addWidget(self.scroll, stretch=1)

        self.image_preview = QLabel()
        self.image_preview.setAlignment(Qt.AlignLeft)
        self.image_preview.hide()
        self.main_layout.addWidget(self.image_preview)

        self.scroll_down_btn = QPushButton("↓", self)
        self.scroll_down_btn.setFixedSize(44, 44)
        self.scroll_down_btn.setToolTip("Jump to newest message")
        self.scroll_down_btn.clicked.connect(self.scroll_to_bottom)
        self.scroll_down_btn.hide()
        self.scroll_down_btn.raise_()
        self.scroll_down_btn.setStyleSheet("""
            QPushButton {
                background: rgba(44, 44, 44, 0.9);
                border-radius: 22px;
                font-size: 18px;
                color: white;
            }
            QPushButton:hover {
                background: rgba(60, 60, 60, 1.0);
            }
        """)
        self.scroll.verticalScrollBar().valueChanged.connect(lambda _: self.update_scroll_button_visibility())

        self.typing_label = QLabel("")
        self.typing_label.setStyleSheet("font-size: 12px; color: #888888;")
        self.main_layout.addWidget(self.typing_label)

        self.progress = QProgressBar()
        self.progress.setFixedHeight(10)
        self.progress.setTextVisible(False)
        self.progress.setRange(0, 0)
        self.progress.hide()
        self.main_layout.addWidget(self.progress)

        self.input_frame = QFrame()
        input_layout = QHBoxLayout(self.input_frame)
        input_layout.setContentsMargins(10, 4, 10, 4)
        input_layout.setSpacing(6)

        self.entry = QLineEdit()
        self.entry.setPlaceholderText("Type a message...")
        self.entry.returnPressed.connect(self.handle_send_clicked)

        attach_btn = QPushButton()
        attach_btn.setIcon(QIcon("icons/attach.svg"))
        attach_btn.setIconSize(QSize(18, 18))
        attach_btn.setFixedSize(36, 36)
        attach_btn.clicked.connect(self.attach_file)

        send_btn = QPushButton()
        send_btn.setIcon(QIcon("icons/send.svg"))
        send_btn.setIconSize(QSize(18, 18))
        send_btn.setFixedSize(36, 36)
        send_btn.clicked.connect(self.handle_send_clicked)
        self.send_btn = send_btn

        input_layout.addWidget(self.entry, stretch=1)
        input_layout.addWidget(attach_btn)
        input_layout.addWidget(send_btn)

        self.input_frame.setStyleSheet("""
            QFrame {
                background: #1e1e1e;
                border-radius: 18px;
            }
            QLineEdit {
                border: none;
                padding: 8px;
                background: transparent;
                font-size: 14px;
            }
            QPushButton {
                border-radius: 18px;
                background: transparent;
            }
            QPushButton:hover {
                background: rgba(255, 255, 255, 0.08);
            }
        """)
        self.main_layout.addWidget(self.input_frame)

        QTimer.singleShot(0, self.update_scroll_button_visibility)

        self.apply_theme()

        if self.chats:
            self.load_chat(self.chats[-1])

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self.scroll_down_btn.isVisible():
            self.position_scroll_button()

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event):
        urls = event.mimeData().urls()
        if not urls:
            return

        path = urls[0].toLocalFile()
        ext = Path(path).suffix.lower()

        if ext in [".png", ".jpg", ".jpeg", ".webp", ".bmp"]:
            self.attached_image_path = path
            self.attached_file_path = None
            self.show_image_preview(path)
            return

        if ext in [".pdf", ".docx", ".txt", ".pptx", ".xlsx"]:
            self.attached_file_path = path
            self.attached_image_path = None
            self.show_file_preview(path)

    def scroll_to_bottom(self):
        QTimer.singleShot(
            0,
            lambda: self.scroll.verticalScrollBar().setValue(self.scroll.verticalScrollBar().maximum())
        )

    def is_near_bottom(self, threshold: int = 80) -> bool:
        vbar = self.scroll.verticalScrollBar()
        return (vbar.maximum() - vbar.value()) <= threshold

    def set_ui_busy(self, busy: bool):
        self.entry.setDisabled(busy)
        self.send_btn.setDisabled(busy)

    def on_worker_busy(self, is_busy: bool):
        self.set_ui_busy(is_busy)
        if is_busy:
            self.progress.show()
            self.progress.setRange(0, 0)
        else:
            self.progress.hide()

    def on_worker_status(self, text: str):
        self.typing_label.setText(text)

    def start_new_chat(self):
        if self.active_chat["messages"]:
            if self.active_chat not in self.chats:
                self.chats.append(self.active_chat)
            save_history(self.username, self.chats)

        while self.chat_layout.count():
            item = self.chat_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()

        self.active_chat = self.create_new_chat()

    def load_chat(self, chat):
        while self.chat_layout.count():
            item = self.chat_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()

        self.active_chat = chat

        for msg in chat.get("messages", []):
            self.add_message(
                msg.get("content", ""),
                is_user=(msg.get("role") == "user"),
                image_path=msg.get("image"),
                file_path=msg.get("file")
            )

    def add_message(self, text, is_user=True, image_path=None, file_path=None):
        should_follow = self.is_near_bottom()

        bubble = ChatBubble(text, is_user=is_user, image_path=image_path, file_path=file_path)
        self.chat_layout.addWidget(bubble)

        if should_follow:
            self.scroll_to_bottom()

        self.update_scroll_button_visibility()

    def show_history_dialog(self):
        dlg = HistoryDialog(self.chats, self)
        dlg.exec()

    def show_image_preview(self, path: str):
        pix = QPixmap(path).scaledToWidth(200, Qt.SmoothTransformation)
        self.image_preview.setPixmap(pix)
        self.image_preview.show()
        self.typing_label.setText(f"Attached image: {Path(path).name}")

    def show_file_preview(self, path: str):
        self.image_preview.hide()
        self.typing_label.setText(f"Attached file: {Path(path).name}")

    def attach_file(self):
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Attach file",
            "",
            "Supported Files (*.png *.jpg *.jpeg *.webp *.bmp *.pdf *.docx *.txt *.pptx *.xlsx);;All Files (*)"
        )
        if not file_path:
            self.attached_file_path = None
            self.attached_image_path = None
            self.image_preview.hide()
            self.typing_label.setText("")
            return

        ext = Path(file_path).suffix.lower()
        if ext in [".png", ".jpg", ".jpeg", ".webp", ".bmp"]:
            self.attached_image_path = file_path
            self.attached_file_path = None
            self.show_image_preview(file_path)
        else:
            self.attached_file_path = file_path
            self.attached_image_path = None
            self.show_file_preview(file_path)

    def handle_send_clicked(self):
        text = self.entry.text().strip()
        if not text and not self.attached_image_path and not self.attached_file_path:
            return
        self.send_message(text)

    def send_message(self, text: str):
        if not text and not self.attached_image_path and not self.attached_file_path:
            return

        file_path = self.attached_file_path
        img_path = self.attached_image_path

        if file_path:
            self.add_message(text or "", is_user=True, file_path=file_path)
            self.active_chat["messages"].append({
                "role": "user",
                "content": text,
                "image": None,
                "file": file_path
            })
        else:
            self.add_message(text or "[Image]", is_user=True, image_path=img_path)
            self.active_chat["messages"].append({
                "role": "user",
                "content": text,
                "image": img_path,
                "file": None
            })

        if self.active_chat not in self.chats:
            self.chats.append(self.active_chat)
        save_history(self.username, self.chats)

        self.entry.clear()
        self.attached_image_path = None
        self.attached_file_path = None
        self.image_preview.hide()

        self.typing_label.setText("Starting...")
        self.current_worker = ChatWorker(text, img_path, file_path)
        self.current_worker.finished.connect(self.on_bot_reply)
        self.current_worker.status.connect(self.on_worker_status)
        self.current_worker.busy.connect(self.on_worker_busy)
        self.current_worker.start()

    def on_bot_reply(self, reply: str):
        self.typing_label.setText("")
        self.add_message(reply, is_user=False)

        self.current_worker = None
        self.active_chat["messages"].append({
            "role": "bot",
            "content": reply,
            "image": None,
            "file": None
        })

        save_history(self.username, self.chats)

        if not self.active_chat.get("summary"):
            first_user = next((m.get("content", "") for m in self.active_chat["messages"] if m.get("role") == "user"), "")
            self.active_chat["summary"] = (first_user[:40] + "…" if len(first_user) > 40 else first_user)
            save_history(self.username, self.chats)

        if self.is_near_bottom():
            self.scroll_to_bottom()

    def update_scroll_button_visibility(self):
        if self.is_near_bottom():
            self.scroll_down_btn.hide()
        else:
            self.scroll_down_btn.show()
            self.position_scroll_button()

    def position_scroll_button(self):
        margin = 12
        btn_w = self.scroll_down_btn.width()
        btn_h = self.scroll_down_btn.height()
        input_geom = self.input_frame.geometry()
        x = self.width() - btn_w - margin
        y = input_geom.y() - btn_h - margin
        self.scroll_down_btn.move(x, y)

    def on_theme_toggled(self, is_dark: bool):
        self.dark_mode = is_dark
        self.apply_theme()

    def closeEvent(self, event):
        save_history(self.username, self.chats)
        event.accept()

    def apply_theme(self):
        if self.dark_mode:
            self.setStyleSheet("""
                QWidget { background: #121212; color: white; }
                QLabel { color: white; }
                QPushButton {
                    background: #2c2c2c;
                    border-radius: 6px;
                    padding: 6px;
                    color: white;
                }
                QPushButton:hover { background: #3a3a3a; }
            """)
        else:
            self.setStyleSheet("""
                QWidget { background: #ffffff; color: black; }
                QLabel { color: black; }
                QPushButton {
                    background: #dddddd;
                    border-radius: 6px;
                    padding: 6px;
                    color: black;
                }
                QPushButton:hover { background: #cccccc; }
            """)


if __name__ == "__main__":
    app = QApplication(sys.argv)

    login = LoginDialog()
    if login.exec() != QDialog.Accepted or not login.username:
        sys.exit(0)

    window = ChatApp(username=login.username, is_guest=login.is_guest)
    window.show()
    sys.exit(app.exec())
