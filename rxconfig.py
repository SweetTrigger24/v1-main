import reflex as rx

config = rx.Config(app_name="iot_dashboard", telemetry_enabled=False,
                   plugins=[rx.plugins.RadixThemesPlugin(theme=rx.theme(appearance="dark", accent_color="teal", radius="large"))],
                   disable_plugins=[rx.plugins.SitemapPlugin])
