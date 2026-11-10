"""Dialog for entering the ElevenLabs API key, stored in Windows Credential Manager."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QHBoxLayout, QLabel, QLayout, QLineEdit, QPushButton, QVBoxLayout

from . import credentials
from .i18n import tr
from .scribe import KEY_PREFIX, find_key


class KeyDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr('key.title'))
        self.setModal(True)
        self.outcome = ''
        self._confirmed = None  # A value the user chose to save despite the format warning.

        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 20, 22, 18)
        layout.setSpacing(12)
        layout.setSizeConstraint(QLayout.SizeConstraint.SetFixedSize)  # Grows when a warning appears.
        for paragraph in tr('key.intro').splitlines():
            intro = QLabel(paragraph)
            intro.setObjectName('dialogText')
            intro.setWordWrap(True)
            intro.setFixedWidth(440)
            layout.addWidget(intro)
        source = find_key()[1]
        current = QLabel(tr('key.current', source=tr(f'key.src.{source or "none"}')))
        current.setObjectName('dialogNote')
        current.setWordWrap(True)
        current.setFixedWidth(440)
        layout.addWidget(current)
        self.field = QLineEdit()
        self.field.setEchoMode(QLineEdit.EchoMode.Password)
        self.field.setPlaceholderText('sk_…')
        self.field.returnPressed.connect(self.save)
        layout.addWidget(self.field)
        self.message = QLabel()
        self.message.setObjectName('dialogWarn')
        self.message.setWordWrap(True)
        self.message.setFixedWidth(440)
        self.message.hide()
        layout.addWidget(self.message)

        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        self.remove_button = QPushButton(tr('key.remove'))
        self.remove_button.setProperty('kind', 'ghost')
        self.remove_button.setVisible(source == 'saved')
        self.remove_button.clicked.connect(self.remove)
        cancel = QPushButton(tr('cancel'))
        cancel.clicked.connect(self.reject)
        self.save_button = QPushButton(tr('key.save'))
        self.save_button.setProperty('kind', 'primary')
        self.save_button.setDefault(True)
        self.save_button.clicked.connect(self.save)
        buttons.addWidget(self.remove_button)
        buttons.addStretch(1)
        buttons.addWidget(cancel)
        buttons.addWidget(self.save_button)
        layout.addLayout(buttons)
        for button in (self.remove_button, cancel, self.save_button):
            button.setCursor(Qt.CursorShape.PointingHandCursor)

    def _warn(self, text):
        self.message.setText(text)
        self.message.show()

    def save(self):
        value = self.field.text().strip()
        if not value:
            self._warn(tr('key.empty'))
        elif not value.startswith(KEY_PREFIX) and self._confirmed != value:
            self._confirmed = value  # A key ID pasted by mistake is the commonest error: ask once.
            self._warn(tr('key.format'))
        elif credentials.write(value):
            self.outcome = tr('key.saved')
            self.accept()
        else:
            self._warn(tr('key.failed'))

    def remove(self):
        credentials.delete()
        self.outcome = tr('key.removed')
        self.accept()
