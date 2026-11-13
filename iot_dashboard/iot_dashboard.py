"""Dashboard Reflex: cập nhật websocket mỗi 5 giây, lịch sử theo giờ/ngày."""
import asyncio
from datetime import timedelta
from typing import Any

import reflex as rx

import common as config
from dashboard_data import dashboard_payload, history_csv
from storage import Store
from runtime_settings import THRESHOLD_FIELDS, load_settings, save_settings
import dashboard_auth as auth

BG = "#090f1c"
PANEL = "#111c2e"
MUTED = "#9aaec8"
HISTORY_RESOLUTIONS = {"5 phút": "minute", "Theo giờ": "hour", "Theo ngày": "day"}


class DashboardState(rx.State):
    authenticated: bool = False
    login_error: str = ""
    password_error: str = ""
    password_success: str = ""
    _auth_version: str = ""
    _auth_expires: float = 0.0
    temp: str = "—"
    hum: str = "—"
    level: str = "ĐANG KẾT NỐI"
    connection: str = "Đang kết nối dịch vụ dữ liệu"
    updated: str = "Chưa có"
    note: str = ""
    red: str = "Chưa đủ dữ liệu đánh giá 12 giờ tới"
    red_expected: bool = False
    email: str = ""
    provenance: str = ""
    error: str = ""
    temp_range: str = "—"
    hum_range: str = "—"
    range_hours: str = "12"
    temp_red_low: float = config.TEMP_RED_LOW
    temp_red_high: float = config.TEMP_RED_HIGH
    hum_red_low: float = config.HUM_RED_LOW
    hum_red_high: float = config.HUM_RED_HIGH
    draft_temp_red_low: str = str(config.TEMP_RED_LOW)
    draft_temp_yellow_low: str = str(config.TEMP_YELLOW_LOW)
    draft_temp_yellow_high: str = str(config.TEMP_YELLOW_HIGH)
    draft_temp_red_high: str = str(config.TEMP_RED_HIGH)
    draft_hum_red_low: str = str(config.HUM_RED_LOW)
    draft_hum_yellow_low: str = str(config.HUM_YELLOW_LOW)
    draft_hum_yellow_high: str = str(config.HUM_YELLOW_HIGH)
    draft_hum_red_high: str = str(config.HUM_RED_HIGH)
    draft_email_enabled: bool = False
    draft_email_to: str = ""
    settings_dirty: bool = False
    draft_revision: int = 0
    settings_success: str = ""
    settings_error: str = ""
    sender_ready: bool = False
    hours: str = "12"
    start: str = ""
    end: str = ""
    resolution: str = "Theo giờ"
    forecast: list[dict[str, Any]] = []
    history: list[dict[str, Any]] = []
    _poll_generation: int = 0

    def _authorized(self):
        if auth.session_valid(Store(), self._auth_version, self._auth_expires):
            return True
        clear_session(self)
        return False

    @rx.event
    def login(self, form_data: dict):
        try:
            session = auth.login(Store(), form_data.get("password", ""), self.router.session.client_ip or "local")
        except ValueError as exc:
            self.login_error = str(exc)
            return
        self._auth_version, self._auth_expires = session["version"], session["expires"]
        self.login_error = ""
        self.authenticated = True

    @rx.event
    def logout(self):
        clear_session(self)

    @rx.event
    def change_login_password(self, form_data: dict):
        if not self._authorized():
            return
        try:
            session = auth.change_password(Store(), form_data.get("current_password", ""),
                                           form_data.get("new_password", ""), form_data.get("confirm_password", ""))
        except ValueError as exc:
            self.password_error = str(exc)
            self.password_success = ""
            return
        self._auth_version, self._auth_expires = session["version"], session["expires"]
        self.password_error = ""
        self.password_success = "Đã đổi mật khẩu."

    @rx.event
    def edit_threshold(self, field: str, value: str):
        if not self._authorized():
            return
        if field in THRESHOLD_FIELDS:
            setattr(self, "draft_" + field, value)
            self.settings_dirty = True
            self.settings_success = self.settings_error = ""

    @rx.event
    def edit_email_to(self, value: str):
        if not self._authorized():
            return
        self.draft_email_to = value
        self.settings_dirty = True
        self.settings_success = self.settings_error = ""

    @rx.event
    def edit_email_enabled(self, value: bool):
        if not self._authorized():
            return
        self.draft_email_enabled = value
        self.settings_dirty = True
        self.settings_success = self.settings_error = ""

    @rx.event
    def save_configuration(self):
        if not self._authorized():
            return
        values = {"thresholds": {field: getattr(self, "draft_" + field) for field in THRESHOLD_FIELDS},
                  "email_enabled": self.draft_email_enabled, "email_to": self.draft_email_to}
        try:
            saved = save_settings(Store(), values, self.draft_revision)
        except ValueError as exc:
            self.settings_error = str(exc)
            self.settings_success = ""
            return
        self.settings_dirty = False
        apply_settings(self, saved)
        self.email = "Đã bật · chờ cập nhật trạng thái gửi" if saved["email_enabled"] else "Đã tắt"
        self.settings_error = ""
        self.settings_success = "Đã lưu. Monitor áp dụng từ lần đo tiếp theo."

    @rx.event
    def reload_configuration(self):
        if not self._authorized():
            return
        self.settings_dirty = False
        apply_settings(self, load_settings(Store()))
        self.settings_error = self.settings_success = ""

    @rx.event
    def choose_hours(self, value: str):
        if not self._authorized():
            return
        self.hours = value

    @rx.event
    def choose_start(self, value: str):
        if not self._authorized():
            return
        self.start = value

    @rx.event
    def choose_end(self, value: str):
        if not self._authorized():
            return
        self.end = value

    @rx.event
    def choose_resolution(self, value: str):
        if not self._authorized():
            return
        self.resolution = value

    @rx.event
    def download(self):
        if not self._authorized():
            return
        try:
            data = dashboard_payload(Store(), self.hours, self.start, self.end,
                                     HISTORY_RESOLUTIONS[self.resolution])
        except (ValueError, KeyError) as exc:
            self.error = f"Không xuất được lịch sử: {exc}"
            return
        self.history = data["history"]
        self.error = ""
        return rx.download(data=history_csv(self.history), filename="lich_su_moi_truong.csv")

    @rx.event
    def stop_polling(self):
        self._poll_generation += 1

    @rx.event(background=True)
    async def poll(self):
        async with self:
            if not self._authorized():
                return
            self._poll_generation += 1
            generation = self._poll_generation
            if not self.start:
                self.start = (config.now_local() - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M")
        while True:
            async with self:
                if generation != self._poll_generation or not self._authorized():
                    break
                if self.router.session.client_token not in app.event_namespace.token_to_sid:
                    break
                args = (self.hours, self.start, self.end,
                        HISTORY_RESOLUTIONS[self.resolution])
            try:
                def read():
                    return dashboard_payload(Store(), *args)
                data = await asyncio.to_thread(read)
                async with self:
                    # Một lần đọc đã bắt đầu trước lúc đăng xuất không được gửi dữ liệu ra sau đó.
                    if generation != self._poll_generation or not self._authorized():
                        break
                    self.temp, self.hum, self.level = data["temp"], data["hum"], data["state"]
                    for key in ("connection", "updated", "note", "red", "red_expected", "email",
                                "error", "temp_range", "hum_range", "range_hours", "forecast", "history", "provenance", "sender_ready"):
                        setattr(self, key, data[key])
                    apply_settings(self, data["settings"])
            except Exception as exc:
                async with self:
                    if self._authorized():
                        self.error = f"Không tải được dữ liệu: {exc}"
            await asyncio.sleep(config.DASHBOARD_REFRESH_SEC)


def clear_session(state):
    state._poll_generation += 1
    state._auth_version = ""
    state._auth_expires = 0.0
    state.authenticated = False
    state.history = []
    state.forecast = []
    state.temp = state.hum = state.temp_range = state.hum_range = "—"
    state.updated = "Chưa có"
    state.draft_email_to = ""
    state.draft_email_enabled = False
    state.settings_dirty = False
    state.settings_success = state.settings_error = ""
    state.password_error = state.password_success = ""
    state.error = state.email = state.note = state.provenance = ""
    state.red = state.connection = ""
    state.red_expected = state.sender_ready = False
    state.draft_revision = 0
    for field in THRESHOLD_FIELDS:
        value = float(getattr(config, field.upper()))
        setattr(state, "draft_" + field, str(value))
        if field in ("temp_red_low", "temp_red_high", "hum_red_low", "hum_red_high"):
            setattr(state, field, value)


def apply_settings(state, settings):
    for field in ("temp_red_low", "temp_red_high", "hum_red_low", "hum_red_high"):
        setattr(state, field, settings["thresholds"][field])
    # Không ghi đè ô người dùng đang nhập khi tác vụ realtime làm mới dữ liệu.
    if not state.settings_dirty:
        for field in THRESHOLD_FIELDS:
            setattr(state, "draft_" + field, str(settings["thresholds"][field]))
        state.draft_email_to = settings["email_to"]
        state.draft_email_enabled = settings["email_enabled"]
        state.draft_revision = settings["revision"]


def stat_card(label, value, unit, icon, color, subtitle=None):
    return rx.box(
        rx.hstack(rx.text(label, color=MUTED, size="2"), rx.spacer(), rx.icon(icon, size=19, color=color)),
        rx.hstack(rx.heading(value, size="8", color="white"), rx.text(unit, color=color, size="4"), align="baseline", margin_top="12px"),
        *([subtitle] if subtitle is not None else []),
        padding="24px", background=PANEL, border="1px solid #26354b", border_radius="16px", width="100%")


def chart(title, data, metric, forecast=False):
    color = "#fbbf24" if metric == "temp" else "#38bdf8"
    low = DashboardState.temp_red_low if metric == "temp" else DashboardState.hum_red_low
    high = DashboardState.temp_red_high if metric == "temp" else DashboardState.hum_red_high
    axis = ({"domain": [0, 100], "allow_data_overflow": True, "ticks": [0, 20, 40, 60, 80, 100]}
            if metric == "hum" else {"domain": ["auto", "auto"]})
    lines = [rx.recharts.line(data_key=metric, name="Dự báo" if forecast else "Trung bình", stroke=color,
                             dot=False, stroke_width=3, is_animation_active=False, connect_nulls=False)]
    if forecast:
        lines += [rx.recharts.line(data_key=f"{metric}_{key}", name=label, stroke="#7186a3", dot=False,
                                   stroke_dasharray="4 4", is_animation_active=False)
                  for key, label in (("low", "Dải thấp tham khảo"), ("high", "Dải cao tham khảo"))]
    else:
        lines += [rx.recharts.line(data_key=f"{metric}_{key}", name=label, stroke=shade, dot=False,
                                   stroke_width=1, is_animation_active=False, connect_nulls=False)
                  for key, label, shade in (("min", "Thấp nhất", "#64748b"), ("max", "Cao nhất", "#a78bfa"))]
    return rx.box(
        rx.heading(title, size="4", margin_bottom="20px"),
        rx.recharts.line_chart(
            rx.recharts.cartesian_grid(stroke="#243247", stroke_dasharray="3 3", vertical=False),
            rx.recharts.x_axis(data_key="label", stroke=MUTED, min_tick_gap=65, tick={"fontSize": 11}),
            rx.recharts.y_axis(stroke=MUTED, width=44, tick={"fontSize": 11}, **axis),
            rx.recharts.graphing_tooltip(), rx.recharts.legend(),
            rx.recharts.reference_line(custom_attrs={"y": low}, stroke="#ef4444", stroke_dasharray="6 4"),
            rx.recharts.reference_line(custom_attrs={"y": high}, stroke="#ef4444", stroke_dasharray="6 4"),
            *lines, data=data, width="100%", height=290,
        ),
        padding="24px", background=PANEL, border="1px solid #26354b", border_radius="16px", width="100%")


def control(label, component):
    return rx.vstack(rx.text(label, size="2", color=MUTED), component, spacing="2", align="start")


def threshold_panel(title, prefix, unit, icon, color):
    fields = []
    for suffix, label in (("red_low", "Đỏ thấp"), ("yellow_low", "Vàng thấp"),
                          ("yellow_high", "Vàng cao"), ("red_high", "Đỏ cao")):
        name = f"{prefix}_{suffix}"
        fields.append(control(f"{label} ({unit})", rx.input(
            id=f"setting-{name}", aria_label=f"{title} · {label}", type="number", step="0.1",
            min=-40 if prefix == "temp" else 0, max=80 if prefix == "temp" else 100,
            value=getattr(DashboardState, "draft_" + name),
            on_change=lambda value, field=name: DashboardState.edit_threshold(field, value), width="100%")))
    return rx.box(
        rx.hstack(rx.icon(icon, color=color, size=22), rx.heading(title, size="4"), margin_bottom="20px"),
        rx.grid(*fields, columns=rx.breakpoints(initial="2", md="4"), spacing="4", width="100%"),
        padding="24px", background=PANEL, border="1px solid #26354b", border_radius="16px", width="100%")


def settings_panel():
    return rx.vstack(
        rx.heading("Cài đặt cảnh báo", size="5"),
        threshold_panel("Nhiệt độ", "temp", "°C", "thermometer", "#fbbf24"),
        threshold_panel("Độ ẩm", "hum", "%", "droplets", "#38bdf8"),
        rx.box(
            rx.flex(
                rx.hstack(rx.icon("mail", size=22, color="#a78bfa"), rx.heading("Cảnh báo email", size="4")),
                rx.spacer(), rx.switch(id="setting-email-enabled", aria_label="Bật cảnh báo email",
                                      checked=DashboardState.draft_email_enabled, on_change=DashboardState.edit_email_enabled),
                align="center", width="100%", margin_bottom="20px"),
            control("Email nhận cảnh báo", rx.input(id="setting-email-to", aria_label="Email nhận cảnh báo",
                    type="email", placeholder="ban@example.com", value=DashboardState.draft_email_to,
                    on_change=DashboardState.edit_email_to, width="100%")),
            rx.hstack(rx.badge(DashboardState.email, color_scheme="gray"),
                      rx.cond(~DashboardState.sender_ready, rx.badge("Chưa có tài khoản gửi", color_scheme="amber")),
                      margin_top="12px", wrap="wrap"),
            padding="24px", background=PANEL, border="1px solid #26354b", border_radius="16px", width="100%"),
        rx.cond(DashboardState.settings_error != "", rx.callout(DashboardState.settings_error, icon="triangle-alert", color_scheme="red", width="100%")),
        rx.cond(DashboardState.settings_success != "", rx.callout(DashboardState.settings_success, icon="check", color_scheme="green", width="100%")),
        rx.flex(rx.button(rx.icon("save", size=17), "Lưu cài đặt", on_click=DashboardState.save_configuration),
                rx.button("Tải lại", variant="soft", on_click=DashboardState.reload_configuration), gap="12px", wrap="wrap"),
        password_panel(),
        spacing="5", align="start", width="100%", padding_top="24px")


def password_panel():
    return rx.box(
        rx.hstack(rx.icon("lock-keyhole", size=22, color="#34d399"), rx.heading("Đổi mật khẩu", size="4"), margin_bottom="20px"),
        rx.form(
            rx.grid(
                control("Mật khẩu hiện tại", rx.input(id="password-current", name="current_password", type="password",
                        aria_label="Mật khẩu hiện tại", required=True, max_length=128, custom_attrs={"autoComplete": "current-password"}, width="100%")),
                control("Mật khẩu mới", rx.input(id="password-new", name="new_password", type="password",
                        aria_label="Mật khẩu mới", required=True, min_length=6, max_length=128, custom_attrs={"autoComplete": "new-password"}, width="100%")),
                control("Xác nhận mật khẩu mới", rx.input(id="password-confirm", name="confirm_password", type="password",
                        aria_label="Xác nhận mật khẩu mới", required=True, min_length=6, max_length=128, custom_attrs={"autoComplete": "new-password"}, width="100%")),
                columns=rx.breakpoints(initial="1", md="3"), spacing="4", width="100%"),
            rx.cond(DashboardState.password_error != "", rx.text(DashboardState.password_error, color="#fca5a5", size="2", margin_top="12px")),
            rx.cond(DashboardState.password_success != "", rx.text(DashboardState.password_success, color="#34d399", size="2", margin_top="12px")),
            rx.button("Đổi mật khẩu", type="submit", variant="soft", margin_top="20px"),
            on_submit=DashboardState.change_login_password, reset_on_submit=True, width="100%"),
        padding="24px", background=PANEL, border="1px solid #26354b", border_radius="16px", width="100%")


def login_page():
    return rx.center(
        rx.box(
            rx.hstack(rx.icon("activity", size=28, color="#34d399"), rx.heading("Môi trường · IoT", size="6"), spacing="3", margin_bottom="30px"),
            rx.box(rx.icon("lock-keyhole", size=25, color="#34d399"), padding="12px", background="#143032", border_radius="12px", width="fit-content", margin_bottom="20px"),
            rx.heading("Đăng nhập", size="6", margin_bottom="8px"),
            rx.text("Nhập mật khẩu để mở dashboard", color=MUTED, size="2", margin_bottom="24px"),
            rx.form(
                control("Mật khẩu", rx.input(id="login-password", name="password", aria_label="Mật khẩu",
                        type="password", placeholder="Nhập mật khẩu", custom_attrs={"autoComplete": "current-password"},
                        required=True, max_length=128, auto_focus=True, width="100%", size="3")),
                rx.cond(DashboardState.login_error != "", rx.text(DashboardState.login_error, color="#fca5a5", size="2", margin_top="12px")),
                rx.button("Đăng nhập", type="submit", size="3", width="100%", margin_top="24px"),
                on_submit=DashboardState.login, reset_on_submit=True, width="100%"),
            padding=rx.breakpoints(initial="24px", sm="36px"), background=PANEL, border="1px solid #26354b",
            border_radius="20px", width="100%", max_width="440px"),
        min_height="100vh", background=BG, color="#e5edf9", padding="20px")


def dashboard_view():
    return rx.box(
        rx.vstack(
            rx.flex(
                rx.hstack(rx.icon("activity", color="#34d399", size=30),
                          rx.vstack(rx.heading("Môi trường · IoT", size="7"),
                                    rx.text("Raspberry Pi / DHT22 / Dự báo 12 giờ", color=MUTED, size="2"), spacing="1")),
                rx.spacer(),
                rx.vstack(rx.badge(DashboardState.connection, color_scheme="teal", size="2"),
                          rx.text("Cập nhật: ", DashboardState.updated, color=MUTED, size="1"), align="end"),
                rx.button(rx.icon("log-out", size=16), "Đăng xuất", on_click=DashboardState.logout, variant="soft"),
                width="100%", gap="16px", wrap="wrap", align="center"),
            rx.cond(DashboardState.error != "", rx.callout(DashboardState.error, icon="triangle-alert", color_scheme="amber", width="100%")),
            rx.grid(
                stat_card("NHIỆT ĐỘ HIỆN TẠI", DashboardState.temp, "°C", "thermometer", "#fbbf24"),
                stat_card("ĐỘ ẨM HIỆN TẠI", DashboardState.hum, "%", "droplets", "#38bdf8"),
                stat_card("NHIỆT ĐỘ DỰ BÁO", DashboardState.temp_range, "", "chart-line", "#34d399",
                          rx.text("Thấp nhất – cao nhất trong ", DashboardState.range_hours, " giờ tới", color=MUTED, size="1", margin_top="8px")),
                stat_card("ĐỘ ẨM DỰ BÁO", DashboardState.hum_range, "", "waves", "#a78bfa",
                          rx.text("Thấp nhất – cao nhất trong ", DashboardState.range_hours, " giờ tới", color=MUTED, size="1", margin_top="8px")),
                columns=rx.breakpoints(initial="1", sm="2", lg="4"), spacing="4", width="100%"),
            rx.flex(
                rx.hstack(rx.text("Hiện tại", color=MUTED),
                          rx.badge(DashboardState.level, color_scheme=rx.cond(DashboardState.level == "DO", "red", rx.cond(DashboardState.level == "VANG", "amber", rx.cond(DashboardState.level == "XANH", "green", "gray"))))),
                rx.text(DashboardState.red, color=rx.cond(DashboardState.red_expected, "#fca5a5", MUTED), size="2"),
                justify="between", gap="12px", wrap="wrap", width="100%", padding="18px", background=PANEL, border_radius="12px"),
            rx.tabs.root(
                rx.tabs.list(rx.tabs.trigger("Dự báo", value="forecast"), rx.tabs.trigger("Lịch sử", value="history"),
                             rx.tabs.trigger(rx.icon("settings", size=15), "Cài đặt", value="settings")),
                rx.tabs.content(
                    rx.vstack(
                        rx.flex(
                            rx.heading("Diễn biến sắp tới", size="5"),
                            rx.spacer(), control("Số giờ dự báo", rx.select([str(i) for i in range(1, 13)], value=DashboardState.hours, on_change=DashboardState.choose_hours)),
                            width="100%", align="center", wrap="wrap", gap="16px"),
                        rx.cond(DashboardState.forecast.length() == 0, rx.text("Chưa có đường dự báo mới. Kiểm tra cảm biến và dịch vụ monitor.", color=MUTED)),
                        rx.grid(chart("Nhiệt độ dự báo · °C", DashboardState.forecast, "temp", True),
                                chart("Độ ẩm dự báo · %", DashboardState.forecast, "hum", True),
                                columns=rx.breakpoints(initial="1", lg="2"), spacing="4", width="100%"),
                        spacing="5", width="100%", padding_top="24px"), value="forecast"),
                rx.tabs.content(
                    rx.vstack(
                        rx.flex(
                            control("Từ (giờ Việt Nam)", rx.input(type="datetime-local", value=DashboardState.start, on_change=DashboardState.choose_start)),
                            control("Đến (để trống = hiện tại)", rx.input(type="datetime-local", value=DashboardState.end, on_change=DashboardState.choose_end)),
                            control("Gộp dữ liệu", rx.select(["5 phút", "Theo giờ", "Theo ngày"], value=DashboardState.resolution, on_change=DashboardState.choose_resolution)),
                            rx.button(rx.icon("download", size=16), "Tải CSV", on_click=DashboardState.download, variant="soft"),
                            gap="16px", wrap="wrap", align="end", width="100%"),
                        rx.cond(DashboardState.history.length() == 0, rx.text("Không có dữ liệu trong khoảng đã chọn. Bạn có thể chọn lại ngày để xem lịch sử đã nhập.", color=MUTED)),
                        rx.grid(chart("Lịch sử nhiệt độ · °C", DashboardState.history, "temp"),
                                chart("Lịch sử độ ẩm · %", DashboardState.history, "hum"),
                                columns=rx.breakpoints(initial="1", lg="2"), spacing="4", width="100%"),
                        spacing="5", width="100%", padding_top="24px"), value="history"),
                rx.tabs.content(settings_panel(), value="settings"),
                default_value="forecast", width="100%"),
            spacing="6", width="100%", max_width="1480px", margin="auto", padding=rx.breakpoints(initial="20px", md="36px")),
        background=BG, min_height="100vh", color="#e5edf9", on_mount=DashboardState.poll)


def index():
    return rx.cond(DashboardState.authenticated, dashboard_view(), login_page())


app = rx.App()
app.add_page(index, title="IoT · Giám sát & dự báo 12 giờ", description="Nhiệt độ và độ ẩm theo thời gian thực trên Raspberry Pi")
