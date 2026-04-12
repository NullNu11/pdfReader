"""
PDF Reader – continuous scroll, annotations, auto-hide.
All pages are rendered vertically in the scene so scrolling naturally
transitions between pages. Drawing tools work per-page with correct
coordinate mapping. Scroll is blocked in drawing modes.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass, field
from enum import Enum, auto

import pymupdf as fitz  # PyMuPDF >= 1.24.x
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QGraphicsView, QGraphicsScene,
    QGraphicsPixmapItem, QFileDialog, QToolBar, QLabel,
    QSpinBox, QComboBox, QDockWidget, QTreeWidget, QTreeWidgetItem,
    QPushButton, QColorDialog, QWidget, QHBoxLayout, QVBoxLayout,
    QFrame, QMenu, QCheckBox, QMenuBar, QSlider, QScrollBar,
    QSizePolicy,
)
from PyQt6.QtGui import (
    QPixmap, QImage, QAction, QKeySequence, QWheelEvent,
    QPen, QColor, QPainter, QPainterPath,
)
from PyQt6.QtCore import Qt, QTimer, QPointF, QRectF

PAGE_GAP = 12  # pixels between pages in scene coords


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------
class ToolMode(Enum):
    BROWSE = auto()
    PEN = auto()
    HIGHLIGHTER = auto()
    LINE = auto()
    RECT = auto()
    ELLIPSE = auto()


@dataclass
class Stroke:
    tool: ToolMode
    color: QColor
    width: float
    page_idx: int = 0
    points: list[QPointF] = field(default_factory=list)
    start: QPointF | None = None
    end: QPointF | None = None


class AnnotationStore:
    def __init__(self):
        self._pages: dict[int, list[Stroke]] = {}

    def strokes(self, page: int) -> list[Stroke]:
        return self._pages.setdefault(page, [])

    def add(self, page: int, stroke: Stroke):
        self._pages.setdefault(page, []).append(stroke)

    def undo(self, page: int) -> bool:
        s = self._pages.get(page, [])
        if s:
            s.pop()
            return True
        return False

    def clear(self):
        self._pages.clear()


# ---------------------------------------------------------------------------
# Floating pen-options popup
# ---------------------------------------------------------------------------
class PenOptionsPopup(QFrame):
    def __init__(self, parent: PdfReaderWindow):
        super().__init__(parent, Qt.WindowType.Popup)
        self._main = parent
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setStyleSheet(
            "PenOptionsPopup { background: #2b2b2b; border: 1px solid #555;"
            " border-radius: 6px; padding: 6px; }"
            "QLabel { color: #ddd; font-size: 12px; }"
            "QPushButton { min-width: 24px; min-height: 24px; }"
            "QCheckBox { color: #ccc; font-size: 11px; }"
        )
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        # Colors
        layout.addWidget(QLabel("Color"))
        color_row = QHBoxLayout()
        color_row.setSpacing(4)
        for hex_c in [
            "#FF0000", "#0000FF", "#00AA00", "#FF00FF", "#000000",
            "#FFFF00", "#00FFFF", "#FF69B4", "#FFA500", "#FFFFFF",
        ]:
            btn = QPushButton()
            btn.setFixedSize(24, 24)
            btn.setStyleSheet(
                f"background-color: {hex_c}; border: 1px solid #888; border-radius: 4px;"
            )
            btn.clicked.connect(lambda _, c=hex_c: self._pick_preset(c))
            color_row.addWidget(btn)
        btn_custom = QPushButton("…")
        btn_custom.setFixedSize(24, 24)
        btn_custom.setToolTip("Custom color")
        btn_custom.setStyleSheet(
            "background: qlineargradient(x1:0,y1:0,x2:1,y2:1,"
            "stop:0 red,stop:0.5 green,stop:1 blue);"
            "border:1px solid #888; border-radius:4px; color:white; font-weight:bold;"
        )
        btn_custom.clicked.connect(self._pick_custom)
        color_row.addWidget(btn_custom)
        layout.addLayout(color_row)

        # Width slider
        wlr = QHBoxLayout()
        wlr.addWidget(QLabel("Width"))
        self._wlabel = QLabel("2 px")
        self._wlabel.setStyleSheet("color: #aaa; font-size: 11px;")
        wlr.addStretch()
        wlr.addWidget(self._wlabel)
        layout.addLayout(wlr)
        self._wslider = QSlider(Qt.Orientation.Horizontal)
        self._wslider.setRange(1, 20)
        self._wslider.setValue(2)
        self._wslider.setTickPosition(QSlider.TickPosition.TicksBelow)
        self._wslider.setTickInterval(2)
        self._wslider.setStyleSheet(
            "QSlider::groove:horizontal{background:#555;height:6px;border-radius:3px}"
            "QSlider::handle:horizontal{background:#0078d4;width:14px;margin:-4px 0;border-radius:7px}"
            "QSlider::sub-page:horizontal{background:#0078d4;border-radius:3px}"
        )
        self._wslider.valueChanged.connect(self._on_slider)
        layout.addWidget(self._wslider)

        # Bottom row
        br = QHBoxLayout()
        self._auto_cb = QCheckBox("Auto close (3s)")
        self._auto_cb.setChecked(True)
        br.addWidget(self._auto_cb)
        br.addStretch()
        bc = QPushButton("✕")
        bc.setFixedSize(22, 22)
        bc.setStyleSheet("background:#555;color:#ddd;border-radius:3px;")
        bc.clicked.connect(self.close)
        br.addWidget(bc)
        layout.addLayout(br)

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(lambda: self.close() if self._auto_cb.isChecked() else None)

    def show_at(self, gpos):
        self._wslider.blockSignals(True)
        self._wslider.setValue(int(self._main._pen_width))
        self._wslider.blockSignals(False)
        self._wlabel.setText(f"{int(self._main._pen_width)} px")
        self.move(gpos)
        self.show()
        self._restart()

    def _restart(self):
        self._timer.stop()
        if self._auto_cb.isChecked():
            self._timer.start(3000)

    def _pick_preset(self, c):
        self._main._set_color(QColor(c))
        self._restart()

    def _pick_custom(self):
        self._timer.stop()
        c = QColorDialog.getColor(self._main._pen_color, self, "Pick color")
        if c.isValid():
            self._main._set_color(c)
        self._restart()

    def _on_slider(self, v):
        self._main._pen_width = float(v)
        self._wlabel.setText(f"{v} px")
        self._restart()

    def enterEvent(self, e):
        self._restart()
        super().enterEvent(e)

    def mouseMoveEvent(self, e):
        self._restart()
        super().mouseMoveEvent(e)


# ---------------------------------------------------------------------------
# Graphics view – continuous scroll, drawing, scroll-block in draw mode
# ---------------------------------------------------------------------------
class PdfGraphicsView(QGraphicsView):
    def __init__(self, scene: QGraphicsScene, parent: PdfReaderWindow):
        super().__init__(scene, parent)
        self._main: PdfReaderWindow = parent
        self.setRenderHints(
            self.renderHints()
            | QPainter.RenderHint.Antialiasing
            | QPainter.RenderHint.SmoothPixmapTransform
        )
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop)
        self._update_drag_mode()

    def _update_drag_mode(self):
        if self._main._tool_mode == ToolMode.BROWSE:
            self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
            self.setCursor(Qt.CursorShape.OpenHandCursor)
        else:
            self.setDragMode(QGraphicsView.DragMode.NoDrag)
            self.setCursor(Qt.CursorShape.CrossCursor)

    # -- coordinate helpers --------------------------------------------------
    def _scene_to_page_pdf(self, scene_pt: QPointF, lock_page: int = -1) -> tuple[int, QPointF] | None:
        """Map a scene point to (page_index, pdf_coords_on_that_page).
        If lock_page >= 0, always use that page's offset (prevents drift
        when drawing near page boundaries)."""
        m = self._main
        if m._doc is None:
            return None
        offsets = m._page_y_offsets
        scale = m._render_scale

        if 0 <= lock_page < len(offsets):
            local_y = scene_pt.y() - offsets[lock_page]
            return lock_page, QPointF(scene_pt.x() / scale, local_y / scale)

        for i in range(len(m._doc) - 1, -1, -1):
            if scene_pt.y() >= offsets[i]:
                local_y = scene_pt.y() - offsets[i]
                return i, QPointF(scene_pt.x() / scale, local_y / scale)
        return 0, QPointF(scene_pt.x() / scale, scene_pt.y() / scale)

    # -- mouse events --------------------------------------------------------
    def mousePressEvent(self, event):
        if self._main._tool_mode == ToolMode.BROWSE or self._main._doc is None:
            return super().mousePressEvent(event)
        if event.button() != Qt.MouseButton.LeftButton:
            return super().mousePressEvent(event)
        sp = self.mapToScene(event.pos())
        hit = self._scene_to_page_pdf(sp)
        if hit is None:
            return
        page_idx, pdf_pt = hit
        mode = self._main._tool_mode
        stroke = Stroke(
            tool=mode, color=QColor(self._main._pen_color),
            width=self._main._pen_width, page_idx=page_idx,
        )
        if mode in (ToolMode.PEN, ToolMode.HIGHLIGHTER):
            stroke.points.append(pdf_pt)
        else:
            stroke.start = pdf_pt
            stroke.end = pdf_pt
        self._main._current_stroke = stroke
        self._main._drawing_page = page_idx
        event.accept()

    def mouseMoveEvent(self, event):
        stroke = self._main._current_stroke
        if stroke is None:
            return super().mouseMoveEvent(event)
        sp = self.mapToScene(event.pos())
        # Lock to the page where drawing started to prevent drift
        hit = self._scene_to_page_pdf(sp, lock_page=stroke.page_idx)
        if hit is None:
            return
        _, pdf_pt = hit
        if stroke.tool in (ToolMode.PEN, ToolMode.HIGHLIGHTER):
            stroke.points.append(pdf_pt)
        else:
            stroke.end = pdf_pt
        self._main._render_page_pixmap(stroke.page_idx)
        event.accept()

    def mouseReleaseEvent(self, event):
        stroke = self._main._current_stroke
        if stroke is None:
            return super().mouseReleaseEvent(event)
        sp = self.mapToScene(event.pos())
        # Lock to the page where drawing started
        hit = self._scene_to_page_pdf(sp, lock_page=stroke.page_idx)
        if hit:
            _, pdf_pt = hit
            if stroke.tool in (ToolMode.PEN, ToolMode.HIGHLIGHTER):
                stroke.points.append(pdf_pt)
            else:
                stroke.end = pdf_pt
        self._main._annotations.add(stroke.page_idx, stroke)
        self._main._current_stroke = None
        self._main._drawing_page = -1
        self._main._render_page_pixmap(stroke.page_idx)
        event.accept()

    def wheelEvent(self, event: QWheelEvent):
        # Only block scroll while actively drawing (mouse held down)
        if self._main._current_stroke is not None:
            event.accept()
            return
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            factor = 1.15 if event.angleDelta().y() > 0 else 1 / 1.15
            self.scale(factor, factor)
        else:
            super().wheelEvent(event)


# ---------------------------------------------------------------------------
# Main window
# ---------------------------------------------------------------------------
class PdfReaderWindow(QMainWindow):
    ZOOM_LEVELS = [25, 50, 75, 100, 125, 150, 200, 300, 400]

    def __init__(self):
        super().__init__()
        self.setWindowTitle("PDF Reader")
        self.resize(1060, 760)

        # Document
        self._doc: fitz.Document | None = None
        self._doc_path: str | None = None
        self._current_page = 0
        self._zoom_pct = 100
        self._render_scale = 2.0

        # Continuous-scroll page layout: y-offset of each page in scene coords
        self._page_y_offsets: list[float] = []
        self._page_items: list[QGraphicsPixmapItem] = []

        # Annotations
        self._annotations = AnnotationStore()
        self._current_stroke: Stroke | None = None
        self._drawing_page: int = -1
        self._tool_mode = ToolMode.BROWSE
        self._pen_color = QColor(Qt.GlobalColor.red)
        self._pen_width = 2.0

        # Auto-hide
        self._hidden = False
        self._hide_timer = QTimer(self)
        self._hide_timer.setInterval(200)
        self._hide_timer.timeout.connect(self._check_mouse_position)
        self._hide_timer.start()
        self.setMouseTracking(True)

        # Popup
        self._pen_popup: PenOptionsPopup | None = None

        self._build_menubar()
        self._build_ui()
        self._build_toolbar()
        self._build_outline_dock()
        self._update_ui_state()

    # ================================================================
    # Menu bar
    # ================================================================
    def _build_menubar(self):
        mb = self.menuBar()
        fm = mb.addMenu("File")
        a = QAction("Open…", self)
        a.setShortcut(QKeySequence.StandardKey.Open)
        a.triggered.connect(self._open_file)
        fm.addAction(a)
        self._act_save = QAction("Save As…", self)
        self._act_save.setShortcut(QKeySequence.StandardKey.Save)
        self._act_save.triggered.connect(self._save_annotations)
        fm.addAction(self._act_save)
        em = mb.addMenu("Edit")
        au = QAction("Undo", self)
        au.setShortcut(QKeySequence.StandardKey.Undo)
        au.triggered.connect(self._undo)
        em.addAction(au)

    # ================================================================
    # Central widget
    # ================================================================
    def _build_ui(self):
        self._scene = QGraphicsScene(self)
        self._view = PdfGraphicsView(self._scene, self)
        self.setCentralWidget(self._view)
        # Track scroll to update current page indicator
        self._view.verticalScrollBar().valueChanged.connect(self._on_scroll)

    # ================================================================
    # Toolbar
    # ================================================================
    def _build_toolbar(self):
        self._main_toolbar = tb = QToolBar("Main", self)
        tb.setMovable(False)
        tb.setStyleSheet("QToolBar{spacing:4px;}")
        self.addToolBar(tb)

        ao = QAction("Open", self)
        ao.setShortcut(QKeySequence.StandardKey.Open)
        ao.triggered.connect(self._open_file)
        tb.addAction(ao)

        self._act_save_tb = QAction("Save As", self)
        self._act_save_tb.triggered.connect(self._save_annotations)
        tb.addAction(self._act_save_tb)
        tb.addSeparator()

        # Page nav
        self._btn_prev = QPushButton("◀")
        self._btn_prev.setFixedWidth(28)
        self._btn_prev.clicked.connect(self._prev_page)
        tb.addWidget(self._btn_prev)
        self._page_spin = QSpinBox()
        self._page_spin.setMinimum(1)
        self._page_spin.setKeyboardTracking(False)
        self._page_spin.valueChanged.connect(lambda v: self._go_to_page(v - 1))
        tb.addWidget(self._page_spin)
        self._page_label = QLabel(" / 0")
        tb.addWidget(self._page_label)
        self._btn_next = QPushButton("▶")
        self._btn_next.setFixedWidth(28)
        self._btn_next.clicked.connect(self._next_page)
        tb.addWidget(self._btn_next)
        tb.addSeparator()

        # Zoom
        self._zoom_combo = QComboBox()
        for z in self.ZOOM_LEVELS:
            self._zoom_combo.addItem(f"{z}%", z)
        self._zoom_combo.setCurrentText("100%")
        self._zoom_combo.setEditable(True)
        self._zoom_combo.currentTextChanged.connect(self._on_zoom_combo)
        tb.addWidget(self._zoom_combo)
        for label, slot in [("+", self._zoom_in), ("−", self._zoom_out)]:
            a = QAction(label, self)
            a.triggered.connect(slot)
            tb.addAction(a)
        tb.addSeparator()

        # Drawing tools
        self._btn_browse = QPushButton("Browse")
        self._btn_browse.setCheckable(True)
        self._btn_browse.setChecked(True)
        self._btn_browse.clicked.connect(lambda: self._set_tool(ToolMode.BROWSE))
        tb.addWidget(self._btn_browse)

        self._btn_pen = QPushButton("Pen ✏️")
        self._btn_pen.setCheckable(True)
        self._btn_pen.clicked.connect(self._on_pen_clicked)
        tb.addWidget(self._btn_pen)

        self._btn_hl = QPushButton("Highlight 🖍️")
        self._btn_hl.setCheckable(True)
        self._btn_hl.clicked.connect(self._on_hl_clicked)
        tb.addWidget(self._btn_hl)

        self._shape_btn = QPushButton("Shapes ▾")
        self._shape_btn.setCheckable(True)
        sm = QMenu(self)
        sm.addAction("Line ─").triggered.connect(lambda: self._set_shape_tool(ToolMode.LINE, "Line ─ ▾"))
        sm.addAction("Rect ▭").triggered.connect(lambda: self._set_shape_tool(ToolMode.RECT, "Rect ▭ ▾"))
        sm.addAction("Ellipse ◯").triggered.connect(lambda: self._set_shape_tool(ToolMode.ELLIPSE, "Ellipse ◯ ▾"))
        self._shape_btn.setMenu(sm)
        tb.addWidget(self._shape_btn)
        tb.addSeparator()

        # Undo button in toolbar
        act_undo_tb = QAction("Undo ↩", self)
        act_undo_tb.setShortcut(QKeySequence.StandardKey.Undo)
        act_undo_tb.triggered.connect(self._undo)
        tb.addAction(act_undo_tb)
        tb.addSeparator()

        self._color_indicator = QPushButton()
        self._color_indicator.setFixedSize(24, 24)
        self._color_indicator.setToolTip("Current color")
        self._color_indicator.clicked.connect(self._show_pen_popup)
        self._update_color_indicator()
        tb.addWidget(self._color_indicator)

        self._tool_buttons = {
            ToolMode.BROWSE: self._btn_browse,
            ToolMode.PEN: self._btn_pen,
            ToolMode.HIGHLIGHTER: self._btn_hl,
        }

        self._toolbar_collapsed = False

        # Separate small toolbar for the collapse button (always at the right end)
        self._collapse_toolbar = ctb = QToolBar("Collapse", self)
        ctb.setMovable(False)
        ctb.setStyleSheet("QToolBar{spacing:0px; border:none;}")
        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        ctb.addWidget(spacer)
        self._collapse_btn = QPushButton("▲")
        self._collapse_btn.setFixedSize(28, 22)
        self._collapse_btn.setToolTip("Collapse toolbar")
        self._collapse_btn.setStyleSheet(
            "QPushButton{background:#555;color:#ddd;border:1px solid #777;border-radius:3px;font-size:11px;}"
            "QPushButton:hover{background:#666;}"
        )
        self._collapse_btn.clicked.connect(self._toggle_toolbar)
        ctb.addWidget(self._collapse_btn)
        self.addToolBar(ctb)

    # ================================================================
    # Outline dock
    # ================================================================
    def _build_outline_dock(self):
        self._outline_tree = QTreeWidget()
        self._outline_tree.setHeaderLabel("Outline")
        self._outline_tree.itemClicked.connect(self._on_outline_click)
        dock = QDockWidget("Outline", self)
        dock.setWidget(self._outline_tree)
        dock.setAllowedAreas(
            Qt.DockWidgetArea.LeftDockWidgetArea | Qt.DockWidgetArea.RightDockWidgetArea
        )
        self.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, dock)
        self._outline_dock = dock

    # ================================================================
    # Toolbar collapse / expand
    # ================================================================
    def _toggle_toolbar(self):
        self._toolbar_collapsed = not self._toolbar_collapsed
        if self._toolbar_collapsed:
            self._main_toolbar.hide()
            self._collapse_btn.setText("▼")
            self._collapse_btn.setToolTip("Expand toolbar")
        else:
            self._main_toolbar.show()
            self._collapse_btn.setText("▲")
            self._collapse_btn.setToolTip("Collapse toolbar")

    # ================================================================
    # Tool switching
    # ================================================================
    def _set_tool(self, mode: ToolMode):
        self._tool_mode = mode
        for m, btn in self._tool_buttons.items():
            btn.setChecked(m == mode)
        self._shape_btn.setChecked(mode in (ToolMode.LINE, ToolMode.RECT, ToolMode.ELLIPSE))
        self._view._update_drag_mode()

    def _set_shape_tool(self, mode: ToolMode, label: str):
        self._shape_btn.setText(label)
        self._set_tool(mode)

    def _on_pen_clicked(self):
        self._set_tool(ToolMode.PEN)
        self._show_pen_popup()

    def _on_hl_clicked(self):
        self._set_tool(ToolMode.HIGHLIGHTER)
        self._show_pen_popup()

    def _show_pen_popup(self):
        if self._pen_popup is None:
            self._pen_popup = PenOptionsPopup(self)
        pos = self._color_indicator.mapToGlobal(self._color_indicator.rect().bottomLeft())
        self._pen_popup.show_at(pos)

    def _set_color(self, c: QColor):
        self._pen_color = c
        self._update_color_indicator()

    def _update_color_indicator(self):
        self._color_indicator.setStyleSheet(
            f"background-color:{self._pen_color.name()};border:1px solid #888;border-radius:4px;"
        )

    def _undo(self):
        """Undo the most recent stroke among all visible pages."""
        visible = self._visible_pages()
        if not visible:
            # fallback
            if self._annotations.undo(self._current_page):
                self._render_page_pixmap(self._current_page)
            return

        # Find which visible page has the most recent stroke
        # (we just pick the one with strokes and undo from it)
        # Try pages in reverse order so bottom page is checked first if equal
        for pg in reversed(visible):
            strokes = self._annotations.strokes(pg)
            if strokes:
                self._annotations.undo(pg)
                self._render_page_pixmap(pg)
                return

    def _visible_pages(self) -> list[int]:
        """Return list of page indices currently visible in the viewport."""
        if self._doc is None or not self._page_y_offsets:
            return []
        vp = self._view.viewport()
        top_scene = self._view.mapToScene(0, 0).y()
        bot_scene = self._view.mapToScene(0, vp.height()).y()
        result = []
        for i in range(len(self._doc)):
            page_top = self._page_y_offsets[i]
            page = self._doc[i]
            page_height = page.rect.height * self._render_scale
            page_bot = page_top + page_height
            if page_bot >= top_scene and page_top <= bot_scene:
                result.append(i)
        return result

    # ================================================================
    # File operations
    # ================================================================
    def _open_file(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Open PDF", "", "PDF Files (*.pdf);;All Files (*)"
        )
        if path:
            self._load_document(path)

    def _load_document(self, path: str):
        self._doc = fitz.open(path)
        self._doc_path = path
        self._current_page = 0
        self._zoom_pct = 100
        self._annotations.clear()
        self._zoom_combo.setCurrentText("100%")
        self._page_spin.setMaximum(len(self._doc))
        self._page_spin.setValue(1)
        self._page_label.setText(f" / {len(self._doc)}")
        self.setWindowTitle(f"PDF Reader — {path.split('/')[-1]}")
        self._populate_outline()
        self._render_all_pages()
        self._update_ui_state()

    def _save_annotations(self):
        if self._doc is None:
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Save PDF", self._doc_path or "", "PDF Files (*.pdf)"
        )
        if not path:
            return
        self._burn_annotations_to_doc()
        self._doc.save(path, deflate=True, garbage=3)

    def _burn_annotations_to_doc(self):
        for page_idx, strokes in self._annotations._pages.items():
            page = self._doc[page_idx]
            for s in strokes:
                color = (s.color.redF(), s.color.greenF(), s.color.blueF())
                opacity = 0.35 if s.tool == ToolMode.HIGHLIGHTER else 1.0
                if s.tool in (ToolMode.PEN, ToolMode.HIGHLIGHTER):
                    if len(s.points) < 2:
                        continue
                    pts = [(p.x(), p.y()) for p in s.points]
                    ann = page.add_ink_annot([pts])
                    ann.set_border(width=s.width)
                    ann.set_colors(stroke=color)
                    ann.set_opacity(opacity)
                    ann.update()
                elif s.tool == ToolMode.LINE and s.start and s.end:
                    ann = page.add_line_annot(
                        fitz.Point(s.start.x(), s.start.y()),
                        fitz.Point(s.end.x(), s.end.y()),
                    )
                    ann.set_border(width=s.width)
                    ann.set_colors(stroke=color)
                    ann.set_opacity(opacity)
                    ann.update()
                elif s.tool == ToolMode.RECT and s.start and s.end:
                    r = fitz.Rect(s.start.x(), s.start.y(), s.end.x(), s.end.y())
                    r.normalize()
                    ann = page.add_rect_annot(r)
                    ann.set_border(width=s.width)
                    ann.set_colors(stroke=color)
                    ann.set_opacity(opacity)
                    ann.update()
                elif s.tool == ToolMode.ELLIPSE and s.start and s.end:
                    r = fitz.Rect(s.start.x(), s.start.y(), s.end.x(), s.end.y())
                    r.normalize()
                    ann = page.add_circle_annot(r)
                    ann.set_border(width=s.width)
                    ann.set_colors(stroke=color)
                    ann.set_opacity(opacity)
                    ann.update()

    # ================================================================
    # Outline
    # ================================================================
    def _populate_outline(self):
        self._outline_tree.clear()
        if self._doc is None:
            return
        toc = self._doc.get_toc()
        if not toc:
            self._outline_dock.hide()
            return
        self._outline_dock.show()
        stack: list[QTreeWidgetItem] = []
        for level, title, pn in toc:
            item = QTreeWidgetItem([title])
            item.setData(0, Qt.ItemDataRole.UserRole, pn - 1)
            while len(stack) >= level:
                stack.pop()
            if stack:
                stack[-1].addChild(item)
            else:
                self._outline_tree.addTopLevelItem(item)
            stack.append(item)
        self._outline_tree.expandAll()

    def _on_outline_click(self, item, _):
        p = item.data(0, Qt.ItemDataRole.UserRole)
        if p is not None:
            self._scroll_to_page(p)

    # ================================================================
    # Rendering – continuous scroll
    # ================================================================
    def _render_all_pages(self):
        """Render every page and lay them out vertically in the scene."""
        if self._doc is None:
            return
        self._scene.clear()
        self._page_items.clear()
        self._page_y_offsets.clear()

        scale = self._zoom_pct / 100.0 * 2
        self._render_scale = scale
        y = 0.0

        for i in range(len(self._doc)):
            self._page_y_offsets.append(y)
            page = self._doc[i]
            mat = fitz.Matrix(scale, scale)
            pix = page.get_pixmap(matrix=mat, alpha=False)
            img = QImage(pix.samples, pix.width, pix.height, pix.stride, QImage.Format.Format_RGB888)
            qpix = QPixmap.fromImage(img)
            self._paint_annotations_on(qpix, i, scale)
            item = self._scene.addPixmap(qpix)
            item.setPos(0, y)
            self._page_items.append(item)
            y += pix.height + PAGE_GAP

        self._scene.setSceneRect(self._scene.itemsBoundingRect())
        self._view.resetTransform()
        self._view.scale(0.5, 0.5)

    def _render_page_pixmap(self, page_idx: int):
        """Re-render a single page pixmap (e.g. after annotation change)."""
        if self._doc is None or page_idx >= len(self._page_items):
            return
        scale = self._render_scale
        page = self._doc[page_idx]
        mat = fitz.Matrix(scale, scale)
        pix = page.get_pixmap(matrix=mat, alpha=False)
        img = QImage(pix.samples, pix.width, pix.height, pix.stride, QImage.Format.Format_RGB888)
        qpix = QPixmap.fromImage(img)
        self._paint_annotations_on(qpix, page_idx, scale)
        self._page_items[page_idx].setPixmap(qpix)

    def _paint_annotations_on(self, qpix: QPixmap, page_idx: int, scale: float):
        painter = QPainter(qpix)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        strokes = list(self._annotations.strokes(page_idx))
        if self._current_stroke is not None and self._current_stroke.page_idx == page_idx:
            strokes.append(self._current_stroke)
        for s in strokes:
            pen = QPen(s.color, s.width * scale)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            if s.tool == ToolMode.HIGHLIGHTER:
                c = QColor(s.color)
                c.setAlpha(90)
                pen.setColor(c)
                pen.setWidth(int(s.width * scale * 4))
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            if s.tool in (ToolMode.PEN, ToolMode.HIGHLIGHTER) and len(s.points) >= 2:
                path = QPainterPath()
                path.moveTo(s.points[0].x() * scale, s.points[0].y() * scale)
                for p in s.points[1:]:
                    path.lineTo(p.x() * scale, p.y() * scale)
                painter.drawPath(path)
            elif s.tool == ToolMode.LINE and s.start and s.end:
                painter.drawLine(
                    QPointF(s.start.x() * scale, s.start.y() * scale),
                    QPointF(s.end.x() * scale, s.end.y() * scale),
                )
            elif s.tool == ToolMode.RECT and s.start and s.end:
                painter.drawRect(QRectF(
                    QPointF(s.start.x() * scale, s.start.y() * scale),
                    QPointF(s.end.x() * scale, s.end.y() * scale),
                ).normalized())
            elif s.tool == ToolMode.ELLIPSE and s.start and s.end:
                painter.drawEllipse(QRectF(
                    QPointF(s.start.x() * scale, s.start.y() * scale),
                    QPointF(s.end.x() * scale, s.end.y() * scale),
                ).normalized())
        painter.end()

    # ================================================================
    # Scroll tracking – update current page from scroll position
    # ================================================================
    def _on_scroll(self):
        if not self._page_y_offsets or self._doc is None:
            return
        # Get the scene Y coordinate at the center of the viewport
        vp_center = self._view.mapToScene(
            self._view.viewport().width() // 2,
            self._view.viewport().height() // 3,  # upper third
        )
        center_y = vp_center.y()
        page = 0
        for i, off in enumerate(self._page_y_offsets):
            if center_y >= off:
                page = i
            else:
                break
        if page != self._current_page:
            self._current_page = page
            self._page_spin.blockSignals(True)
            self._page_spin.setValue(page + 1)
            self._page_spin.blockSignals(False)
            self._update_ui_state()

    def _scroll_to_page(self, page_idx: int):
        """Scroll the view so that the given page is visible at the top."""
        if self._doc is None or page_idx < 0 or page_idx >= len(self._doc):
            return
        if page_idx < len(self._page_y_offsets):
            y = self._page_y_offsets[page_idx]
            self._view.centerOn(0, y)
            self._current_page = page_idx
            self._page_spin.blockSignals(True)
            self._page_spin.setValue(page_idx + 1)
            self._page_spin.blockSignals(False)
            self._update_ui_state()

    # ================================================================
    # Navigation
    # ================================================================
    def _go_to_page(self, page: int):
        if self._doc is None:
            return
        page = max(0, min(page, len(self._doc) - 1))
        self._scroll_to_page(page)

    def _prev_page(self):
        self._go_to_page(self._current_page - 1)

    def _next_page(self):
        self._go_to_page(self._current_page + 1)

    # ================================================================
    # Zoom
    # ================================================================
    def _set_zoom(self, pct: int):
        pct = max(10, min(pct, 500))
        self._zoom_pct = pct
        self._zoom_combo.blockSignals(True)
        self._zoom_combo.setCurrentText(f"{pct}%")
        self._zoom_combo.blockSignals(False)
        self._render_all_pages()
        # Restore scroll position to current page
        self._scroll_to_page(self._current_page)

    def _zoom_in(self):
        for z in self.ZOOM_LEVELS:
            if z > self._zoom_pct:
                self._set_zoom(z)
                return
        self._set_zoom(self.ZOOM_LEVELS[-1])

    def _zoom_out(self):
        for z in reversed(self.ZOOM_LEVELS):
            if z < self._zoom_pct:
                self._set_zoom(z)
                return
        self._set_zoom(self.ZOOM_LEVELS[0])

    def _on_zoom_combo(self, text: str):
        t = text.replace("%", "").strip()
        try:
            self._set_zoom(int(t))
        except ValueError:
            pass

    def _fit_width(self):
        if self._doc is None:
            return
        vw = self._view.viewport().width()
        pct = int(vw / self._doc[self._current_page].rect.width * 100) - 2
        self._set_zoom(pct)

    def _fit_page(self):
        if self._doc is None:
            return
        p = self._doc[self._current_page]
        vw = self._view.viewport().width()
        vh = self._view.viewport().height()
        pct = int(min(vw / p.rect.width, vh / p.rect.height) * 100) - 2
        self._set_zoom(pct)

    # ================================================================
    # UI state
    # ================================================================
    def _update_ui_state(self):
        has = self._doc is not None
        self._btn_prev.setEnabled(has and self._current_page > 0)
        self._btn_next.setEnabled(has and self._current_page < (len(self._doc) - 1 if has else 0))
        self._page_spin.setEnabled(has)
        self._zoom_combo.setEnabled(has)
        self._act_save.setEnabled(has)

    # ================================================================
    # Auto-hide
    # ================================================================
    def _check_mouse_position(self):
        if self._doc is None:
            return
        inside = self.frameGeometry().contains(self.cursor().pos())
        if not inside and not self._hidden:
            self._hidden = True
            self.setWindowOpacity(0.0)
        elif inside and self._hidden:
            self._hidden = False
            self.setWindowOpacity(1.0)

    # ================================================================
    # Keyboard
    # ================================================================
    def keyPressEvent(self, event):
        if self._doc is None:
            return super().keyPressEvent(event)
        k = event.key()
        if k in (Qt.Key.Key_Right, Qt.Key.Key_PageDown):
            self._next_page()
        elif k in (Qt.Key.Key_Left, Qt.Key.Key_PageUp):
            self._prev_page()
        elif k == Qt.Key.Key_Home:
            self._go_to_page(0)
        elif k == Qt.Key.Key_End:
            self._go_to_page(len(self._doc) - 1)
        else:
            super().keyPressEvent(event)

    # ================================================================
    # Drag & drop
    # ================================================================
    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event):
        for url in event.mimeData().urls():
            p = url.toLocalFile()
            if p.lower().endswith(".pdf"):
                self._load_document(p)
                break


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def main():
    app = QApplication(sys.argv)
    app.setApplicationName("PDF Reader")
    win = PdfReaderWindow()
    if len(sys.argv) > 1:
        win._load_document(sys.argv[1])
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
