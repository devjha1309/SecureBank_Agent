from apps.banking_api.app import app
from apps.banking_api.chat import router
from core.config.settings import get_settings

app.include_router(router)


if get_settings().mount_ui:
    from apps.gradio_ui.app import mount_ui

    app = mount_ui(app)
