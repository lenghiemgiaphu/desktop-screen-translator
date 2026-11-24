"""Dark theme shared by the overlay, cards and the settings dialog."""

BADGE_STYLES = {
    "local": ("#16301F", "#4ADE80", "#2E5A44"),
    "google": ("#1B2A44", "#60A5FA", "#2B4470"),
    "cache": ("#2A2A3D", "#A5B4FC", "#41416B"),
    "error": ("#3A2A12", "#FBBF24", "#6B4A12"),
    "context": ("#262932", "#9CA3AF", "#3A3D47"),
    "history": ("#262932", "#9CA3AF", "#3A3D47"),
}


def badge_qss(kind: str) -> str:
    bg, fg, border = BADGE_STYLES.get(kind, BADGE_STYLES["history"])
    return (f"background-color: {bg}; color: {fg}; border: 1px solid {border}; "
            "border-radius: 8px; padding: 1px 7px; font-family: 'Segoe UI'; font-size: 7.5pt; font-weight: 600;")


OVERLAY_QSS = """
QWidget#MainContainer { background-color: #1D1E23; border: 1px solid #3A3D47; border-radius: 10px; }
QLabel#HeaderTitle { color: #9CA3AF; font-family: "Segoe UI"; font-size: 8.5pt; font-weight: 600; }
QLabel#Hint { color: #8E95A5; font-family: "Segoe UI"; font-size: 9pt; padding: 8px 2px; }
QLabel#ErrorHint { color: #FBBF24; font-family: "Segoe UI"; font-size: 9pt; padding: 8px 2px; }
QComboBox { background-color: #272930; color: #F3F4F6; border: 1px solid #3A3D47; border-radius: 5px;
            padding: 3px 8px; font-family: "Segoe UI"; font-size: 9pt; min-width: 160px; }
QComboBox:hover, QComboBox:focus { border-color: #5B6275; }
QComboBox QLineEdit { background: transparent; color: #F3F4F6; border: none; selection-background-color: #2563EB; }
QComboBox::drop-down { border: none; width: 20px; }
QComboBox QAbstractItemView { background-color: #272930; color: #E5E7EB; border: 1px solid #454957;
                              selection-background-color: #373B47; outline: none; }
QLineEdit#HistorySearch { background-color: #272930; color: #F3F4F6; border: 1px solid #3A3D47;
                          border-radius: 5px; padding: 5px 9px; font-family: "Segoe UI"; font-size: 9pt; }
QLineEdit#HistorySearch:focus { border-color: #5B6275; }
QPushButton#HeaderBtn { background-color: #272930; color: #D1D5DB; border: 1px solid #3A3D47; border-radius: 5px;
                        padding: 3px 9px; font-family: "Segoe UI"; font-size: 8.5pt; font-weight: 600; }
QPushButton#HeaderBtn:hover { background-color: #32353E; color: #FFFFFF; }
QPushButton#HeaderBtn[active="true"] { background-color: #2563EB; border-color: #2563EB; color: #FFFFFF; }
QPushButton#DangerBtn { background-color: #2E2326; color: #F87171; border: 1px solid #4B2E33; border-radius: 5px;
                        padding: 4px 9px; font-size: 8.5pt; }
QPushButton#DangerBtn:hover { background-color: #3F282C; }
QPushButton#CloseBtn { background-color: transparent; color: #9CA3AF; border: none; border-radius: 5px;
                       font-size: 11pt; font-weight: bold; }
QPushButton#CloseBtn:hover { background-color: #DC2626; color: #FFFFFF; }
QScrollArea { border: none; background: transparent; }
QScrollBar:vertical { background: transparent; width: 6px; margin: 2px 0; }
QScrollBar::handle:vertical { background: #3E424E; border-radius: 3px; min-height: 20px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0px; }
QProgressBar { background-color: #272930; border: none; border-radius: 2px; max-height: 4px; }
QProgressBar::chunk { background-color: #2563EB; border-radius: 2px; }
"""

CARD_QSS = """
QWidget#ResultCard { background-color: #272930; border: 1px solid #353842; border-radius: 7px; }
QWidget#ResultCard:hover { border: 1px solid #4B505E; background-color: #2B2E36; }
QPushButton#CopyRawBtn { background-color: #2F323B; color: #9CA3AF; border: 1px solid #3E424E; border-radius: 4px;
                         font-family: "Segoe UI"; font-size: 8pt; }
QPushButton#CopyRawBtn:hover { background-color: #3B3F4B; color: #E5E7EB; }
QPushButton#CopyTransBtn { background-color: #373B47; color: #E5E7EB; border: 1px solid #4A4F5E; border-radius: 4px;
                           font-family: "Segoe UI"; font-size: 8pt; font-weight: 600; }
QPushButton#CopyTransBtn:hover { background-color: #464B59; color: #FFFFFF; }
QPushButton#CopyTransBtn:disabled { background-color: #2A2C33; color: #5B6070; border-color: #343741; }
QPushButton#SaveBtn { background-color: #1B2A44; color: #93C5FD; border: 1px solid #2B4470; border-radius: 4px;
                      font-family: "Segoe UI"; font-size: 8pt; font-weight: 600; padding: 0 8px; }
QPushButton#SaveBtn:hover { background-color: #233659; color: #FFFFFF; }
QPushButton#SaveBtn:disabled { background-color: #16301F; color: #4ADE80; border-color: #2E5A44; }
"""

COPIED_QSS = ("background-color: #1E3A2F; color: #4ADE80; border: 1px solid #2E5A44; "
              "border-radius: 4px; font-size: 8.5pt; font-weight: bold;")

DIALOG_QSS = """
QDialog { background-color: #1D1E23; }
QLabel { color: #D1D5DB; font-family: "Segoe UI"; font-size: 9pt; }
QLabel#Section { color: #9CA3AF; font-size: 8.5pt; font-weight: 600; }
QLabel#Status { color: #8E95A5; font-size: 8.5pt; }
QLineEdit { background-color: #272930; color: #F3F4F6; border: 1px solid #3A3D47; border-radius: 5px; padding: 5px 8px; }
QLineEdit:focus { border-color: #5B6275; }
QCheckBox { color: #E5E7EB; font-family: "Segoe UI"; font-size: 9pt; spacing: 8px; }
QPushButton { background-color: #272930; color: #E5E7EB; border: 1px solid #3A3D47; border-radius: 5px; padding: 5px 14px; }
QPushButton:hover { background-color: #32353E; }
QPushButton:default { background-color: #2563EB; border-color: #2563EB; color: #FFFFFF; }
"""
